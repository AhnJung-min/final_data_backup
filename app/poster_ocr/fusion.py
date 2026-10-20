"""필드 단위 결합: 두 경로가 만든 표준 문서를 슬롯 단위로 고른다(OCR 기록은 섞지 않는다).

정책 두 가지.
- absent_fill(기본): 기본 경로가 찾지 못한(absent) 슬롯만 보조 경로의 추출값으로 채운다.
- paddle2x1x: 같은 엔진의 두 전처리를 쓴다. 기본 PaddleOCR rgb_2x, 보조 PaddleOCR rgb_1x.
  1) 제목: 두 문서의 제목이 다르면 근거 OCR 기록의 평균 판독 신뢰도가 더 높은 쪽(같은 엔진이라 신뢰도를 견줄 수 있다. 같으면 rgb_2x).
  2) 회차: rgb_1x에 회차가 있으면 rgb_1x 값.
  3) 기관: rgb_1x의 기관 목록 전체(rgb_1x가 찾지 못했으면 없음).
  4) 그 밖에 rgb_2x가 찾지 못한 슬롯: rgb_1x 값(absent_fill과 같은 규칙).
  정답·파일명·TourAPI 값은 보지 않고 OCR 문자도 고치지 않는다. 입력은 두 문서와 각 OCR 실행의 기록 신뢰도뿐이다.

일정과 연도는 서로 묶여 있어 함께 가져온다. 결합 문서의 근거 OCR ID는 두 실행의 ID를 합친 집합으로 검증한다
(ID 접두어가 엔진·전처리마다 달라 겹치지 않는다).

  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.fusion --policy paddle2x1x --name fusion_p21
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.fusion --primary paddleocr --secondary hybrid --name fusion_ph
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import unicodedata

from app.festival_data.poster import LISTS, SCALARS, validate_poster
from app.festival_data.settings import CollectionError

ROOT = Path(__file__).resolve().parents[2]
LINKED = {"schedule": ("schedule", "year"), "year": ("schedule", "year")}
POLICIES = ("absent_fill", "paddle2x1x")
# paddle2x1x 정책의 경로: 같은 엔진(pred/<engine>, runs/<engine>)의 두 전처리.
PADDLE2X1X = {"engine": "paddleocr", "primary": "rgb_2x", "secondary": "rgb_1x"}


def fill_absent(doc, secondary):
    """doc에서 absent인 슬롯을 secondary의 present 값으로 채운다(일정·연도는 한 묶음). 가져온 슬롯 목록을 돌려준다."""
    taken = []
    for slot in [*SCALARS, *LISTS]:
        if doc[slot]["status"] == "present" or secondary[slot]["status"] != "present":
            continue
        group = LINKED.get(slot, (slot,))
        if any(doc[s]["status"] == "present" for s in group if s != slot):
            continue  # 일정·연도 중 하나라도 기본 경로에 있으면 짝을 깨지 않는다.
        for s in group:
            doc[s] = copy.deepcopy(secondary[s])
            taken.append(s)
    return list(dict.fromkeys(taken))


def fuse(primary, secondary, label):
    """absent_fill 정책."""
    doc = copy.deepcopy(primary)
    taken = fill_absent(doc, secondary)
    doc["extraction"]["extractor"]["model"] = label
    doc["extraction"]["notes"] = doc["extraction"]["notes"] + [f"필드 단위 결합: 기본 경로에서 찾지 못한 {', '.join(taken) or '없음'}을 보조 경로에서 가져옴."]
    return doc, taken


def text_key(text):
    """제목 비교용 key: NFKC·대소문자 무시, 글자·숫자만."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(text or "")).casefold() if ch.isalnum())


def mean_confidence(slot, confidences):
    """슬롯 근거 OCR 기록의 평균 판독 신뢰도(신뢰도가 없는 기록은 뺀다). 근거가 없으면 None."""
    ids = (slot.get("evidence") or {}).get("ocr_ids") or []
    values = [confidences.get(i) for i in ids]
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def fuse_paddle2x1x(primary, secondary, primary_conf, secondary_conf, label):
    """paddle2x1x 정책. primary=rgb_2x 문서, secondary=rgb_1x 문서, *_conf = {OCR ID: 신뢰도}."""
    doc = copy.deepcopy(primary)
    taken = []
    a, b = doc["title"], secondary["title"]
    if a["status"] == "present" and b["status"] == "present" and text_key(a["official"]) and text_key(b["official"]) \
            and text_key(a["official"]) != text_key(b["official"]):
        ca, cb = mean_confidence(a, primary_conf), mean_confidence(b, secondary_conf)
        if ca is not None and cb is not None and cb > ca:
            doc["title"] = copy.deepcopy(b)
            taken.append("title")
    if secondary["edition"]["status"] == "present":
        doc["edition"] = copy.deepcopy(secondary["edition"])
        taken.append("edition")
    doc["organizations"] = copy.deepcopy(secondary["organizations"])
    taken.append("organizations")
    taken += fill_absent(doc, secondary)
    taken = list(dict.fromkeys(taken))
    doc["extraction"]["extractor"]["model"] = label
    doc["extraction"]["notes"] = doc["extraction"]["notes"] + [
        f"필드 단위 결합(paddle2x1x): 제목은 근거 판독 신뢰도가 높은 전처리, 회차·기관은 rgb_1x, 나머지 빈 슬롯은 rgb_1x로 채움. "
        f"rgb_1x에서 가져온 슬롯: {', '.join(taken)}."]
    return doc, taken


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def selected_cases(manifest, splits):
    """splits가 있으면 매니페스트에서 그 분할의 사례 ID만, 없으면 None(모든 사례)."""
    if not splits:
        return None
    return {c["id"] for c in load(manifest)["cases"] if c["split"] in splits}


def write_checked(doc, ids, target, stats, label):
    stats["documents"] += 1
    try:
        validate_poster(doc, ids)
        stats["valid"] += 1
    except CollectionError as error:
        stats["errors"].append(f"{label}: {error}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def run(primary_dir, secondary_dir, out_dir, label, primary_runs, secondary_runs, variant_map, cases=None):
    """absent_fill 정책으로 기본 경로의 모든 예측 문서를 결합한다(cases가 있으면 그 사례만)."""
    stats = {"documents": 0, "valid": 0, "taken": {}, "errors": []}
    for path in sorted(Path(primary_dir).glob("*/*.poster.json")):
        case_id, p_variant = path.parent.name, path.name.split(".")[0]
        if cases is not None and case_id not in cases:
            continue
        s_variant = variant_map.get(p_variant, p_variant)
        other = Path(secondary_dir) / case_id / f"{s_variant}.poster.json"
        if not other.exists():
            continue
        doc, taken = fuse(load(path), load(other), label)
        ids = set()
        for runs, variant in ((primary_runs, p_variant), (secondary_runs, s_variant)):
            ids |= {r["id"] for r in load(Path(runs) / case_id / f"{variant}.json")["records"]}
        for slot in taken:
            stats["taken"][slot] = stats["taken"].get(slot, 0) + 1
        write_checked(doc, ids, Path(out_dir) / case_id / f"{p_variant}.poster.json", stats, f"{case_id}/{p_variant}")
    return stats


def run_paddle2x1x(pred_dir, runs_dir, out_dir, label, cases=None, primary="rgb_2x", secondary="rgb_1x"):
    """paddle2x1x 정책: pred_dir/<사례>/rgb_2x·rgb_1x 문서와 runs_dir/<사례>/rgb_2x·rgb_1x OCR 신뢰도로
    out_dir/<사례>/rgb_2x.poster.json을 만든다. rgb_1x 문서가 없는 사례는 rgb_2x 문서를 그대로(결합 표시만) 쓴다."""
    stats = {"documents": 0, "valid": 0, "taken": {}, "errors": [], "secondary_missing": 0}
    for path in sorted(Path(pred_dir).glob(f"*/{primary}.poster.json")):
        case_id = path.parent.name
        if cases is not None and case_id not in cases:
            continue
        payloads = {}
        for variant in (primary, secondary):
            ocr = Path(runs_dir) / case_id / f"{variant}.json"
            payloads[variant] = load(ocr)["records"] if ocr.exists() else None
        if payloads[primary] is None:
            stats["errors"].append(f"{case_id}: {primary} OCR 기록이 없습니다.")
            continue
        other = path.parent / f"{secondary}.poster.json"
        if other.exists() and payloads[secondary] is not None:
            confidences = {v: {r["id"]: r.get("confidence") for r in payloads[v]} for v in (primary, secondary)}
            doc, taken = fuse_paddle2x1x(load(path), load(other), confidences[primary], confidences[secondary], label)
            ids = {r["id"] for v in (primary, secondary) for r in payloads[v]}
        else:
            stats["secondary_missing"] += 1
            doc, taken = load(path), []
            doc["extraction"]["extractor"]["model"] = label
            doc["extraction"]["notes"] = doc["extraction"]["notes"] + [f"필드 단위 결합(paddle2x1x): {secondary} 문서가 없어 {primary} 값만 씀."]
            ids = {r["id"] for r in payloads[primary]}
        for slot in taken:
            stats["taken"][slot] = stats["taken"].get(slot, 0) + 1
        write_checked(doc, ids, Path(out_dir) / case_id / f"{primary}.poster.json", stats, case_id)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", choices=POLICIES, default="absent_fill")
    parser.add_argument("--primary", help="기본 경로 이름(pred/<이름>). absent_fill에는 필요, paddle2x1x는 엔진 폴더(기본 paddleocr)")
    parser.add_argument("--secondary", help="보조 경로 이름(absent_fill)")
    parser.add_argument("--name", required=True, help="결과 경로 이름")
    parser.add_argument("--pred-root", type=Path, default=ROOT / "data/runtime/poster-ocr/pred")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "data/runtime/poster-ocr/runs")
    parser.add_argument("--secondary-variant", help="absent_fill: 보조 경로는 이 전처리만 쓴다(예: rgb_1x)")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/benchmarks/posters-v2.json")
    parser.add_argument("--splits", nargs="+", choices=["dev", "test", "reserve"], help="이 분할의 사례만 결합한다(없으면 모든 사례)")
    args = parser.parse_args()
    cases = selected_cases(args.manifest, args.splits)
    if args.policy == "paddle2x1x":
        engine = args.primary or PADDLE2X1X["engine"]
        label = f"{engine}/{PADDLE2X1X['primary']}+{engine}/{PADDLE2X1X['secondary']}(paddle2x1x)"
        stats = run_paddle2x1x(args.pred_root / engine, args.runs_root / engine, args.pred_root / args.name, label, cases,
                               PADDLE2X1X["primary"], PADDLE2X1X["secondary"])
    else:
        if not (args.primary and args.secondary):
            parser.error("absent_fill 정책에는 --primary와 --secondary가 필요합니다.")
        variant_map = {"rgb_2x": args.secondary_variant, "rgb_1x": args.secondary_variant} if args.secondary_variant else {}
        stats = run(args.pred_root / args.primary, args.pred_root / args.secondary, args.pred_root / args.name,
                    f"{args.primary}+{args.secondary}(fallback)", args.runs_root / args.primary, args.runs_root / args.secondary,
                    variant_map, cases)
    print(json.dumps({k: (v[:10] if k == "errors" else v) for k, v in stats.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
