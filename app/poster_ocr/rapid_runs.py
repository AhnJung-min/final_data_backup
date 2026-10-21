"""제품 OCR(app/ocr.py: RapidOCR · PP-OCRv5 한국어 ONNX)을 서비스와 같은 방식으로 포스터마다 돌려 기준선을 만든다.

서비스 경로(app/inspector.py read_ocr → app/ocr.py _read_rapid)와 같게 한다.
- 원본 파일 바이트를 전처리 없이 그대로 엔진에 넣는다(확대·회색조·알파 합성 없음). 엔진은 _rapid_engine 그대로다
  (text_score 0, 방향 분류 끔, PP-OCRv5 검출 + 한국어 PP-OCRv5 인식, ONNX CPU, 스레드 min(4, CPU 수)).
- 재현성을 위해 LATTE_OCR_DEVICE=cpu로 고정하고, 장치·모델 파일 sha256·rapidocr/onnxruntime 버전·엔진 설정을 기록한다.
- 서비스와 다른 점은 검사 허용 폴더(inspector.ALLOWED_ROOTS) 확인 하나다. 포스터가 data/img-v2에 있기 때문이다.
  형식·용량·픽셀 제한과 EXIF 회전 거부는 같은 문구로 본다.
기록은 ocr_worker.py와 같은 형식(variant=product, scale=1, 원본 픽셀 좌표)이다. 서비스가 검사 규칙에 넘기는
병합 줄(normalize_lines → merge_same_line_fragments, 정규화 XYWH)은 service_lines로 함께 둔다.
모델은 내려받지 않는다. 없으면 알리고 멈춘다(준비: python scripts/prepare_ocr.py). 전처리 이미지 파일은 만들지 않는다.

단계: ocr(실행, runs/rapidocr/<사례>/product.json) → extract(규칙 v2, pred-rapidocr/rapidocr/<사례>/product.poster.json)
→ compare(evaluate.py 지표를 paddleocr rgb_2x 기준선 옆에 둔다, eval/rapidocr-product.json).
실행: .\\.venv\\Scripts\\python.exe -m app.poster_ocr.rapid_runs [--splits dev test reserve] [--steps ocr extract compare]
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import sys
import time

from .ocr_worker import memory as worker_memory, sha256_file, to_records

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
RUNS = ROOT / "data/runtime/poster-ocr/runs"
EVAL = ROOT / "data/runtime/poster-ocr/eval"
ENGINE, VARIANT, PIPELINE = "rapidocr", "product", "rapidocr/product"
PREFIX = f"{ENGINE[:4]}_{VARIANT}"  # ocr_worker와 같은 ID 규칙(rapi_product_000)
STEPS = ("ocr", "extract", "compare")
REFERENCE = "paddleocr/rgb_2x"
OTHER_REFERENCES = ("paddleocr/rgb_1x", "easyocr/rgb_2x", "hybrid/rgb_2x")
HEADLINE_FIELDS = ("core_all", "title", "title_any", "schedule_pair", "schedule_start", "schedule_end", "venue", "venue_partial",
                   "year", "edition", "start_year", "start_weekday", "admin_district")


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def memory():
    """ocr_worker.memory()와 같은 모양. psutil이 없으면 Windows API로 현재·최대 작업 집합을 읽는다."""
    info = worker_memory()
    if info["rss_bytes"] is None and os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), *[(n, ctypes.c_size_t) for n in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
            counters = Counters(cb=ctypes.sizeof(Counters))
            psapi, kernel = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return {"rss_bytes": counters.WorkingSetSize, "peak_bytes": counters.PeakWorkingSetSize}
        except Exception:  # 진단용 값이라 실패해도 실행을 멈추지 않는다
            pass
    return info


# ------------------------------------------------------------- 제품 엔진
def require_models():
    """로컬 모델 확인만 한다(내려받기 없음). 없거나 해시가 다르면 이유를 알리고 멈춘다."""
    from app.ocr_models import model_directory, model_manifest, model_paths, models_ready
    if not models_ready():
        missing = [m["filename"] for m in model_manifest().values() if not (model_directory() / m["filename"]).is_file()]
        raise SystemExit(f"제품 OCR 모델이 없습니다({model_directory()}): {', '.join(missing)}. "
                         "이 도구는 내려받지 않습니다. 필요하면 python scripts/prepare_ocr.py로 준비한 뒤 다시 실행하세요.")
    try:
        return model_paths(verify=True)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


def engine_config(engine):
    """재현용 엔진 설정(절대 경로는 파일 이름만 남긴다)."""
    cfg = getattr(engine, "cfg", None)
    if cfg is None:
        return {}
    from omegaconf import OmegaConf
    cfg = OmegaConf.to_container(cfg, resolve=True, enum_to_str=True)
    out = {"Global": {k: v for k, v in cfg["Global"].items() if k not in ("model_root_dir", "font_path")}}
    for stage in ("Det", "Cls", "Rec"):
        out[stage] = {k: (Path(v).name if k.endswith("_path") and v else v) for k, v in cfg[stage].items()
                      if k not in ("engine_cfg", "model_root_dir")}
    ort = cfg["EngineConfig"]["onnxruntime"]
    out["onnxruntime"] = {k: ort.get(k) for k in ("intra_op_num_threads", "inter_op_num_threads", "enable_cpu_mem_arena", "use_cuda", "use_dml")}
    return out


def providers(engine):
    out = {}
    for stage in ("det", "cls", "rec"):
        try:
            out[stage] = list(getattr(engine, f"text_{stage}").session.session.get_providers())
        except AttributeError:
            out[stage] = None
    return out


def fingerprint(identity):
    """같은 원본이라도 모델·버전·설정이 바뀌면 다시 읽도록 실행 결과에 남기는 엔진 지문."""
    keys = ("rapidocr_version", "onnxruntime_version", "device", "models", "config")
    return sha256_bytes(json.dumps({k: identity.get(k) for k in keys}, ensure_ascii=False, sort_keys=True).encode("utf-8"))[:16]


def product_engine():
    """서비스와 같은 RapidOCR 엔진을 CPU로 만든다. 반환: (엔진, 신원 기록, 로드 초)."""
    os.environ["LATTE_OCR_BACKEND"] = "rapidocr"
    os.environ["LATTE_OCR_DEVICE"] = "cpu"  # 재현성: GPU가 있어도 서비스의 CPU 경로로 고정
    from app import ocr
    from app.ocr_device import select_device
    from app.ocr_models import model_directory, model_manifest
    paths = require_models()
    selection = select_device()
    if selection.device != "cpu":
        raise SystemExit(f"CPU 고정에 실패했습니다: 선택된 장치 {selection.device}")
    started = time.perf_counter()
    engine = ocr._rapid_engine(str(model_directory()))  # _read_rapid가 CPU에서 부르는 것과 같은 호출(같은 캐시 키)
    load_seconds = time.perf_counter() - started
    pinned, hashes = model_manifest(), {k: sha256_file(p) for k, p in paths.items()}
    identity = {"engine": ENGINE, "rapidocr_version": importlib.metadata.version("rapidocr"),
                "onnxruntime_version": importlib.metadata.version("onnxruntime"),
                "method": ocr.method_name("cpu"), "engine_label": ocr.engine_label("cpu"),
                "device_requested": selection.requested, "device_selected": selection.device, "device": "cpu",
                "fallback_reason": selection.reason, "providers": providers(engine),
                "models": {paths[k].name: hashes[k] for k in paths},
                "models_pinned_match": all(hashes[k] == pinned[k]["sha256"] for k in paths),
                "model_dir": os.path.relpath(model_directory(), ROOT).replace("\\", "/"),
                "config": engine_config(engine)}
    identity["fingerprint"] = fingerprint(identity)
    return engine, identity, load_seconds


# ------------------------------------------------------------- 사례 하나
def service_checks(path, content):
    """inspector.validate_image_path의 형식·용량·픽셀 검사와 _read_rapid의 EXIF 거부(허용 폴더 검사만 뺀다). 반환: (너비, 높이)."""
    from PIL import Image
    from app.inspector import MAX_BYTES, MAX_PIXELS
    if Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("PNG/JPEG 이미지 파일만 검사할 수 있습니다.")
    if len(content) > MAX_BYTES:
        raise ValueError("이미지 용량 제한(20MB)을 초과했습니다.")
    with Image.open(BytesIO(content)) as image:
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("파일 내용이 PNG/JPEG 형식이 아닙니다.")
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("이미지 픽셀 제한(20MP)을 초과했습니다.")
        image.verify()
    with Image.open(BytesIO(content)) as image:
        if image.getexif().get(274, 1) != 1:
            raise ValueError("EXIF 회전·반전 정보가 있는 이미지입니다. 방향을 적용해 PNG로 다시 저장한 뒤 제출하세요.")
        return image.size


def ocr_case(engine, case, path, content, engine_fingerprint=None):
    """원본 바이트 → ocr_worker 형식 기록. 서비스가 쓰는 병합 줄도 service_lines로 남긴다."""
    from app import ocr
    payload = {"case_id": case["id"], "engine": ENGINE, "variant": VARIANT, "scale": 1, "normalized_dimensions": None,
               "input_dimensions": None, "source_sha256": case["sha256"], "input_sha256": sha256_bytes(content),
               "coordinate_space": "normalized_original_pixels", "input": "original_bytes", "device": "cpu",
               "method": ocr.METHODS["rapidocr"], "engine_fingerprint": engine_fingerprint}
    started = None
    try:
        width, height = service_checks(path, content)
        payload.update(normalized_dimensions=[width, height], input_dimensions=[width, height])
        with ocr._INFERENCE_LOCK, redirect_stdout(sys.stderr):  # _read_rapid와 같은 잠금·출력 분리
            started = time.perf_counter()
            result = engine(content)
            seconds = time.perf_counter() - started
        boxes, texts, scores = result.boxes, result.txts, result.scores
        raw = [] if boxes is None else [{"text": str(t), "confidence": float(s), "polygon": [[float(x), float(y)] for x, y in box]}
                                        for box, t, s in zip(boxes, texts, scores)]
        stages = list(getattr(result, "elapse_list", None) or [None, None, None])
        payload.update(status="success", records=to_records(raw, 1, width, height, PREFIX), raw_detections=len(raw),
                       stage_seconds={k: (round(v, 3) if isinstance(v, float) else None) for k, v in zip(("det", "cls", "rec"), stages)})
        try:  # 서비스는 여기서 실패하면 검사 전체를 실패로 낸다. OCR 기록은 그대로 두고 따로 표시한다.
            lines = ocr.merge_same_line_fragments(ocr.normalize_lines(boxes, texts, scores, width, height), height / width)
            payload.update(service_status="succeeded", service_lines=[
                {"text": l["text"], "confidence": l["confidence"], "bbox": [round(v, 6) for v in l["bbox"]]} for l in lines])
        except ValueError as error:
            payload.update(service_status="failed", service_error=str(error), service_lines=[])
    except Exception as error:  # 실패는 기록하고 다음 사례로 넘어간다
        seconds = time.perf_counter() - started if started else 0.0
        payload.update(status="failed", records=[], error=f"{type(error).__name__}: {error}")
    payload["inference_seconds"] = round(seconds, 3)
    return payload


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path, payload, indent=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.tmp")  # 대시보드가 쓰는 도중의 파일을 읽지 않도록 다 쓴 뒤 바꿔 넣는다
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(partial, path)


def select_cases(manifest, splits, only=None, limit=None):
    cases = [c for c in load_json(manifest)["cases"] if c["split"] in splits]
    if only:
        cases = [c for c in cases if c["id"] in set(only)]
    return cases[:limit] if limit else cases


def run_cases(engine, identity, cases, out, root=ROOT, load_seconds=None, splits=None, log=print):
    """사례마다 원본 해시를 확인하고, 같은 원본·같은 엔진 지문으로 이미 성공한 사례는 건너뛴다."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    run_info = {"engine": ENGINE, "variant": VARIANT, "identity": identity, "load_seconds": load_seconds, "variants": [VARIANT],
                "splits": splits, "cases": len(cases), "python": sys.version.split()[0], "platform": platform.platform(),
                "processor": platform.processor(), "cpu_count": os.cpu_count(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "memory_after_load": memory(),
                "input": "원본 파일 바이트를 그대로 엔진에 넣음(확대·회색조·알파 합성 없음, scale 1)",
                "service_path": "app.inspector.read_ocr → app.ocr._read_rapid → app.ocr._rapid_engine (CPU)",
                "deviations": ["검사 허용 폴더(inspector.ALLOWED_ROOTS) 확인만 건너뜀: 포스터가 data/img-v2에 있다.",
                               "기록(records)은 엔진 원 검출(다각형 포함)이고, 서비스가 규칙에 넘기는 병합 줄은 service_lines에 따로 둔다."]}
    write_json(out / "run.json", run_info, indent=2)
    with (out / "runs.jsonl").open("a", encoding="utf-8") as stream:  # 실행마다 덮어쓰지 않는 기록
        stream.write(json.dumps({"event": "start", **run_info}, ensure_ascii=False) + "\n")
    done = failed = skipped = service_failed = 0
    seconds = []
    stamp = identity.get("fingerprint")
    for case in cases:
        target = out / case["id"] / f"{VARIANT}.json"
        source = Path(root) / case["image"]
        content = source.read_bytes()
        if sha256_bytes(content) != case["sha256"]:
            raise SystemExit(f"{case['id']}: 원본 해시가 매니페스트와 다릅니다.")
        old = load_json(target) if target.exists() else {}
        if old.get("status") == "success" and old.get("source_sha256") == case["sha256"] and old.get("engine_fingerprint") == stamp:
            skipped += 1
            seconds.append(old["inference_seconds"])  # 시간 요약은 이번에 고른 사례 전체로 낸다
            continue  # 같은 원본을 같은 엔진으로 이미 읽었다
        payload = ocr_case(engine, case, source, content, stamp)
        payload["memory"] = memory()
        write_json(target, payload)
        if payload["status"] == "success":
            done += 1
            seconds.append(payload["inference_seconds"])
            service_failed += payload["service_status"] != "succeeded"
        else:
            failed += 1
        log(f"{VARIANT} {case['id']} {payload['status']} {payload['inference_seconds']}s lines={len(payload['records'])}")
    seconds.sort()
    run_info.update(finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), succeeded=done, failed=failed, skipped=skipped,
                    service_failed=service_failed, memory_at_end=memory(),
                    inference_seconds=({"n": len(seconds), "median": seconds[len(seconds) // 2], "mean": round(sum(seconds) / len(seconds), 3),
                                        "p90": seconds[max(0, int(len(seconds) * .9) - 1)], "max": seconds[-1],
                                        "total": round(sum(seconds), 1), "includes_skipped": skipped > 0} if seconds else None))
    write_json(out / f"run.{VARIANT}.json", run_info, indent=2)
    with (out / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "finish", **run_info}, ensure_ascii=False) + "\n")
    return run_info


# ------------------------------------------------------------- 추출·평가
def extract_run(runs_dir, manifest, pred_dir):
    """다른 경로의 pred-final과 같은 규칙 v2 추출기(extract.run_dir)를 쓴다."""
    from .extract import run_dir
    return run_dir(runs_dir, manifest, Path(pred_dir) / ENGINE)


def build_rows(cases, golds, runs_dir, pred_dir):
    """evaluate.evaluate()의 행 만들기와 같은 계산을 variant=product에 적용한다(evaluate는 rgb_2x·rgb_1x만 돈다)."""
    from .evaluate import compare, gold_texts, key, ocr_text, semiglobal
    rows = []
    for c in cases:
        ocr_path, pred_path = Path(runs_dir) / c["id"] / f"{VARIANT}.json", Path(pred_dir) / ENGINE / c["id"] / f"{VARIANT}.poster.json"
        if c["id"] not in golds or not ocr_path.exists() or not pred_path.exists():
            continue
        ocr, gold, pred = load_json(ocr_path), golds[c["id"]], load_json(pred_path)
        fields, lists, presence = compare(gold, pred)
        text = ocr_text(ocr["records"])
        reads = [{"category": cat, "chars": len(key(t)), "accuracy": max(0.0, 1 - semiglobal(key(t), text) / len(key(t)))}
                 for cat, t in gold_texts(gold)]
        rows.append({"case_id": c["id"], "split": c["split"], "fields": fields, "lists": lists,
                     "presence": {k: list(v) for k, v in presence.items()}, "reads": reads,
                     "inference_seconds": ocr.get("inference_seconds"), "lines": len(ocr["records"]),
                     "small_text": gold["quality"]["small_text"]})
    return rows


def headline(summary, results, splits):
    """분할별로 제품 OCR과 기준선(paddleocr rgb_2x)을 나란히 둔 요약: 판독률(문구 범주별)·필드 값 정확도·짝 비교·시간."""
    from .evaluate import paired
    out = {}
    for split in (*splits, "all"):
        a, b = summary.get(PIPELINE, {}).get(split), summary.get(REFERENCE, {}).get(split)
        if not a:
            continue
        b = b or {"n": 0, "fields": {}, "reads": {}, "time": {}}
        relabeled = {k: [dict(r, split="all") for r in v] for k, v in results.items()} if split == "all" else results
        diff = lambda x, y: None if x is None or y is None else round(x - y, 4)
        reads = {}
        for category in [c for c in a["reads"] if c in b["reads"]] or list(a["reads"]):
            x, y = a["reads"][category], b["reads"].get(category) or {}
            reads[category] = {PIPELINE: x, REFERENCE: y or None, "diff": diff(x["mean_char_accuracy"], y.get("mean_char_accuracy"))}
        fields = {}
        for name in HEADLINE_FIELDS:
            x, y = a["fields"].get(name), b["fields"].get(name)
            if not x:
                continue
            item = {PIPELINE: x, REFERENCE: y, "diff": diff(x["rate"], (y or {}).get("rate"))}
            if REFERENCE in results:
                item["mcnemar"] = paired(relabeled, PIPELINE, REFERENCE, split, name)  # only_a=제품만 정답, only_b=기준선만 정답
            fields[name] = item
        fields["title_cer_mean"] = {PIPELINE: a["fields"].get("title_cer_mean"), REFERENCE: b["fields"].get("title_cer_mean"),
                                    "diff": diff(a["fields"].get("title_cer_mean"), b["fields"].get("title_cer_mean"))}
        out[split] = {"n": {PIPELINE: a["n"], REFERENCE: b["n"]}, "reads": reads, "fields": fields,
                      "time": {PIPELINE: a.get("time"), REFERENCE: b.get("time")}}
    return out


def same_rows(left, right):
    """두 경로 결과의 사례별 필드 판정이 같은지(최종 평가 JSON이 현재 정답·예측과 맞는지 확인용)."""
    index = {r["case_id"]: r["fields"] for r in right}
    differ = [r["case_id"] for r in left if index.get(r["case_id"]) != r["fields"]]
    return {"cases": len(left), "missing": sum(r["case_id"] not in index for r in left), "different": differ[:20], "n_different": len(differ)}


def compare_run(manifest, gold_dir, runs_dir, pred_dir, splits, baseline_path=None, baseline_runs=RUNS,
                baseline_pred=ROOT / "data/runtime/poster-ocr/pred-final"):
    from .evaluate import FIELD_RULES, evaluate, summarize
    from .report_experiment import FIELD_LABEL, READ_LABEL
    cases = [c for c in load_json(manifest)["cases"] if c["split"] in splits]
    # 정답 선택(사람 검토본·완료 문서만)은 evaluate()를 그대로 쓴다. 같은 호출로 paddleocr 기준선도 현재 파일에서 다시 계산한다.
    fresh, golds = evaluate(manifest, gold_dir, baseline_runs, baseline_pred, splits, pipelines={"paddleocr": "paddleocr"})
    rows = build_rows(cases, golds, runs_dir, pred_dir)
    baseline = load_json(baseline_path) if baseline_path and Path(baseline_path).exists() else {}
    notes = []
    if baseline.get("rows"):
        references = {k: [r for r in baseline["rows"][k] if r["split"] in splits] for k in (REFERENCE, *OTHER_REFERENCES) if k in baseline["rows"]}
        source = Path(baseline_path).resolve().relative_to(ROOT).as_posix() if Path(baseline_path).resolve().is_relative_to(ROOT) else str(baseline_path)
        check = same_rows(fresh.get(REFERENCE, []), references.get(REFERENCE, [])) if fresh.get(REFERENCE) else None
        if check and check["n_different"] + check["missing"]:
            notes.append(f"최종 평가 JSON의 paddleocr rgb_2x 행이 현재 정답·예측으로 다시 계산한 결과와 {check['n_different']}건 다르고 {check['missing']}건 없다.")
    else:
        references, source, check = {k: v for k, v in fresh.items() if k in (REFERENCE, "paddleocr/rgb_1x")}, "현재 파일에서 다시 계산(final.json 없음)", None
        notes.append("기준선 최종 평가 JSON이 없어 paddleocr를 현재 pred-final에서 다시 계산했다.")
    results = {PIPELINE: rows, **references}
    summary = {}
    for name, items in results.items():
        summary[name] = {split: summarize([r for r in items if r["split"] == split]) for split in splits}
        summary[name]["all"] = summarize(items)
    stored = (baseline.get("summary") or {}).get(REFERENCE, {})
    matches = {split: stored[split]["fields"] == summary[REFERENCE][split]["fields"] and stored[split]["reads"] == summary[REFERENCE][split]["reads"]
               for split in splits if split in stored and REFERENCE in summary}
    run = load_json(Path(runs_dir) / f"run.{VARIANT}.json") or load_json(Path(runs_dir) / "run.json")
    identity = run.get("identity", {})
    ocr_status = {"cases": len(cases), "with_gold": sum(c["id"] in golds for c in cases), "rows": len(rows),
                  "ocr_success": sum(load_json(Path(runs_dir) / c["id"] / f"{VARIANT}.json").get("status") == "success" for c in cases)}
    notes += [
        "제품 OCR은 원본 크기 입력(scale 1)이다. 기준선 paddleocr rgb_2x는 LANCZOS 2배 입력이고 검출 모델도 server급이다.",
        "판독률은 정답 문구 문자 일치율(규칙과 무관), 필드는 같은 규칙 v2 추출기 결과의 완전 일치 정확도다. diff = 제품 − 기준선.",
        "mcnemar.only_a는 제품만 맞힌 포스터 수, only_b는 기준선만 맞힌 수, p_exact는 정확 McNemar 양측 p다.",
        "시간은 엔진 호출만 잰 장당 초다. 기준선 본 실행은 다른 작업과 CPU를 나눠 쓴 상태라 직접 비교하지 않는다.",
    ]
    return {"schema": "poster-ocr.rapidocr-product.v1", "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "pipeline": PIPELINE, "reference": REFERENCE, "splits": list(splits), "notes": notes,
            "inputs": {"manifest": rel(manifest), "gold": rel(gold_dir), "runs": rel(runs_dir), "pred": rel(Path(pred_dir) / ENGINE),
                       "baseline": source, "baseline_sha256": sha256_file(baseline_path) if baseline else None},
            "baseline_check": {"stored_summary_matches_rows": matches, "recomputed_vs_stored_rows": check},
            "engine": {k: identity.get(k) for k in ("rapidocr_version", "onnxruntime_version", "method", "engine_label", "device",
                                                    "providers", "models", "models_pinned_match", "fingerprint")} | {"load_seconds": run.get("load_seconds")},
            "coverage": ocr_status, "labels": {"fields": FIELD_LABEL, "reads": READ_LABEL}, "field_rules": FIELD_RULES,
            "headline": headline(summary, results, splits), "summary": summary, "rows": {PIPELINE: rows}}


def rel(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def pct(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def print_headline(report):
    for split, h in report["headline"].items():
        r = h["reads"].get("all", {})
        line = [f"[{split}] n={h['n'][PIPELINE]}/{h['n'][REFERENCE]} 판독률 {pct(r.get(PIPELINE, {}).get('mean_char_accuracy'))}"
                f" (기준 {pct((r.get(REFERENCE) or {}).get('mean_char_accuracy'))})"]
        for name in ("core_all", "title", "schedule_pair", "venue", "year", "edition"):
            f = h["fields"].get(name)
            if f:
                line.append(f"{name} {pct(f[PIPELINE]['rate'])} (기준 {pct((f[REFERENCE] or {}).get('rate'))})")
        print(" · ".join(line))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--splits", nargs="+", default=["dev", "test", "reserve"])
    parser.add_argument("--out", type=Path, default=RUNS / ENGINE, help="OCR 실행 폴더(<사례>/product.json)")
    parser.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS))
    parser.add_argument("--pred", type=Path, default=ROOT / "data/runtime/poster-ocr/pred-rapidocr", help="추출 문서 루트(<루트>/rapidocr/<사례>/product.poster.json)")
    parser.add_argument("--gold", type=Path, default=ROOT / "data/benchmarks/gold-v2h")
    parser.add_argument("--baseline", type=Path, default=EVAL / "final.json", help="기준선 최종 평가 JSON(evaluate.py 출력)")
    parser.add_argument("--eval-out", type=Path, default=EVAL / "rapidocr-product.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", nargs="*", help="이 사례 ID만 실행")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if "ocr" in args.steps:
        cases = select_cases(args.manifest, args.splits, args.only, args.limit)
        engine, identity, load_seconds = product_engine()
        print(f"제품 OCR {identity['engine_label']} · rapidocr {identity['rapidocr_version']} · 장치 {identity['device']} "
              f"· 모델 해시 일치 {identity['models_pinned_match']} · 로드 {load_seconds:.1f}초 · 사례 {len(cases)}장", flush=True)
        info = run_cases(engine, identity, cases, args.out, load_seconds=load_seconds, splits=args.splits,
                         log=lambda text: print(text, flush=True))
        print(f"OCR 완료: 성공 {info['succeeded']} · 실패 {info['failed']} · 건너뜀 {info['skipped']} · 서비스 정규화 실패 {info['service_failed']}")
    if "extract" in args.steps:
        stats = extract_run(args.out, args.manifest, args.pred)
        print(f"추출: 문서 {stats['documents']} · 검증 통과 {stats['valid']} · 오류 {len(stats['errors'])}")
        for error in stats["errors"][:20]:
            print("  -", error)
    if "compare" in args.steps:
        report = compare_run(args.manifest, args.gold, args.out, args.pred, args.splits, args.baseline)
        args.eval_out.parent.mkdir(parents=True, exist_ok=True)
        args.eval_out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print_headline(report)
        print(f"평가 저장: {rel(args.eval_out)}")


if __name__ == "__main__":
    main()
