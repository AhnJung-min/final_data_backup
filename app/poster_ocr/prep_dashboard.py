"""데이터 준비 대시보드(로컬 전용, 127.0.0.1). 빌더가 만든 결과를 한 화면에서 보고, 사람 검토 화면 두 개(줄 정답·요청 의도)를 띄운다.

실행: .\\.venv\\Scripts\\python.exe -m app.poster_ocr.prep_dashboard [--port 8767]
읽는 자료(없으면 화면에 만드는 명령을 보여 준다. 파일 수정 시각이 같으면 다시 읽지 않는다)
  data/runtime/poster-ocr/lines-v2/<출처>/          줄 정답 초안·검토 큐(line_labels)
  data/runtime/poster-ocr/eval/rapidocr-product.json  엔진별 판독률(rapid_runs, evaluate.py 형식)
  data/runtime/poster-ocr/calib/                    신뢰도 보정 진단(calibration)
  data/runtime/poster-ocr/synth-boxes/<세트>/        가상 포스터 글자 상자(synth_boxes)
  data/runtime/logo-catalog/                        CI 로고 카탈로그(logo_catalog)
  data/collection/evidence/request-*                요청 의도 라벨·분할·기준 모델(request_labels)
쓰는 파일은 검토 기록 두 개뿐이다: lines-v2/<출처>/review.jsonl(line_labels.record), request-label-review.jsonl(request_labels.record).
줄 crop은 요청 때 메모리에서만 만든다(포스터 원본 공공누리 제3유형, 파일로 저장하지 않음). 로고는 logo-catalog의 파생 PNG만 보낸다.
POST는 Host와 Origin이 이 서버(127.0.0.1:<포트>)와 같을 때만 받는다.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

from app import request_labels as rl
from . import line_labels as L

ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).with_name("static") / "prep_dashboard.html"
PY = r".\.venv\Scripts\python.exe -m"
DEFAULT_SOURCE = "paddleocr-rgb_2x"
SHUFFLE_SEED = "prep-dashboard-v1"
PLANTED_MIN_REVIEWS = 20   # 큐를 다 검토했고 이만큼 이상 검토했을 때만 심은 오류 잡은 비율을 보여 준다(중간에 보이면 저장할 때마다 바뀌는 값으로 심은 줄을 알아낼 수 있다)
MAX_BODY = 100_000
PNG_SIG = b"\x89PNG\r\n\x1a\n"
NAME = re.compile(r"[a-z0-9_]+(?:[+-][a-z0-9_]+)*")
LINE_ID = re.compile(r"[A-Za-z0-9_.\-]{1,120}")

COMMANDS = {
    "lines": f"{PY} app.poster_ocr.line_labels build --engine paddleocr --variant rgb_2x",
    "queue": f"{PY} app.poster_ocr.line_labels queue --source {{source}}",
    "eval": f"{PY} app.poster_ocr.rapid_runs --steps extract compare --splits dev test reserve",
    "calib": f"{PY} app.poster_ocr.calibration",
    "synth": f"{PY} app.poster_ocr.synth_boxes build --set v1 v2",
    "logos": f"{PY} app.poster_ocr.logo_catalog build",
    "req_labels": "data/collection/evidence/request-labels.jsonl(요청 의도 DRAFT 라벨)이 필요합니다.",
    "req_split": f"{PY} app.request_labels split",
    "req_baseline": f"{PY} app.request_labels baseline",
    "req_effective": f"{PY} app.request_labels baseline --labels effective",
}
FIELD_LABEL = {"title.official": "축제명(공식)", "title.raw": "축제명 원문(연도·회차 포함)", "edition.raw": "회차", "schedule.raw": "일정",
               "schedule.times": "운영 시간", "venue.items": "장소 항목", "venue.raw": "장소", "address.raw": "주소", "contacts": "연락처",
               "links": "링크", "budget.raw": "예산", "admission.raw": "입장료", "organizations.text": "기관(글자)",
               "organizations.logo": "기관(로고 속 글자)", "slogans": "문구", "programs": "프로그램", "notices": "안내", "badges": "배지"}
SEGMENT_LABEL = {"A_incorrect": "OCR과 다른 줄", "B_edge_missing": "가장자리 확인 줄", "A_correct_sample": "OCR과 같은 줄(표본)"}
PIPELINES = [("rapidocr/product", "RapidOCR 제품(원본 1배)"), ("paddleocr/rgb_2x", "PaddleOCR 2배(기준선)"),
             ("paddleocr/rgb_1x", "PaddleOCR 1배"), ("hybrid/rgb_2x", "EasyOCR+Paddle 재인식 2배"), ("easyocr/rgb_2x", "EasyOCR 2배")]
ENGINE_FIELDS = ("core_all", "title", "schedule_pair", "venue", "year", "edition")
SYNTH_FIELD_LABEL = {"title": "축제명", "edition_year": "연도·회차", "schedule": "일정", "time": "시간", "venue": "장소", "host": "주최",
                     "organizer": "주관", "contact": "문의", "slogan": "문구", "programs": "프로그램", "label": "항목 이름표",
                     "decor_mark": "장식 숫자", "other": "기타", "boilerplate": "문서 고정 문구"}
SYNTH_STATUS = {"match": "일치(IoU≥0.5)", "loose": "여백 큰 한 상자", "split": "나눠 읽음", "merged": "합쳐 읽음", "missed": "못 찾음"}
LOGO_HUMAN_FLAGS = ("sheet_like", "svg_gradient_check", "preview_only", "white_on_transparent", "low_res", "tiny")
LOGO_FLAG_LABEL = {"sheet_like": "낱장·여백 많음", "wide": "가로로 김", "needs_ghostscript": "Ghostscript 필요", "busy_background": "배경 복잡",
                   "low_res": "저해상도", "white_on_transparent": "흰 로고(투명 바탕)", "tiny": "아주 작음", "preview_only": "내장 미리보기만",
                   "huge": "아주 큼", "svg_gradient_check": "SVG 그라디언트 확인", "cmyk_naive": "CMYK 단순 변환", "off_artboard": "아트보드 밖",
                   "ext_mismatch": "확장자 불일치", "empty_render": "빈 렌더", "render_failed": "렌더 실패", "placeholder_ai": "AI 자리표시",
                   "multi_frame": "여러 프레임", "icc_failed": "ICC 실패", "no_provenance": "출처 없음"}
LOGO_TIER_LABEL = {"official_ready": "공식 · 참조 가능", "official_unrendered": "공식 · 렌더 안 됨", "official_blocked": "공식 · 이용 조건 제외",
                   "commons_only": "커먼즈만", "no_files": "파일 없음", "not_collected": "미수집", "council": "의회", "central_agency": "중앙부처·청",
                   "unmatched": "대응 못 함"}
LOGO_KIND_LABEL = {"sido": "광역", "sigungu": "시군구", "foundation": "재단", "public_institution": "공공기관"}
LOGO_STATUS_LABEL = {"ok": "렌더 성공", "needs_ghostscript": "Ghostscript 필요", "empty": "빈 그림", "placeholder": "자리표시",
                     "error": "오류", "timeout": "시간 초과", "crashed": "중단", "bomb": "너무 큼", "unsupported": "지원 안 함",
                     "not_rendered": "렌더 안 함"}
LOGO_FORMAT_LABEL = {"png": "PNG", "jpeg": "JPEG", "ai_pdf": "AI(PDF 호환)", "ai_ps": "AI(PostScript)", "svg": "SVG", "eps": "EPS",
                     "gif": "GIF", "pdf": "PDF", "eps_dosbin": "EPS(미리보기 포함)", "psd": "PSD", "bmp": "BMP", "ps": "PS", "zip": "ZIP"}
REQ_STATE_LABEL = {"approved": "승인", "relabelled": "변경", "flagged": "보류", "pending": "남음"}


def paths(root=ROOT):
    """자료 위치. 테스트는 임시 폴더로 바꿔 끼운다(P를 바꾸면 모든 화면이 그 폴더를 읽는다)."""
    root = Path(root)
    rel = lambda p: root / Path(p).resolve().relative_to(rl.ROOT.resolve())
    return {"lines": root / "data/runtime/poster-ocr/lines-v2", "eval": root / "data/runtime/poster-ocr/eval/rapidocr-product.json",
            "calib": root / "data/runtime/poster-ocr/calib", "synth": root / "data/runtime/poster-ocr/synth-boxes",
            "logos": root / "data/runtime/logo-catalog", "manifest": root / "data/benchmarks/posters-v2.json",
            "req_labels": rel(rl.LABELS), "req_texts": rel(rl.TEXTS), "req_production": rel(rl.PRODUCTION),
            "req_split": rel(rl.SPLIT_OUT), "req_baseline": rel(rl.BASELINE_OUT), "req_log": rel(rl.REVIEW_LOG)}


P = paths()
_CACHE, _CACHE_LOCK = {}, threading.Lock()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stamp(files):
    out = []
    for f in files:
        try:
            st = Path(f).stat()
            out.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(f), None, None))
    return tuple(out)


def cached(name, files, make):
    """files의 수정 시각·크기가 그대로면 이전 결과를 다시 쓴다."""
    key = stamp(files)
    with _CACHE_LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] == key:
            return hit[1]
    value = make()
    with _CACHE_LOCK:
        _CACHE[name] = (key, value)
    return value


def empty(what, command, **fmt):
    return {"empty": True, "what": what, "command": COMMANDS[command].format(**fmt)}


def ratio(a, b):
    return round(a / b, 4) if b else None


# ------------------------------------------------------------------ 포스터 줄 정답
def line_sources():
    out = P["lines"]
    files = sorted(Path(out).glob("*/summary.json")) + sorted(Path(out).glob("*/queue.json"))
    rows = cached("line_sources", files, lambda: L.sources(out) if Path(out).exists() else [])
    order = lambda s: (s["source"] != DEFAULT_SOURCE, not s["has_queue"], s["source"])
    return sorted(rows, key=order)


def default_source(sources, need_queue=False):
    names = [s["source"] for s in sources if s["has_queue"] or not need_queue]
    return DEFAULT_SOURCE if DEFAULT_SOURCE in names else (names[0] if names else None)


def check_source(source, sources):
    if not NAME.fullmatch(str(source or "")) or source not in {s["source"] for s in sources}:
        raise LookupError("알 수 없는 출처입니다.")
    return source


def lines_view(source=None):
    sources = line_sources()
    if not sources:
        return empty("줄 정답 초안", "lines")
    source = check_source(source or default_source(sources), sources)
    path = Path(P["lines"]) / source / "summary.json"

    def make():
        s = read_json(path) or {}
        ln = s.get("lines", {})
        fields = []
        for name, f in (s.get("fields") or {}).items():
            human, present = f.get("human_checked_values", 0), f.get("gold_present", 0)
            fields.append({"field": name, "label": FIELD_LABEL.get(name, name), "group": f.get("field_group"),
                           "group_label": (s.get("group_labels") or {}).get(f.get("field_group"), f.get("field_group")),
                           "human": "all" if present and human >= present else ("none" if not human else "some"),
                           **{k: f.get(k) for k in ("human_checked_values", "gold_present", "auto_matched", "exact", "exact_substring",
                                                    "median_cer", "multi_line", "lines_matched", "tiers", "A_correct", "A_incorrect",
                                                    "B_correct", "by_split")}})
        keep = ("lines_total", "lines_with_alnum", "unmatched_lines_with_alnum", "lines_matched", "tiers", "A_correct", "A_incorrect",
                "B_correct", "human_checked", "small_text", "conf_mean_correct", "conf_mean_incorrect", "conf_auroc_correct_vs_incorrect",
                "text_edge_punct_kept", "posters_with_any_matched_line")
        return {"source": source, **{k: s.get(k) for k in ("engine", "variant", "gold", "built_at", "posters", "thresholds", "rules",
                                                             "group_labels", "splits", "small_text", "confusions")},
                "missing": len(s.get("missing") or []), "lines": {k: ln.get(k) for k in keep}, "fields": fields}
    return {"sources": sources, **cached(f"lines:{source}", [path], make)}


def engines_view():
    path = P["eval"]

    def make():
        d = read_json(path)
        if not d or "summary" not in d:
            return None
        rows = []
        for key, label in PIPELINES:
            per = (d["summary"].get(key) or {})
            if not per:
                continue
            splits = {}
            for split, s in per.items():
                reads = (s.get("reads") or {}).get("all") or {}
                fields = s.get("fields") or {}
                splits[split] = {"n": s.get("n"), "read": {k: reads.get(k) for k in ("mean_char_accuracy", "ci95", "strings", "fully_read")},
                                 "fields": {f: {k: (fields.get(f) or {}).get(k) for k in ("correct", "n", "rate", "ci95")} for f in ENGINE_FIELDS},
                                 "time": s.get("time")}
            rows.append({"key": key, "label": label, "splits": splits})
        head = {}
        for split, h in (d.get("headline") or {}).items():
            head[split] = {f: {"diff": (h.get("fields", {}).get(f) or {}).get("diff"), "mcnemar": (h.get("fields", {}).get(f) or {}).get("mcnemar")}
                           for f in ENGINE_FIELDS}
            head[split]["read_diff"] = ((h.get("reads") or {}).get("all") or {}).get("diff")
        eng = d.get("engine") or {}
        return {"created_at": d.get("created_at"), "notes": d.get("notes", []), "field_labels": (d.get("labels") or {}).get("fields", {}),
                "splits": [s for s in ("all", "dev", "test", "reserve") if any(s in r["splits"] for r in rows)], "pipelines": rows,
                "headline": head, "engine": {k: eng.get(k) for k in ("engine_label", "rapidocr_version", "onnxruntime_version", "device", "models_pinned_match")},
                "coverage": d.get("coverage"), "baseline_ok": ((d.get("baseline_check") or {}).get("recomputed_vs_stored_rows") or {}).get("n_different") == 0}
    view = cached("engines", [path], make)
    return view or empty("엔진별 판독률(RapidOCR 제품 비교)", "eval")


# ------------------------------------------------------------------ 줄 검토
def queue_sources():
    return [s for s in line_sources() if s["has_queue"]]


def safe_stats(source):
    """검토자 화면용 현황. 심은 제안 수·구간별 잡은 수는 보내지 않고, 충분히 검토한 뒤 잡은 비율만 보낸다."""
    folder = Path(P["lines"]) / source

    def make():
        st = L.review_stats(source, P["lines"])
        segs = {k: {"label": SEGMENT_LABEL.get(k, k), **{x: v.get(x) for x in ("total", "reviewed", "remaining", "ok", "fix", "exclude")}}
                for k, v in st["segments"].items()}
        shown = st["remaining"] == 0 and st["reviewed"] >= PLANTED_MIN_REVIEWS
        return {"source": source, "queue_total": st["queue_total"], "reviewed": st["reviewed"], "remaining": st["remaining"],
                "stale": st["stale"], "segments": segs, "reviewers": st["reviewers"],
                "planted": {"min_reviews": PLANTED_MIN_REVIEWS, "shown": shown,
                            "caught_rate": st["planted"]["caught_rate"] if shown else None,
                            "has_sample": bool(st["planted"]["reviewed"]) if shown else None}}
    return cached(f"stats:{source}", [folder / "summary.json", folder / "queue.json", folder / "review.jsonl"], make)


def shuffle_key(line_id):
    return hashlib.sha256(f"{SHUFFLE_SEED}:{line_id}".encode("utf-8")).hexdigest()


def queue_view(source=None, order="shuffle", show_ocr=True):
    sources = line_sources()
    queued = [s for s in sources if s["has_queue"]]
    if not sources:
        return empty("줄 정답 초안", "lines")
    if not queued:
        return {**empty("줄 검토 큐", "queue", source=default_source(sources)), "sources": sources}
    source = check_source(source or default_source(queued, True), queued)
    items = L.queue(source, P["lines"], show_ocr=show_ocr)   # queue.json이 있을 때만 부른다(없으면 queue()가 새로 만든다)
    drop = ("position", "polygon", "bbox")
    items = [{k: v for k, v in it.items() if k not in drop} for it in items]
    items.sort(key=(lambda it: (it["case_id"], it["line_id"])) if order == "poster" else (lambda it: shuffle_key(it["line_id"])))
    groups = Counter(it["field_label"] for it in items)
    return {"source": source, "sources": sources, "order": order, "show_ocr": show_ocr, "items": items,
            "field_labels": [g for g, _ in groups.most_common()], "stats": safe_stats(source),
            "no_queue": [{"source": s["source"], "command": COMMANDS["queue"].format(source=s["source"])} for s in sources if not s["has_queue"]]}


def record_line(body):
    sources = queue_sources()
    source = check_source(body.get("source"), sources)
    reviewer = str(body.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("검토자 이름을 먼저 적어 주세요.")
    line_id = str(body.get("line_id") or "")
    if not LINE_ID.fullmatch(line_id):
        raise ValueError("알 수 없는 줄입니다.")
    row = L.record(source, line_id, body.get("decision"), body.get("text"), reviewer, body.get("note"), P["lines"])
    return {"saved": row, "stats": safe_stats(source)}


def crop_png(source, line_id):
    sources = line_sources()
    source = check_source(source, sources)
    if not LINE_ID.fullmatch(str(line_id or "")):
        raise LookupError("알 수 없는 줄입니다.")
    try:
        return L.crop_line(source, line_id, out=P["lines"], manifest=P["manifest"])
    except ValueError as error:
        raise LookupError(str(error)) from error
    except OSError as error:
        raise LookupError("포스터 원본을 찾을 수 없습니다.") from error


# ------------------------------------------------------------------ 신뢰도 보정
def calib_view():
    path = Path(P["calib"]) / "summary.json"

    def make():
        d = read_json(path)
        if not d or "sources" not in d:
            return None
        series = d.get("series") or {}
        return {**{k: d.get(k) for k in ("created_at", "diagnostic_only", "notice", "service_use", "compare_note", "labels", "settings", "sources")},
                "series": {**{k: series.get(k) for k in ("posters", "series_total", "multi_series", "posters_in_multi", "folds", "rules")},
                           "reserve_overlap": (series.get("reserve_overlap") or {}).get("posters"),
                           "groups": [{k: g.get(k) for k in ("series_id", "size", "splits", "fold")} for g in (series.get("groups") or [])[:8]]}}
    return cached("calib", [path], make) or empty("신뢰도 보정 진단", "calib")


def calib_detail(source, ev="oof", scope="all"):
    folder = Path(P["calib"])
    if not NAME.fullmatch(str(source or "")) or not (folder / f"{source}.json").is_file():
        raise LookupError("알 수 없는 출처입니다.")
    if ev not in ("oof", "reserve") or scope not in ("all", "human_checked", "ai_assisted"):
        raise ValueError("평가·범위 이름이 잘못되었습니다.")
    path = folder / f"{source}.json"
    d = cached(f"calib-file:{source}", [path], lambda: read_json(path)) or {}
    block = ((d.get("evaluation") or {}).get(ev) or {})
    sc = (block.get("scopes") or {}).get(scope) or {"n": 0}
    keep = ("mean_score", "auroc", "brier", "ece", "auroc_ci95", "brier_ci95", "ece_ci95", "vs_raw", "ece_equal_mass",
            "reliability", "reliability_equal_mass", "risk_coverage", "selective")
    methods = {m: {k: v.get(k) for k in keep} for m, v in (sc.get("methods") or {}).items()}
    final = ((d.get("fit") or {}).get("final") or {})
    return {"source": source, "ev": ev, "scope": scope, "label": block.get("label"), "notice": d.get("notice"),
            "n": sc.get("n"), "posters": sc.get("posters"), "errors": sc.get("errors"), "accuracy": sc.get("accuracy"), "methods": methods,
            "fit": {"T": (final.get("temperature") or {}).get("T"), "platt": final.get("platt"),
                    "isotonic_blocks": (final.get("isotonic") or {}).get("blocks")},
            "line_accuracy": ((d.get("line_accuracy") or {}).get("scopes") or {}).get(scope)}


# ------------------------------------------------------------------ 가상 포스터 박스
def synth_sets():
    return sorted(p.parent.name for p in Path(P["synth"]).glob("*/summary.json") if NAME.fullmatch(p.parent.name))


def synth_view():
    sets = synth_sets()
    if not sets:
        return empty("가상 포스터 글자 상자", "synth")

    def make():
        out = []
        for name in sets:
            s = read_json(Path(P["synth"]) / name / "summary.json") or {}
            det = read_json(Path(P["synth"]) / name / "detection.json") or {}
            sections = {}
            for kind in ("poster", "document"):
                sec = det.get(kind) or {}
                sections[kind] = {k: sec.get(k) for k in ("product_relevant", "note", "images", "ocr_boxes", "unexplained_ocr_boxes", "unexplained_top",
                                                          "overall", "checked_fields", "by_field", "by_template")}
                sections[kind]["lines"] = (sec.get("lines") or {}).get("overall")
            overlays = [Path(f).name for f in ((s.get("files") or {}).get("overlays") or [])
                        if (Path(P["synth"]) / name / "overlays" / Path(f).name).is_file()]
            out.append({"set": name, **{k: s.get(k) for k in ("dataset_id", "sha256_check", "boxes", "excluded", "bbox_vs_ink_iou", "templates", "notes")},
                        "headline": s.get("detection"), "sections": sections, "overlays": overlays})
        first = read_json(Path(P["synth"]) / sets[0] / "detection.json") or {}
        return {"sets": out, "definitions": first.get("definitions") or {}, "field_labels": SYNTH_FIELD_LABEL, "status_labels": SYNTH_STATUS}
    files = [Path(P["synth"]) / n / f for n in sets for f in ("summary.json", "detection.json")]
    return cached("synth", files, make)


def safe_file(base, rel_path, allowed):
    """base 아래 allowed 폴더 안의 .png 파일만. 경로를 풀어서(resolve) 폴더 밖이면 거절한다."""
    rel_path = str(rel_path or "")
    if not rel_path or "\0" in rel_path or "\\" in rel_path or ":" in rel_path or rel_path.startswith("/"):
        raise LookupError("허용되지 않은 경로입니다.")
    base = Path(base).resolve()
    target = (base / rel_path).resolve()
    roots = [(base / a).resolve() for a in allowed]
    if target.suffix.lower() != ".png" or not any(target.is_relative_to(r) and target != r for r in roots) or not target.is_file():
        raise LookupError("허용되지 않은 경로입니다.")
    data = target.read_bytes()
    if not data.startswith(PNG_SIG):  # 이름만 png인 파일은 보내지 않는다(내용은 해석하지 않고 서명만 본다)
        raise LookupError("PNG 파일이 아닙니다.")
    return data


def synth_overlay(set_name, file_name):
    if not NAME.fullmatch(str(set_name or "")) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}\.png", str(file_name or "")):
        raise LookupError("허용되지 않은 경로입니다.")
    return safe_file(P["synth"], f"{set_name}/overlays/{file_name}", [f"{set_name}/overlays"])


# ------------------------------------------------------------------ CI 카탈로그
def _catalog():
    path = Path(P["logos"]) / "catalog.json"
    return cached("catalog-raw", [path], lambda: read_json(path))


def logo_index():
    cat = _catalog()
    if not cat:
        return None

    def make():
        by_entity = defaultdict(list)
        for f in cat.get("files", []):
            by_entity[f.get("entity_id")].append(f)
        return by_entity
    return cached("catalog-index", [Path(P["logos"]) / "catalog.json"], make)


def logos_view():
    base = Path(P["logos"])
    if not (base / "catalog.json").is_file():
        return empty("CI 로고 카탈로그", "logos")

    def make():
        cat = _catalog() or {}
        files = cat.get("files", [])
        c = cat.get("counts", {})
        terms_split = Counter("참조 가능" if f.get("reference_ready") else "조건 통과 · 렌더 안 됨" if f.get("usable_for_reference")
                              else "이용 조건 제외" if (f.get("terms") or {}).get("blocked") else "로고 아님(매뉴얼·묶음)" for f in files)
        human = sum(1 for f in files if set(f.get("flags") or ()) & set(LOGO_HUMAN_FLAGS))
        names = {e["entity_id"]: e.get("name") for e in cat.get("entities", [])}
        entities = []
        for e in cat.get("entities", []):
            t = e.get("terms") or {}
            entities.append({"entity_id": e.get("entity_id"), "name": e.get("name"), "kind": LOGO_KIND_LABEL.get(e.get("kind"), e.get("kind")),
                             "tier": e.get("tier"), "tier_label": LOGO_TIER_LABEL.get(e.get("tier"), e.get("tier")),
                             **{k: e.get(k) for k in ("files", "official_files", "commons_files", "usable_files", "reference_ready", "commons_ready",
                                                      "vector_ok", "by_format", "in_index", "commons_only")},
                             "terms": {k: t.get(k) for k in ("hold", "review", "limited_use", "approval_required", "blocked", "license", "dev_use")},
                             "flags": e.get("flags") or {}, "gold_logo_posters": (e.get("data_frequency") or {}).get("gold_logo_posters") or 0})
        link = read_json(base / "linkage.json") or {}
        rep = read_json(base / "render_report.json") or {}
        linkage = None
        if link:
            lc = link.get("counts") or {}
            linkage = {"notes": link.get("notes", []), "posters": link.get("posters"), "counts": lc, "by_split": link.get("by_split"),
                       "top_entities": [{**t, "name": names.get(t.get("entity_id"), t.get("entity_id")),
                                         "tier_label": LOGO_TIER_LABEL.get(t.get("tier"), t.get("tier"))} for t in link.get("top_entities", [])[:15]],
                       "unmatched_top": link.get("unmatched_top", [])[:20], "unmatched_by_category": link.get("unmatched_by_category")}
        render = {k: rep.get(k) for k in ("jobs", "by_status", "by_render_source", "icc", "needs_ghostscript", "out_long_side", "seconds",
                                          "failure_count", "disk", "elapsed_build_s")} if rep else None
        if rep:
            render["failures"] = [{k: f.get(k) for k in ("display_path", "format", "status", "error")} for f in rep.get("failures", [])[:20]]
        return {"created_at": cat.get("created_at"), "notes": cat.get("notes", []), "counts": {k: c.get(k) for k in (
                    "files", "skipped", "by_area", "by_format", "by_kind", "by_logo_kind", "by_render_status", "by_flag", "sheet_reasons", "name_fix",
                    "terms", "usable_for_reference", "reference_ready", "official_logo_like", "commons_logo_like", "entities", "dedup")},
                "terms_split": dict(terms_split), "human_check": human, "human_flags": list(LOGO_HUMAN_FLAGS), "entities": entities,
                "linkage": linkage, "render": render, "labels": {"flags": LOGO_FLAG_LABEL, "tiers": LOGO_TIER_LABEL, "status": LOGO_STATUS_LABEL,
                                                                    "formats": LOGO_FORMAT_LABEL}}
    return cached("logos", [base / n for n in ("catalog.json", "linkage.json", "render_report.json")], make)


def logo_entity(entity_id):
    index = logo_index()
    if index is None:
        return empty("CI 로고 카탈로그", "logos")
    if entity_id not in index:
        raise LookupError("알 수 없는 기관입니다.")
    cat = _catalog() or {}
    ent = next((e for e in cat.get("entities", []) if e.get("entity_id") == entity_id), {})
    rows = []
    for f in index[entity_id]:
        r, dup = f.get("render") or {}, f.get("dup") or {}
        rows.append({"file_id": f.get("file_id"), "name": f.get("display_name"), "path": f.get("display_path"), "area": f.get("area"),
                     "format": f.get("format"), "kind": f.get("kind"), "logo_kind": f.get("logo_kind"), "usable": f.get("usable_for_reference"),
                     "ready": f.get("reference_ready"), "blocked": (f.get("terms") or {}).get("blocked"), "thumb": r.get("thumb"),
                     "status": r.get("status"), "out_px": r.get("out_px"), "flags": f.get("flags") or [], "tags": f.get("tags") or [],
                     "rep": dup.get("is_exact_rep", True) is not False and dup.get("is_stem_rep", True) is not False,
                     "near_dup": dup.get("near_dup_group")})
    rows.sort(key=lambda x: (not x["ready"], not x["usable"], x["name"] or ""))
    return {"entity_id": entity_id, "name": ent.get("name"), "tier_label": LOGO_TIER_LABEL.get(ent.get("tier"), ent.get("tier")),
            "representatives": ent.get("representatives") or [], "files": rows}


def logo_thumb(rel_path):
    return safe_file(P["logos"], rel_path, ["thumb", "png"])


# ------------------------------------------------------------------ 요청 의도 라벨
def req_files():
    return [P["req_log"], P["req_labels"], P["req_texts"]]


def req_status():
    if not Path(P["req_labels"]).is_file():
        return None
    return cached("req-status", req_files(), lambda: rl.status(P["req_log"], P["req_labels"], P["req_texts"]))


def req_split():
    path = Path(P["req_split"])

    def make():
        sp = rl.split_spec(path, compute_if_missing=False)
        if not sp:
            return None
        pre = sp.get("preprocess") or {}
        nd = pre.get("near_duplicates") or {}
        kl = pre.get("keyword_leakage") or {}
        tp = pre.get("template_phrases") or {}
        join = sp.get("join") or {}
        return {"generated_at": sp.get("generated_at"), "seed": sp.get("seed"), "unit": sp.get("unit"), "method": sp.get("method"),
                "shares": sp.get("shares"), "join": {"ok": join.get("ok"), "counts": join.get("counts"),
                                                     "issues": {k: len(v) for k, v in (join.get("issues") or {}).items()}},
                "group_intent_check": sp.get("group_intent_check"), "group_to_split": sp.get("group_to_split"),
                "splits": {k: v.get("counts") for k, v in (sp.get("splits") or {}).items()}, "label_source": sp.get("label_source"),
                "lengths": pre.get("lengths"), "normalization": pre.get("normalization"), "duplicates": pre.get("duplicates"),
                "near": {"threshold": nd.get("threshold"), **{k: nd.get(k) for k in ("within_group", "cross_group", "cross_group_same_intent")},
                         "held_out_max_vs_train": (nd.get("cross_split") or {}).get("held_out_max_vs_train"),
                         "held_out_ge_threshold_vs_train": (nd.get("cross_split") or {}).get("held_out_ge_threshold_vs_train")},
                "leakage": {"by_intent": kl.get("by_intent"), "top": (kl.get("top") or [])[:12]},
                "templates": {k: tp.get(k) for k in ("phrases", "single_intent", "in_all_splits", "items")}, "notes": sp.get("notes", [])}
    return cached("req-split", [path], make)


def req_baseline():
    path = Path(P["req_baseline"])

    def make():
        b = rl.baseline_result(path)
        if not b:
            return None
        variants = {}
        for name, v in (b.get("variants") or {}).items():
            test, val = v.get("test") or {}, v.get("validation") or {}
            variants[name] = {"description": v.get("description"), "vocab_size": v.get("vocab_size"), "chosen_l2": v.get("chosen_l2"),
                              "validation": {k: val.get(k) for k in ("n", "accuracy", "macro_f1")},
                              "test": {**{k: test.get(k) for k in ("n", "accuracy", "macro_f1", "per_intent", "confusion", "by_template",
                                                                   "macro_f1_ci95_group_bootstrap")}, "errors": (test.get("errors") or [])[:10]}}
        return {**{k: b.get(k) for k in ("generated_at", "label_status", "diagnostic_only", "warning", "caveats", "labels", "summary")},
                "model": {k: (b.get("model") or {}).get(k) for k in ("features", "classifier", "min_df", "iterations")},
                "split_changed": (b.get("split") or {}).get("inputs_changed_since_split") or [], "variants": variants}
    return cached("req-baseline", [path], make)


def requests_view():
    st = req_status()
    if st is None:
        return empty("요청 의도 DRAFT 라벨", "req_labels")
    split, base = req_split(), req_baseline()
    return {"status": st, "intents": rl.INTENTS, "intent_names": rl.INTENT_NAMES, "states": rl.STATES, "state_labels": REQ_STATE_LABEL,
            "split": split or empty("요청 분할 명세", "req_split"), "baseline": base or empty("요청 의도 기준 모델(초안 라벨)", "req_baseline"),
            "commands": {"effective": COMMANDS["req_effective"]}}


def record_request(body):
    if not Path(P["req_labels"]).is_file():
        raise ValueError("요청 의도 라벨 파일이 없습니다.")
    decision = body.get("decision")
    intent = body.get("intent") or None
    entry = rl.record(str(body.get("request_id") or ""), decision, intent, str(body.get("reviewer") or ""), str(body.get("note") or ""),
                      log=P["req_log"], labels=P["req_labels"])
    return {"saved": entry, "status": req_status()}


# ------------------------------------------------------------------ 개요
def overview():
    sources = line_sources()
    tracks, heads, todo, missing = [], [], [], []
    lv = lines_view() if sources else None
    ev = engines_view()
    cv = calib_view()
    sv = synth_view()
    gv = logos_view()
    rv = requests_view()
    for name, view in (("포스터 줄 정답", lv or empty("줄 정답 초안", "lines")), ("엔진 판독률", ev), ("신뢰도 보정", cv), ("가상 포스터 상자", sv),
                       ("CI 로고 카탈로그", gv), ("요청 의도 라벨", rv)):
        if view.get("empty"):
            missing.append({"name": name, "command": view["command"]})

    # 포스터 OCR
    poster = {"key": "poster", "name": "포스터 OCR", "tab": "review", "facts": []}
    queued = [s for s in sources if s["has_queue"]]
    src = default_source(queued, True)
    if src:
        st = safe_stats(src)
        poster["progress"] = {"draft": st["queue_total"], "checked": st["reviewed"], "remaining": st["remaining"], "unit": "줄", "label": f"{src} 검토 큐"}
        heads.append({"label": "줄 검토", "value": f"{st['reviewed']:,} / {st['queue_total']:,}", "detail": f"{src} · 검토한 줄 / 큐", "tab": "review"})
        if st["remaining"]:
            todo.append({"p": 1, "text": f"줄 검토: {src} 큐에 {st['remaining']:,}개가 남았습니다.", "tab": "review"})
        if st["stale"]:
            todo.append({"p": 1, "text": f"{src} 줄 초안이 큐를 만든 뒤 바뀌었습니다. 검토 큐를 다시 볼지 정해야 합니다.", "tab": "review"})
        others = [s for s in queued if s["source"] != src]
        if others:
            poster["facts"].append("다른 검토 큐: " + ", ".join(f"{s['source']} {safe_stats(s['source'])['reviewed']}/{safe_stats(s['source'])['queue_total']}" for s in others))
    for s in sources:
        if not s["has_queue"]:
            todo.append({"p": 3, "text": f"{s['source']}는 검토 큐가 없습니다.", "command": COMMANDS["queue"].format(source=s["source"])})
    if lv and not lv.get("empty"):
        ln = lv["lines"]
        t = ln.get("tiers") or {}
        heads.insert(0, {"label": "정답과 맞춘 OCR 줄", "value": f"{ln.get('lines_matched') or 0:,}", "tab": "lines",
                         "detail": f"{lv['source']} · A {t.get('A_clean', 0):,} / B {t.get('B_edge_missing', 0):,} / C {t.get('C_partial', 0):,}"})
        poster["facts"].insert(0, f"{lv['source']}: 포스터 {lv.get('posters') or 0}장, OCR 줄 {ln.get('lines_total') or 0:,}개 중 {ln.get('lines_matched') or 0:,}개를 정답과 맞춤")
    if not ev.get("empty"):
        rows = {r["key"]: r for r in ev["pipelines"]}
        rd = lambda k: (((rows.get(k) or {}).get("splits", {}).get("all") or {}).get("read") or {}).get("mean_char_accuracy")
        tm = (((rows.get("rapidocr/product") or {}).get("splits", {}).get("all") or {}).get("time") or {}).get("median_s")
        if rd("rapidocr/product") is not None:
            heads.append({"label": "판독률 RapidOCR 제품", "value": f"{rd('rapidocr/product') * 100:.1f}%", "tab": "lines",
                          "detail": f"PaddleOCR 2배 {rd('paddleocr/rgb_2x') * 100:.1f}%" + (f" · 장당 {tm}초" if tm else "") if rd("paddleocr/rgb_2x") is not None else ""})
            poster["facts"].append(f"판독률(전체 252장): RapidOCR 제품 {rd('rapidocr/product') * 100:.1f}% · PaddleOCR 2배 {(rd('paddleocr/rgb_2x') or 0) * 100:.1f}%")
    if not cv.get("empty"):
        pick = next((s for s in cv["sources"] if s["source"] == DEFAULT_SOURCE), (cv["sources"] or [None])[0])
        if pick:
            m = (((pick.get("scopes") or {}).get("all") or {}).get("oof") or {}).get("methods") or {}
            if m.get("raw") and m.get("isotonic"):
                heads.append({"label": "신뢰도 ECE(교차검증)", "value": f"{m['raw']['ece']:.3f} → {m['isotonic']['ece']:.3f}", "tab": "calib",
                              "detail": f"{pick['source']} · 보정 전 → 등위 회귀 · 진단용"})
        todo.append({"p": 6, "text": "신뢰도 보정 값은 진단용입니다. 서비스에 넣을지는 팀이 정합니다.", "tab": "calib"})
    if not sv.get("empty"):
        last = sv["sets"][-1]
        h = ((last.get("headline") or {}).get("poster") or {})
        if h:
            poster["facts"].append(f"가상 포스터 {last['set']}: 그린 줄 재현율 {h.get('line_recall', 0) * 100:.1f}% · 상자 단위(IoU≥0.5) {h.get('recall', 0) * 100:.1f}%")
        todo.append({"p": 7, "text": "가상 v2는 홀드아웃입니다. 학습용 상자가 필요하면 새 틀(v3)로 만듭니다.", "tab": "synth"})
    tracks.append(poster)

    # CI 로고
    logo = {"key": "logo", "name": "CI 로고", "tab": "logos", "facts": []}
    if not gv.get("empty"):
        c = gv["counts"]
        ents = c.get("entities") or {}
        logo["progress"] = {"draft": c.get("files") or 0, "checked": None, "remaining": gv["human_check"], "unit": "파일",
                            "label": "자동 분류 · 사람 확인 표시"}
        logo["facts"] += [f"참조 가능 {c.get('reference_ready') or 0:,}개 / 파일 {c.get('files') or 0:,}개",
                          f"기관 {ents.get('total') or 0}곳 중 공식 참조 가능 {(ents.get('by_tier') or {}).get('official_ready', 0)}곳"]
        if gv.get("linkage"):
            lc = gv["linkage"]["counts"].get("logo_named") or {}
            logo["facts"].append(f"포스터 로고 언급 {lc.get('mentions', 0)}개 중 렌더된 참조까지 있는 것 {lc.get('official_ready', 0)}개")
        heads.append({"label": "참조 가능 로고", "value": f"{c.get('reference_ready') or 0:,}", "tab": "logos",
                      "detail": f"파일 {c.get('files') or 0:,}개 · 공식 참조 기관 {(ents.get('by_tier') or {}).get('official_ready', 0)}곳"})
        todo.append({"p": 4, "text": f"로고 사람 확인 표시 {gv['human_check']:,}개(낱장·그라디언트·미리보기·흰 로고·저해상도·아주 작음)를 훑어봅니다.", "tab": "logos"})
        gs = (c.get("by_render_status") or {}).get("needs_ghostscript")
        if gs:
            todo.append({"p": 5, "text": f"PostScript AI/EPS {gs:,}개는 Ghostscript가 없어 그리지 못했습니다(참조 후보에서 빠짐).", "tab": "logos"})
    tracks.append(logo)

    # 요청 의도
    intent = {"key": "intent", "name": "요청 의도", "tab": "intent", "facts": []}
    if not rv.get("empty"):
        t = rv["status"]["totals"]
        checked = t["approved"] + t["relabelled"] + t["flagged"]
        intent["progress"] = {"draft": t["total"], "checked": checked, "remaining": t["pending"], "unit": "건", "label": "DRAFT 라벨 사람 검토"}
        intent["facts"].append(f"승인 {t['approved']} · 변경 {t['relabelled']} · 보류 {t['flagged']} · 남음 {t['pending']}")
        b = rv["baseline"]
        detail = ""
        if not b.get("empty"):
            raw, strip = (b.get("summary") or {}).get("raw") or {}, (b.get("summary") or {}).get("template_stripped") or {}
            detail = f"기준 모델 macro-F1 {raw.get('test_macro_f1', 0):.2f}(템플릿 제거 {strip.get('test_macro_f1', 0):.2f}) · 초안 라벨"
            intent["facts"].append(detail)
        heads.append({"label": "요청 의도 검토", "value": f"{checked} / {t['total']}", "detail": detail or "검토한 건 / 전체", "tab": "intent"})
        if t["pending"]:
            todo.append({"p": 2, "text": f"요청 의도 {t['pending']}건을 사람이 확인합니다. 끝나면 유효 라벨로 기준 모델을 다시 돌립니다.",
                         "tab": "intent", "command": COMMANDS["req_effective"]})
        if rv["status"].get("group_conflicts"):
            todo.append({"p": 2, "text": f"그룹 안 의도가 갈린 그룹 {len(rv['status']['group_conflicts'])}개: 분할을 다시 만들어야 합니다.",
                         "command": COMMANDS["req_split"], "tab": "intent"})
    tracks.append(intent)
    for m in missing:
        todo.append({"p": 0, "text": f"{m['name']} 결과가 없습니다.", "command": m["command"]})
    todo.sort(key=lambda t: t.pop("p"))
    return {"tracks": tracks, "headlines": heads[:6], "todo": todo, "missing": missing}


# ------------------------------------------------------------------ HTTP
def _one(query, key, default=""):
    return (query.get(key) or [default])[0]


GET_JSON = {
    "/api/overview": lambda q: overview(),
    "/api/lines": lambda q: lines_view(_one(q, "source") or None),
    "/api/engines": lambda q: engines_view(),
    "/api/review/queue": lambda q: queue_view(_one(q, "source") or None, _one(q, "order", "shuffle"), _one(q, "ocr", "1") != "0"),
    "/api/review/stats": lambda q: safe_stats(check_source(_one(q, "source"), queue_sources())),
    "/api/calib": lambda q: calib_view(),
    "/api/calib/detail": lambda q: calib_detail(_one(q, "source"), _one(q, "ev", "oof"), _one(q, "scope", "all")),
    "/api/synth": lambda q: synth_view(),
    "/api/logos": lambda q: logos_view(),
    "/api/logos/entity": lambda q: logo_entity(_one(q, "id")),
    "/api/requests": lambda q: requests_view(),
    "/api/requests/status": lambda q: req_status() or empty("요청 의도 DRAFT 라벨", "req_labels"),
}
GET_PNG = {
    "/crop": lambda q: (crop_png(_one(q, "source"), _one(q, "line")), "no-store"),
    "/logo-thumb": lambda q: (logo_thumb(_one(q, "path")), "private, max-age=600"),
    "/synth-overlay": lambda q: (synth_overlay(_one(q, "set"), _one(q, "file")), "private, max-age=600"),
}
POST_JSON = {"/api/review/record": record_line, "/api/requests/record": record_request}
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    allowed_hosts = frozenset()
    server_version = "prep-dashboard"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send(self, code, body, kind, cache="no-store"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if kind.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        return self.send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def host_ok(self):
        return self.headers.get("Host", "") in self.allowed_hosts  # DNS 리바인딩 차단

    def do_GET(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "허용되지 않은 호스트입니다."})
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path in GET_JSON:
                return self.send_json(200, GET_JSON[url.path](query))
            if url.path in GET_PNG:
                data, cache = GET_PNG[url.path](query)
                return self.send(200, data, "image/png", cache)
            return self.send_json(404, {"error": "없는 주소입니다."})
        except LookupError as error:
            return self.send_json(404, {"error": str(error)})
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        except Exception:  # 대시보드는 계속 떠 있어야 한다. 자세한 내용은 서버 로그에만 남긴다
            traceback.print_exc(file=sys.stderr)
            return self.send_json(500, {"error": "서버 오류입니다. 서버 로그를 확인하세요."})

    def do_POST(self):
        host, origin = self.headers.get("Host", ""), self.headers.get("Origin")
        if host not in self.allowed_hosts or origin != f"http://{host}":
            return self.send_json(403, {"error": "다른 출처의 요청은 받지 않습니다."})
        url = urlsplit(self.path)
        if url.path not in POST_JSON:
            return self.send_json(404, {"error": "없는 주소입니다."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send_json(415, {"error": "JSON으로 보내 주세요."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self.send_json(413, {"error": "요청이 너무 큽니다."})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("JSON 객체가 필요합니다.")
            return self.send_json(200, POST_JSON[url.path](body))
        except LookupError as error:
            return self.send_json(404, {"error": str(error)})
        except (ValueError, UnicodeDecodeError) as error:
            return self.send_json(400, {"error": str(error)})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return self.send_json(500, {"error": "서버 오류입니다. 서버 로그를 확인하세요."})


def make_server(port=8767):
    """127.0.0.1에만 묶는다. 포트 0이면 빈 포트를 골라 쓴다(테스트용)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    real = server.server_address[1]
    server.RequestHandlerClass = type("BoundHandler", (Handler,), {"allowed_hosts": frozenset({f"127.0.0.1:{real}", f"localhost:{real}"})})
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    server = make_server(args.port)
    print(f"데이터 준비 대시보드: http://127.0.0.1:{server.server_address[1]}/ (이 컴퓨터에서만 열립니다)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
