"""OpenAI Decisions API 섀도 실험: 가상 검수 세트(v1·v2)마다 모델 답을 sim_check r4 판정 옆에 기록만 한다(판정은 바꾸지 않는다).

세트마다 요청 하나(기준 문서 PNG + 포스터 PNG, base64, detail original)에 네 종류 문항을 묶는다.
  (a) 기준 문서 확인 predicate: 문서 OCR 값이 기준 문서에 그대로 적혀 있는가(항목별)
  (b) 줄 고르기 choice: 포스터 OCR 줄 + '해당 줄 없음' 중 항목을 나타내는 줄. 기준 값은 문항에 넣지 않는다.
  (c) 직접 판정 predicate(비교용): 포스터에 기준 값이 그대로 표기되어 있는가
  (d) 판독 가능 정도 score(4단계)
기본은 dry-run: 요청 본문을 파일로 쓰고 문항 수와 예상 입력 토큰·비용만 센다. 네트워크를 쓰지 않는다.
실제 호출은 --call을 주고 터미널 환경변수 OPENAI_API_KEY가 있을 때만 한다(.env는 읽지 않는다, 자동 재시도 없음).
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.decisions_shadow                      # dry-run, 보기는 OCR 줄 순서
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.decisions_shadow --order shuffled     # 보기 순서를 바꾼 두 번째 실행
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.decisions_shadow --call --dataset v2 --set S08
채점은 decisions_shadow_score.py.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import time

from PIL import Image

from . import sim_check as sc
from .evaluate import key

ROOT = sc.ROOT
DATASETS = {"v1": (ROOT / "data/synthetic/poster-check-v1", ROOT / "data/runtime/poster-ocr/sim"),
            "v2": (ROOT / "data/synthetic/poster-check-v2", ROOT / "data/runtime/poster-ocr/sim-v2")}
SYNTHETIC = ROOT / "data/synthetic"
OUT = ROOT / "data/runtime/poster-ocr/decisions-shadow"
MODEL = "gpt-6-luna"
API_BASE = "https://api.openai.com/v1"  # OPENAI_BASE_URL 같은 환경변수가 있어도 다른 곳으로 보내지 않도록 고정
PRICE_PER_M_INPUT = 0.10  # 달러/입력 100만 토큰(2026-10-06 공개 베타, 출력 요금 없음)
ORDER_SEED = 20261021
NONE = "none"  # 줄 고르기의 '해당 줄 없음'
FIELDS = ["title", "edition", "schedule", "time", "venue", "host", "organizer", "contact", "slogan"]
DOC_FIELDS = ["title", "schedule", "time", "venue", "host", "organizer", "contact", "slogan"]  # (a) 행사명 칸은 회차까지 한 번에 본다
HINT = {"title": "행사명(축제 이름)", "edition": "회차(제○회)", "schedule": "행사 기간(날짜)", "time": "운영 시간(시각)", "venue": "행사 장소",
        "host": "주최 기관", "organizer": "주관 기관", "contact": "문의 전화번호", "slogan": "필수 문구(슬로건)"}
LEVELS = [{"label": "판독 불가", "description": "이 항목 표기가 없거나 글자를 알아볼 수 없다"},
          {"label": "일부 판독", "description": "일부 글자만 확실하게 읽힌다"},
          {"label": "대체로 판독", "description": "대부분 읽히지만 한두 글자가 확실하지 않다"},
          {"label": "완전 판독", "description": "모든 글자가 또렷하게 읽힌다"}]
INTRO = ("이미지 1은 축제 포스터 디자인 용역 계약서의 '표기 사항' 표(기준 문서)이고, 이미지 2는 그 계약으로 만든 포스터다. 둘 다 실험용 가상 자료다. "
         "각 문항은 이미지에 보이는 글자만 근거로 답한다. 맞춤법이 틀려 보이거나 흔하지 않은 낱말이어도 고쳐 읽지 말고 보이는 글자 그대로 판단한다.")
SAME_RULE = "띄어쓰기·줄바꿈 차이는 무시하고, 글자나 숫자가 하나라도 다르면 거짓이다."
FORMAT_RULE = ("띄어쓰기·줄바꿈, 라벨 유무, 날짜·시각 표기 형식(예: '2026. 1. 2.(금)'와 '1월 2일(금)', '9:00'과 '09:00') 차이는 같은 것으로 본다. "  # 예시 날짜는 세트에 없는 날
               "글자나 숫자가 하나라도 다르면 거짓이다.")
# 예상 토큰(추정): 이미지는 32px 패치당 1토큰(GPT-5.4 이후 detail original 방식 가정, gpt-6-luna 규칙은 공개 전),
# 글자는 한글 1음절 1토큰·그 밖 3글자 1토큰, 문항·보기·등급마다 구조 토큰을 더한다. tiktoken은 인코딩 파일을 내려받으므로 쓰지 않는다.
PATCH, PATCH_CAP = 32, 10_000
QUESTION_OVERHEAD, OPTION_OVERHEAD, IMAGE_OVERHEAD = 12, 4, 8


# ------------------------------------------------------------------ 입력 준비
def ocr_inputs(dataset, set_id):
    """sim_check r4와 같은 방식으로 기준 문서 조건과 포스터 OCR 줄을 만든다."""
    _, runtime = DATASETS[dataset]
    runs = runtime / "runs/paddleocr"
    doc = sc.load(runs / f"{set_id}-doc" / f"{sc.VARIANT}.json")
    poster = sc.load(runs / f"{set_id}-poster" / f"{sc.VARIANT}.json")
    conds = sc.parse_document(doc)
    rows = [row for row in sc.rows_of(poster["records"]) if key(row["text"])]  # check_set과 같은 줄
    return conds, rows


def image_input(path):
    """PNG → base64 데이터 URL 입력 조각과 기록용 정보(경로·sha256·크기)."""
    path = Path(path)
    data = path.read_bytes()
    with Image.open(path) as im:
        size = list(im.size)
    rel = path.resolve().relative_to(ROOT).as_posix() if path.resolve().is_relative_to(ROOT) else path.name
    part = {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"), "detail": "original"}
    return part, {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "size": size}


def line_ids(dataset, set_id, n, seed=ORDER_SEED):
    """보기 값(L###): 위치를 드러내지 않는 번호를 세트마다 고정한다(두 실행이 같은 번호를 쓴다)."""
    return [f"L{x}" for x in random.Random(f"{seed}:{dataset}:{set_id}:ids").sample(range(100, 1000), n)]


def compact_cond(cond):
    return {k: v for k, v in cond.items() if k not in ("source", "source_box")}


def doc_raw_value(cond):
    """(a)에 쓰는 문서 OCR 원문: 행사명은 회차를 떼기 전 칸 전체."""
    if cond["key"] == "title" and cond.get("source"):
        return " ".join(r["text"] for r in cond["source"]).strip()
    return cond.get("expected")


def build_request(dataset, set_id, conds, rows, doc_png, poster_png, order="ocr", seed=ORDER_SEED):
    """요청 본문(body)과 채점용 기록(meta). body에는 기관·사용자 정보를 넣지 않는다."""
    if order not in ("ocr", "shuffled"):
        raise ValueError(order)
    by_key = {c["key"]: c for c in conds if c["kind"] == "info" and c.get("expected")}
    items = [f for f in FIELDS if f in by_key]
    ids = line_ids(dataset, set_id, len(rows), seed)
    options = [{"value": ids[i], "index": i, "text": row["text"], "conf": round(row["conf"], 4),
                "records": [{"id": r["id"], "text": r["text"], "confidence": r["confidence"], "bbox_xyxy": r["bbox_xyxy"]} for r in row["records"]]}
               for i, row in enumerate(rows)]
    questions, qmeta, choice_order = [], [], {}
    for f in DOC_FIELDS:
        if f in by_key:
            value = doc_raw_value(by_key[f])
            questions.append({"type": "predicate", "name": f"a_doc_{f}",
                              "instructions": f"기준 문서(이미지 1) 표의 '{sc.CONDITION_LABEL[f]}' 칸에 다음 값이 그대로 적혀 있다: '{value}'. {SAME_RULE}"})
            qmeta.append({"name": f"a_doc_{f}", "probe": "a", "item": f, "value": value})
    for f in items:
        name = f"b_line_{f}"
        listed = list(options)
        if order == "shuffled":
            random.Random(f"{seed}:{dataset}:{set_id}:{f}").shuffle(listed)
        choices = [{"value": o["value"], "description": o["text"]} for o in listed]
        choices.append({"value": NONE, "description": "해당 줄 없음(이 항목을 나타내는 줄이 보기에 없다)"})  # 두 실행 모두 맨 끝
        choice_order[name] = [c["value"] for c in choices]
        questions.append({"type": "choice", "name": name, "choices": choices,
                          "instructions": (f"포스터(이미지 2)에서 {HINT[f]} 항목을 나타내는 줄을 고른다. 보기는 포스터를 OCR로 읽은 줄이라 글자가 틀려 있을 수 있으니 "
                                           "포스터에서 그 줄이 무엇을 나타내는지를 보고 고른다. '장소'·'주최' 같은 라벨이 있든 없든 상관없다. "
                                           "여러 줄에 나뉘어 있으면 가장 많은 부분을 담은 줄을 고르고, 그런 줄이 없으면 '해당 줄 없음'을 고른다.")})
        qmeta.append({"name": name, "probe": "b", "item": f})
    for f in items:
        value = by_key[f]["expected"]
        questions.append({"type": "predicate", "name": f"c_direct_{f}",
                          "instructions": f"포스터(이미지 2)에 다음 {sc.CONDITION_LABEL[f]} 표기가 그대로 나와 있다: '{value}'. {FORMAT_RULE}"})
        qmeta.append({"name": f"c_direct_{f}", "probe": "c", "item": f, "value": value})
    for f in items:
        questions.append({"type": "score", "name": f"d_legible_{f}", "levels": LEVELS,
                          "instructions": f"포스터(이미지 2)에서 {HINT[f]} 표기의 글자를 판독할 수 있는 정도를 고른다(낮은 등급부터 높은 등급 순)."})
        qmeta.append({"name": f"d_legible_{f}", "probe": "d", "item": f})
    doc_part, doc_info = image_input(doc_png)
    poster_part, poster_info = image_input(poster_png)
    body = {"model": MODEL,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": INTRO},
                                                   {"type": "input_text", "text": "이미지 1 — 기준 문서"}, doc_part,
                                                   {"type": "input_text", "text": "이미지 2 — 포스터"}, poster_part]}],
            "questions": questions}
    meta = {"dataset": dataset, "set": set_id, "order": order, "seed": seed, "model": MODEL, "rules": sc.RULES,
            "images": {"document": doc_info, "poster": poster_info},
            "items": {f: {"cond": compact_cond(by_key[f]), "doc_raw": doc_raw_value(by_key[f])} for f in by_key},
            "rows": options, "questions": qmeta, "choice_order": choice_order}
    return body, meta


def prepare(dataset, set_id, order="ocr", seed=ORDER_SEED):
    sim, _ = DATASETS[dataset]
    conds, rows = ocr_inputs(dataset, set_id)
    doc_png, poster_png = sim / "documents" / f"{set_id}.png", sim / "posters" / f"{set_id}.png"
    for p in (doc_png, poster_png):  # 가상 세트 이미지만 보낸다
        if not p.resolve().is_relative_to(SYNTHETIC.resolve()):
            raise SystemExit(f"가상 세트 밖 이미지는 보내지 않습니다: {p}")
    return build_request(dataset, set_id, conds, rows, doc_png, poster_png, order, seed)


# ------------------------------------------------------------------ 추정
def image_tokens(width, height):
    n = math.ceil(width / PATCH) * math.ceil(height / PATCH)
    if n > PATCH_CAP:  # 상한을 넘으면 비율을 지켜 줄인다고 가정
        s = math.sqrt(PATCH_CAP * PATCH * PATCH / (width * height))
        n = math.ceil(width * s / PATCH) * math.ceil(height * s / PATCH)
    return n


def text_tokens(text):
    hangul = sum("가" <= ch <= "힣" for ch in text)
    other = sum(not ch.isspace() for ch in text) - hangul
    return math.ceil(hangul + other / 3)


def estimate(body, meta):
    content = body["input"][0]["content"]
    text = sum(text_tokens(p["text"]) for p in content if p["type"] == "input_text")
    options = 0
    for q in body["questions"]:
        text += QUESTION_OVERHEAD + text_tokens(q["name"]) + text_tokens(q["instructions"])
        for o in q.get("choices", []) + q.get("levels", []):
            options += 1
            text += OPTION_OVERHEAD + text_tokens(" ".join(str(v) for v in o.values()))
    images = sum(image_tokens(*info["size"]) + IMAGE_OVERHEAD for info in meta["images"].values())
    by_probe = {p: sum(q["probe"] == p for q in meta["questions"]) for p in "abcd"}
    total = images + text
    return {"questions": len(body["questions"]), "by_probe": by_probe, "options": options, "image_tokens": images, "text_tokens": text,
            "input_tokens": total, "cost_usd": total / 1e6 * PRICE_PER_M_INPUT}


def redacted(body):
    """파일 기록용: 이미지 base64는 길이와 해시만 남긴다(--full-body면 그대로)."""
    out = json.loads(json.dumps(body))
    for p in out["input"][0]["content"]:
        if p["type"] == "input_image":
            raw = base64.b64decode(p["image_url"].split(",", 1)[1])
            p["image_url"] = f"data:image/png;base64,<생략: {len(raw):,} bytes, sha256 {hashlib.sha256(raw).hexdigest()[:16]}…>"
    return out


def response_path(out_dir, dataset, set_id, order):
    return Path(out_dir) / dataset / (f"{set_id}.json" if order == "ocr" else f"{set_id}.{order}.json")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def dry_run(jobs, out_dir, order, full_body=False):
    rows = []
    for dataset, set_id, body, meta in jobs:
        est = estimate(body, meta)
        write_json(Path(out_dir) / "requests" / dataset / f"{set_id}.{order}.json",
                   {"note": "dry-run 요청 본문(호출하지 않음)", "estimate": est, "meta": meta, "body": body if full_body else redacted(body)})
        rows.append({"dataset": dataset, "set": set_id, **est})
    summary = {"order": order, "model": MODEL, "price_per_m_input_usd": PRICE_PER_M_INPUT, "token_rule": "추정(이미지 32px 패치당 1토큰, 한글 1음절 1토큰)",
               "datasets": {}, "sets": rows}
    for dataset in sorted({r["dataset"] for r in rows}):
        rs = [r for r in rows if r["dataset"] == dataset]
        summary["datasets"][dataset] = {"requests": len(rs), "questions": sum(r["questions"] for r in rs),
                                        "by_probe": {p: sum(r["by_probe"][p] for r in rs) for p in "abcd"},
                                        "image_tokens": sum(r["image_tokens"] for r in rs), "text_tokens": sum(r["text_tokens"] for r in rs),
                                        "input_tokens": sum(r["input_tokens"] for r in rs), "cost_usd": sum(r["cost_usd"] for r in rs)}
    total = {k: sum(d[k] for d in summary["datasets"].values()) for k in ("requests", "questions", "image_tokens", "text_tokens", "input_tokens", "cost_usd")}
    summary["total"] = total
    write_json(Path(out_dir) / f"dry-run.{order}.json", summary)
    print(f"dry-run ({order}) — 호출하지 않음. 요청 본문: {Path(out_dir) / 'requests'}")
    print("데이터셋  요청  문항(a/b/c/d)            이미지 토큰  텍스트 토큰  입력 토큰  예상 비용")
    for dataset, d in summary["datasets"].items():
        bp = d["by_probe"]
        print(f"{dataset:8s} {d['requests']:4d}  {d['questions']:5d} ({bp['a']}/{bp['b']}/{bp['c']}/{bp['d']})"
              f"  {d['image_tokens']:11,d}  {d['text_tokens']:11,d}  {d['input_tokens']:9,d}  ${d['cost_usd']:.4f}")
    print(f"합계     {total['requests']:4d}  {total['questions']:5d}{'':20s}{total['image_tokens']:11,d}  {total['text_tokens']:11,d}  "
          f"{total['input_tokens']:9,d}  ${total['cost_usd']:.4f}")
    return summary


# ------------------------------------------------------------------ 실제 호출(--call)
def to_plain(resp):
    if hasattr(resp, "model_dump"):
        return resp.model_dump(mode="json")
    if hasattr(resp, "to_dict"):
        return resp.to_dict()
    return json.loads(json.dumps(resp, default=str))


def make_client(api_key, timeout):
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("openai 패키지가 없습니다: .\\.venv\\Scripts\\python.exe -m pip install -r requirements-decisions.txt")
    client = OpenAI(api_key=api_key, base_url=API_BASE, max_retries=0, timeout=timeout)  # 자동 재시도 끔
    if not hasattr(client, "decisions"):
        raise SystemExit("이 openai 버전에는 decisions가 없습니다(openai>=3.26.0 필요).")
    return client


def call(jobs, out_dir, order, force=False, keep_going=False, timeout=120.0, client_factory=make_client):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()  # 터미널에서 직접 설정한 값만 쓴다(.env 안 읽음, 출력·기록 안 함)
    if not api_key:
        raise SystemExit("OPENAI_API_KEY가 설정되지 않았습니다. 터미널에서 직접 설정한 뒤 --call로 다시 실행하세요(호출하지 않음).")
    todo = [j for j in jobs if force or not response_path(out_dir, j[0], j[1], order).exists()]
    est = [estimate(body, meta) for _, _, body, meta in todo]
    print(f"호출 {len(todo)}건(이미 받은 {len(jobs) - len(todo)}건 건너뜀) · 모델 {MODEL} · {API_BASE} · "
          f"예상 입력 {sum(e['input_tokens'] for e in est):,} 토큰 · 약 ${sum(e['cost_usd'] for e in est):.4f}")
    if not todo:
        return []
    client = client_factory(api_key, timeout)
    saved = []
    for (dataset, set_id, body, meta), e in zip(todo, est):
        started = time.perf_counter()
        called_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            resp = client.decisions.create(**body)
        except Exception as exc:  # 재시도하지 않고 기록만 한다
            message = str(exc).replace(api_key, "***")[:2000]
            write_json(Path(out_dir) / dataset / f"{set_id}.{order}.error.json",
                       {"meta": meta, "called_at": called_at, "error": {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
                                                                         "message": message}})
            print(f"{dataset} {set_id}: 실패 {type(exc).__name__} {getattr(exc, 'status_code', '')}")
            if keep_going:
                continue
            break
        raw = to_plain(resp)
        record = {"meta": meta, "request_estimate": e, "called_at": called_at, "api_base": API_BASE,
                  "request_id": getattr(resp, "_request_id", None), "elapsed_seconds": round(time.perf_counter() - started, 2),
                  "model": raw.get("model"), "usage": raw.get("usage"), "response": raw}
        path = response_path(out_dir, dataset, set_id, order)
        write_json(path, record)
        saved.append(path)
        print(f"{dataset} {set_id}: 저장 {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} · 답 {len(raw.get('answers') or [])}개")
    return saved


# ------------------------------------------------------------------ CLI
def jobs_for(datasets, set_ids, order, seed):
    jobs = []
    for dataset in datasets:
        sim, _ = DATASETS[dataset]
        ids = [s["id"] for s in sc.load(sim / "sets.json")["sets"]]
        for set_id in ids:
            if set_ids and set_id not in set_ids:
                continue
            body, meta = prepare(dataset, set_id, order, seed)
            jobs.append((dataset, set_id, body, meta))
    return jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값. 요청 본문만 쓰고 호출하지 않는다")
    mode.add_argument("--call", action="store_true", help="실제 호출(터미널 환경변수 OPENAI_API_KEY 필요)")
    parser.add_argument("--dataset", nargs="+", choices=sorted(DATASETS), default=sorted(DATASETS))
    parser.add_argument("--set", nargs="+", help="일부 세트만(예: S08 S13)")
    parser.add_argument("--order", choices=["ocr", "shuffled"], default="ocr", help="choice 보기 순서: OCR 줄 순서 또는 섞은 순서(순서 민감도 측정용)")
    parser.add_argument("--seed", type=int, default=ORDER_SEED)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--full-body", action="store_true", help="dry-run 파일에 이미지 base64까지 그대로 쓴다")
    parser.add_argument("--force", action="store_true", help="이미 받은 응답도 다시 호출")
    parser.add_argument("--keep-going", action="store_true", help="호출 실패 뒤에도 다음 세트로 넘어간다(기본은 멈춤)")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    jobs = jobs_for(args.dataset, set(args.set or []), args.order, args.seed)
    if args.call:
        return call(jobs, args.out, args.order, args.force, args.keep_going, args.timeout)
    return dry_run(jobs, args.out, args.order, args.full_body)


if __name__ == "__main__":
    main()
