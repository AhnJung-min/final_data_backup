"""정답(기본: 사람 검토본 gold-v2h)과 OCR·추출 결과를 비교한다.

세 층을 분리한다.
1) 판독률: 정답 문구가 OCR 전체 문자열 어딘가에 있는지(준전역 편집거리, 공백·문장부호 제외). 추출 규칙과 무관한 엔진 성능.
2) 존재 탐지: 정답 present 슬롯을 추출 결과가 present로 냈는지(정밀도·재현율). illegible 정답은 제외한다.
3) 값 정확도: present 정답 슬롯의 값이 맞았는지. 비교 단위는 필드마다 다르다(아래 FIELD_RULES).
통계: 비율은 Wilson 95% 구간, 판독률 평균은 포스터 단위 부트스트랩, 경로 간 비교는 정확 McNemar(양측).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import re
import unicodedata

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PIPELINES = {"easyocr": "EasyOCR", "paddleocr": "PaddleOCR", "hybrid": "EasyOCR+Paddle 재인식"}
FIELD_RULES = {
    "core_all": "축제명·시작일·종료일·장소가 모두 인쇄된 포스터에서 넷 다 정답",
    "title": "공백·문장부호 제외 후 title.official 완전 일치",
    "title_any": "title.official이 정답 축제명 또는 정답의 영문·브랜드·별칭 병기(title.variants, 4자리 연도·회차 표기를 뺀 문구) 중 하나와 일치",
    "year": "정수 일치", "edition": "회차 숫자 일치",
    "schedule_start": "시작 월·일 일치", "schedule_end": "종료 월·일 일치(하루 행사는 시작일과 같게 봄)",
    "schedule_pair": "시작·종료 월·일 모두 일치", "start_year": "시작 연도 일치(정답에 연도가 있을 때)",
    "start_weekday": "인쇄 요일 일치(정답에 요일이 있을 때)", "venue": "공백·문장부호 제외 후 venue.raw 완전 일치",
    "venue_partial": "venue.raw 문자 일치율 ≥ 0.8", "budget": "예산 금액(원) 일치", "address": "주소 원문 완전 일치",
    "admin_district": "시·군·구 일치", "duration": "'N일간' 문구 일치",
}
LIST_FIELDS = ("organizations", "contacts", "links", "times")
# 목록 지표: LIST_FIELDS에 판독 가능 정답 기관만 센 기관 F1을 더한다(기존 organizations F1은 그대로 둔다).
LIST_METRICS = (*LIST_FIELDS, "organizations_legible")
LIST_RULES = {
    "organizations_legible": "이름·역할이 있고 legibility=legible인 정답 기관만 센 (역할, 이름) F1. 이름·역할이 있지만 판독 가능이 아닌"
                             "(partial 등) 정답 기관은 맞춤·놓침에 넣지 않고, 그 이름과 같은 예측(정답 판독 가능 기관과 맞지 않은 것)은 오검출로 세지 않는다.",
}
# title_any 비교 전 정답 병기(variants)에서 빼는 4자리 연도('2026', '2026년')와 회차 표기('제N회', 'The Nth', 'Nst/nd/rd/th').
TITLE_MARKS = re.compile(r"(?<![A-Za-z])the\s+\d{1,3}\s*(?:st|nd|rd|th)(?![A-Za-z])|(?<!\d)\d{1,3}\s*(?:st|nd|rd|th)(?![A-Za-z])"
                         r"|제\s*\d{1,3}\s*회|(?<!\d)(?:19|20)\d{2}(?!\d)(?:\s*년)?", re.I)
PRESENCE_SLOTS = ("title", "year", "edition", "schedule", "venue", "address", "admin_area", "organizations", "contacts",
                  "links", "admission", "notices", "budget_disclosure", "badges")


def key(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def semiglobal(pattern, text):
    """pattern을 text의 임의 부분 문자열에 맞출 때의 최소 편집거리(삽입은 열 방향 누적 최소로 벡터화)."""
    if not pattern:
        return 0
    if not text:
        return len(pattern)
    t = np.array([ord(c) for c in text], dtype=np.int64)
    idx = np.arange(len(t) + 1, dtype=np.int64)
    prev = np.zeros(len(t) + 1, dtype=np.int64)
    for i, ch in enumerate(pattern, 1):
        cur = np.empty_like(prev)
        cur[0] = i
        cur[1:] = np.minimum(prev[:-1] + (t != ord(ch)), prev[1:] + 1)
        prev = np.minimum.accumulate(cur - idx) + idx
    return int(prev.min())


def levenshtein(a, b):
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def wilson(k, n, z=1.959964):
    if n == 0:
        return [None, None]
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0, centre - half), 4), round(min(1, centre + half), 4)]


def mcnemar(pairs):
    """pairs: (a_correct, b_correct) 목록 → (a만 맞음, b만 맞음, 정확 양측 p)."""
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if y and not x)
    n = b + c
    if n == 0:
        return b, c, 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return b, c, round(min(1.0, 2 * p), 6)


def bootstrap_mean(values_by_case, seed=20261019, rounds=2000):
    cases = [v for v in values_by_case.values() if v]
    if not cases:
        return None, [None, None]
    flat = [x for v in cases for x in v]
    mean = sum(flat) / len(flat)
    rng = random.Random(seed)
    stats = []
    for _ in range(rounds):
        sample = [cases[rng.randrange(len(cases))] for _ in cases]
        values = [x for v in sample for x in v]
        stats.append(sum(values) / len(values))
    stats.sort()
    return round(mean, 4), [round(stats[int(.025 * rounds)], 4), round(stats[int(.975 * rounds) - 1], 4)]


# ------------------------------------------------------------- gold text inventory
def gold_texts(doc):
    """판독률을 잴 정답 문구(범주, 문자열)."""
    out = []
    def add(category, text):
        if text and len(key(text)) >= 2:
            out.append((category, text))
    if doc["title"]["status"] == "present":
        add("title", doc["title"]["raw"])
        for v in doc["title"]["variants"]:
            add("title_variant", v["raw"])
    for slot in ("edition", "venue", "address", "budget_disclosure", "admission"):
        if doc[slot]["status"] == "present":
            add(slot, doc[slot]["raw"])
    if doc["schedule"]["status"] == "present":
        add("schedule", doc["schedule"]["raw"])
        for t in doc["schedule"]["times"]:
            add("times", t["raw"])
    for item in doc["slogans"]["items"]:
        add("slogans", item["raw"])
    for item in doc["organizations"]["items"]:
        if item["name"] and item["legibility"] == "legible" and item["form"] == "text":
            add("organizations", item["name"])
    for item in doc["contacts"]["items"]:
        add("contacts", item["raw"])
    for item in doc["links"]["items"]:
        add("links", item["raw"])
    for item in doc["programs"]["items"]:
        add("programs", item["name"])
    for item in doc["notices"]["items"]:
        add("notices", item["raw"])
    for item in doc["badges"]["items"]:
        add("badges", item["raw"])
    return out


def ocr_text(records):
    """읽기 순서(위→아래, 왼→오)로 OCR 문자열을 잇는다."""
    ordered = sorted(records, key=lambda r: (round((r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2 / 8), r["bbox_xyxy"][0]))
    return "".join(key(r["text"]) for r in ordered)


# ------------------------------------------------------------- field comparison
def end_of(schedule):
    return schedule["end"] or schedule["start"]


def link_key(raw):
    raw = raw.lower().strip()
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    return key(raw.rstrip("/"))


def item_sets(doc):
    sets = {
        "organizations": {(tuple(sorted(i["roles"])), key(i["name"])) for i in doc["organizations"]["items"] if i["name"] and i["roles"]},
        "contacts": {re.sub(r"\D", "", i["value"] or i["raw"]) for i in doc["contacts"]["items"] if i["kind"] == "phone"},
        "links": {link_key(i["raw"]) for i in doc["links"]["items"]},
        "times": {t["start"] for t in doc["schedule"]["times"] if t["start"]} if doc["schedule"]["status"] == "present" else set(),
    }
    return {k: {v for v in s if v not in ("", None) and v != ((), "")} for k, s in sets.items()}


def variant_key(raw):
    """title_any용 병기 비교 문구: 4자리 연도·회차 표기를 뺀 key(병기 원문 raw는 인쇄된 그대로 둔다)."""
    return key(TITLE_MARKS.sub(" ", raw or ""))


def legible_organizations(gold, pred):
    """organizations_legible의 TP·FP·FN(LIST_RULES 참고). 비교 단위는 기존 기관 F1과 같은 (역할, 이름 key)다."""
    named = [i for i in gold["organizations"]["items"] if i["name"] and i["roles"]]
    legible = {(tuple(sorted(i["roles"])), key(i["name"])) for i in named if i.get("legibility") == "legible"}
    unsure = {key(i["name"]) for i in named if i.get("legibility") != "legible"}
    predicted = item_sets(pred)["organizations"]
    extra = predicted - legible
    return {"tp": len(predicted & legible), "fp": sum(1 for _, name in extra if name not in unsure), "fn": len(legible - predicted)}


def compare(gold, pred):
    """필드별 (분모 포함 여부, 정답 여부)와 목록 필드 TP/FP/FN."""
    out = {}
    def put(name, applicable, correct):
        if applicable:
            out[name] = bool(correct)
    g, p = gold, pred
    present = lambda doc, slot: doc[slot]["status"] == "present"
    put("title", present(g, "title"), present(p, "title") and key(p["title"]["official"]) == key(g["title"]["official"]))
    names = {key(g["title"]["official"])} | ({variant_key(v["raw"]) for v in g["title"]["variants"]} - {""}) if present(g, "title") else set()
    put("title_any", present(g, "title"), present(p, "title") and key(p["title"]["official"]) in names)
    if present(g, "title"):
        out["title_cer"] = min(1.0, levenshtein(key(g["title"]["official"]), key(p["title"]["official"]) if present(p, "title") else "")
                               / max(1, len(key(g["title"]["official"]))))
    put("year", present(g, "year"), present(p, "year") and p["year"]["value"] == g["year"]["value"])
    put("edition", present(g, "edition"), present(p, "edition") and p["edition"]["number"] == g["edition"]["number"])
    gs, ps = g["schedule"], p["schedule"]
    if gs["status"] == "present" and gs["start"]:
        ok = ps["status"] == "present" and ps["start"] is not None
        start_ok = ok and (ps["start"]["month"], ps["start"]["day"]) == (gs["start"]["month"], gs["start"]["day"])
        end_ok = ok and (end_of(ps)["month"], end_of(ps)["day"]) == (end_of(gs)["month"], end_of(gs)["day"])
        put("schedule_start", True, start_ok)
        put("schedule_end", True, end_ok)
        put("schedule_pair", True, start_ok and end_ok)
        put("start_year", gs["start"]["year"] is not None, ok and ps["start"]["year"] == gs["start"]["year"])
        put("start_weekday", gs["start"]["weekday"] is not None, ok and ps["start"]["weekday"] == gs["start"]["weekday"])
        put("duration", gs["duration_raw"] is not None, ok and key(ps["duration_raw"]) == key(gs["duration_raw"]))
    if present(g, "venue"):
        gk = key(g["venue"]["raw"])
        pk = key(p["venue"]["raw"]) if present(p, "venue") else ""
        put("venue", True, pk == gk)
        put("venue_partial", True, bool(pk) and 1 - levenshtein(gk, pk) / max(1, len(gk)) >= .8)
    put("budget", present(g, "budget_disclosure") and g["budget_disclosure"]["total_krw"] is not None,
        present(p, "budget_disclosure") and p["budget_disclosure"]["total_krw"] == g["budget_disclosure"]["total_krw"])
    put("address", present(g, "address"), present(p, "address") and key(p["address"]["raw"]) == key(g["address"]["raw"]))
    put("admin_district", present(g, "admin_area") and g["admin_area"]["district"],
        present(p, "admin_area") and key(p["admin_area"]["district"]) == key(g["admin_area"]["district"]))
    core = [out[name] for name in ("title", "schedule_start", "schedule_end", "venue") if name in out]
    put("core_all", len(core) == 4, all(core))  # 정답에 네 필드가 모두 인쇄된 포스터만 분모
    lists = {}
    gsets, psets = item_sets(g), item_sets(p)
    for name in LIST_FIELDS:
        lists[name] = {"tp": len(gsets[name] & psets[name]), "fp": len(psets[name] - gsets[name]), "fn": len(gsets[name] - psets[name])}
    lists["organizations_legible"] = legible_organizations(g, p)
    presence = {}
    for slot in PRESENCE_SLOTS:
        if g[slot]["status"] == "illegible":
            continue
        presence[slot] = (g[slot]["status"] == "present", p[slot]["status"] == "present")
    return out, lists, presence


# ------------------------------------------------------------- driver
def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate(manifest_path, gold_dir, runs_root, pred_root, splits=("dev", "test"), reviewed_only=True, pipelines=None):
    """pipelines: {이름: OCR 실행 폴더 이름}. 결합 경로는 기본 경로의 OCR로 판독률·시간을 잰다."""
    pipelines = pipelines or {name: name for name in PIPELINES}
    cases = [c for c in load(manifest_path)["cases"] if c["split"] in splits]
    golds = {}
    for c in cases:
        path = Path(gold_dir) / f"{c['id']}.poster.json"
        doc = load(path) if path.exists() else None
        version = ((doc or {}).get("extraction", {}).get("extractor") or {}).get("version") or ""
        complete = doc is not None and doc["extraction"]["status"] == "completed" and all(
            doc[s]["status"] != "not_checked" for s in ("title", "schedule", "venue", "organizations", "contacts"))
        if complete and (not reviewed_only or version.endswith(("pass2-reviewed", "human-reviewed"))):
            golds[c["id"]] = doc
    results = {}
    for pipeline, ocr_source in pipelines.items():
        for variant in ("rgb_2x", "rgb_1x"):
            rows = []
            for c in cases:
                if c["id"] not in golds:
                    continue
                ocr_path = Path(runs_root) / ocr_source / c["id"] / f"{variant}.json"
                pred_path = Path(pred_root) / pipeline / c["id"] / f"{variant}.poster.json"
                if not ocr_path.exists() or not pred_path.exists():
                    continue
                ocr = load(ocr_path)
                gold, pred = golds[c["id"]], load(pred_path)
                fields, lists, presence = compare(gold, pred)
                text = ocr_text(ocr["records"])
                reads = [{"category": cat, "chars": len(key(t)),
                          "accuracy": max(0.0, 1 - semiglobal(key(t), text) / len(key(t)))} for cat, t in gold_texts(gold)]
                rows.append({"case_id": c["id"], "split": c["split"], "fields": fields, "lists": lists,
                             "presence": {k: list(v) for k, v in presence.items()}, "reads": reads,
                             "inference_seconds": ocr.get("inference_seconds"), "lines": len(ocr["records"]),
                             "small_text": gold["quality"]["small_text"]})
            if rows:
                results[f"{pipeline}/{variant}"] = rows
    return results, golds


def summarize(rows):
    summary = {"n": len(rows), "fields": {}, "lists": {}, "presence": {}, "reads": {}, "time": {}}
    names = sorted({n for r in rows for n in r["fields"] if n != "title_cer"})
    for name in names:
        values = [r["fields"][name] for r in rows if name in r["fields"]]
        k = sum(values)
        summary["fields"][name] = {"correct": k, "n": len(values), "rate": round(k / len(values), 4) if values else None,
                                   "ci95": wilson(k, len(values))}
    cers = [r["fields"]["title_cer"] for r in rows if "title_cer" in r["fields"]]
    summary["fields"]["title_cer_mean"] = round(sum(cers) / len(cers), 4) if cers else None
    for name in LIST_METRICS:
        counted = [r["lists"][name] for r in rows if name in r["lists"]]  # 이전에 저장한 행에는 새 지표가 없을 수 있다
        tp = sum(c["tp"] for c in counted)
        fp = sum(c["fp"] for c in counted)
        fn = sum(c["fn"] for c in counted)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if tp + fp and tp + fn else None)
        summary["lists"][name] = {"tp": tp, "fp": fp, "fn": fn, "precision": r4(precision), "recall": r4(recall), "f1": r4(f1)}
    for slot in PRESENCE_SLOTS:
        pairs = [tuple(r["presence"][slot]) for r in rows if slot in r["presence"]]
        tp = sum(1 for g, p in pairs if g and p)
        fp = sum(1 for g, p in pairs if p and not g)
        fn = sum(1 for g, p in pairs if g and not p)
        summary["presence"][slot] = {"gold_present": tp + fn, "tp": tp, "fp": fp, "fn": fn,
                                     "precision": r4(tp / (tp + fp)) if tp + fp else None,
                                     "recall": r4(tp / (tp + fn)) if tp + fn else None}
    categories = sorted({x["category"] for r in rows for x in r["reads"]})
    for category in ["all", *categories]:
        by_case = {r["case_id"]: [x["accuracy"] for x in r["reads"] if category == "all" or x["category"] == category] for r in rows}
        mean, ci = bootstrap_mean(by_case)
        full = [x["accuracy"] == 1.0 for r in rows for x in r["reads"] if category == "all" or x["category"] == category]
        summary["reads"][category] = {"strings": len(full), "mean_char_accuracy": mean, "ci95": ci,
                                      "fully_read": round(sum(full) / len(full), 4) if full else None}
    times = sorted(r["inference_seconds"] for r in rows if r["inference_seconds"] is not None)
    if times:
        summary["time"] = {"median_s": round(times[len(times) // 2], 2), "p90_s": round(times[int(len(times) * .9) - 1], 2),
                           "mean_s": round(sum(times) / len(times), 2)}
    return summary


def r4(value):
    return None if value is None else round(value, 4)


def paired(results, a, b, split, field):
    left = {r["case_id"]: r["fields"].get(field) for r in results[a] if r["split"] == split}
    right = {r["case_id"]: r["fields"].get(field) for r in results[b] if r["split"] == split}
    pairs = [(left[k], right[k]) for k in left if k in right and left[k] is not None and right[k] is not None]
    only_a, only_b, p = mcnemar(pairs)
    return {"n": len(pairs), "only_a": only_a, "only_b": only_b, "p_exact": p}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/benchmarks/posters-v2.json")
    parser.add_argument("--gold", type=Path, default=ROOT / "data/benchmarks/gold-v2h")
    parser.add_argument("--runs", type=Path, default=ROOT / "data/runtime/poster-ocr/runs")
    parser.add_argument("--pred", type=Path, default=ROOT / "data/runtime/poster-ocr/pred")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--splits", nargs="+", default=["dev", "test"])
    parser.add_argument("--include-unreviewed", action="store_true", help="2차 검토 전 1차 라벨도 정답으로 사용(개발 중 확인용)")
    parser.add_argument("--pipeline", action="append", default=[], help="추가 경로 '이름=OCR폴더'(예: fusion_pe=paddleocr)")
    args = parser.parse_args()
    pipelines = {name: name for name in PIPELINES}
    pipelines.update(dict(item.split("=", 1) for item in args.pipeline))
    results, golds = evaluate(args.manifest, args.gold, args.runs, args.pred, args.splits, reviewed_only=not args.include_unreviewed,
                              pipelines=pipelines)
    summary = {name: {split: summarize([r for r in rows if r["split"] == split]) for split in args.splits}
               for name, rows in results.items()}
    comparisons = {}
    for split in args.splits:
        for field in ("core_all", "title", "schedule_pair", "venue", "year"):
            for a, b in (("paddleocr/rgb_2x", "easyocr/rgb_2x"), ("hybrid/rgb_2x", "easyocr/rgb_2x"), ("paddleocr/rgb_2x", "hybrid/rgb_2x"),
                         ("easyocr/rgb_2x", "easyocr/rgb_1x"), ("paddleocr/rgb_2x", "paddleocr/rgb_1x")):
                if a in results and b in results:
                    comparisons[f"{split}:{field}:{a} vs {b}"] = paired(results, a, b, split, field)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"field_rules": FIELD_RULES, "list_rules": LIST_RULES, "summary": summary, "comparisons": comparisons,
                                    "rows": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, by_split in summary.items():
        for split, s in by_split.items():
            f = s["fields"]
            print(f"{name:20s} {split:4s} n={s['n']:3d} core={f.get('core_all', {}).get('rate')} title={f.get('title', {}).get('rate')} "
                  f"pair={f.get('schedule_pair', {}).get('rate')} venue={f.get('venue', {}).get('rate')} read={s['reads'].get('all', {}).get('mean_char_accuracy')} "
                  f"org_leg_f1={s['lists'].get('organizations_legible', {}).get('f1')}")


if __name__ == "__main__":
    main()
