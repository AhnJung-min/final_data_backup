"""가상 검수 세트 대조: 기준 문서 OCR → 검수 조건, 포스터 OCR → 조건별 판정(충족·위반·확인 필요)과 근거, 실험 채점.

문서 추출 기능이 아직 없으므로 기준 문서도 PaddleOCR 결과(줄·박스)에서 표의 '항목' 칸을 찾아 조건을 뽑는다.
판정은 정답(sets.json)을 보지 않는다. 정답은 evaluate()에서 채점할 때만 읽는다.
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_check --all
"""
from __future__ import annotations

import argparse
from datetime import date
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import statistics
import time

from .evaluate import key, semiglobal

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "data/synthetic/poster-check-v1"
RUNTIME = ROOT / "data/runtime/poster-ocr/sim"
RUNS = RUNTIME / "runs/paddleocr"
CHECKS = RUNTIME / "checks"
VARIANT = "rgb_1x"
WEEKDAY = "월화수목금토일"
DOC_LABELS = [("title", "행사명"), ("schedule", "행사 기간"), ("time", "운영 시간"), ("venue", "행사 장소"), ("host", "주최"),
              ("organizer", "주관"), ("contact", "문의처"), ("slogan", "필수 문구"), ("logo", "기관 로고")]
CONDITION_LABEL = {"title": "행사명", "edition": "회차", "schedule": "행사 기간", "time": "운영 시간", "venue": "행사 장소", "host": "주최",
                   "organizer": "주관", "contact": "문의처", "slogan": "필수 문구", "logo": "기관 로고"}
POSTER_LABEL = {"venue": "장소", "host": "주최", "organizer": "주관"}
POINT = re.compile(r"(?:(20\d{2})\s*[.년/]\s*)?(\d{1,2})\s*[.월/]\s*(\d{1,2})\s*(?:일|\.)?\s*(?:\(\s*([월화수목금토일])\s*\)?|([월화수목금토일])요일)?")
# 월을 생략한 끝 날짜: '~ 5일(월)'. OCR이 물결표를 놓친 '6일(목) 8일(토)'도 요일이 붙어 있으면 받는다.
DAY_ONLY = re.compile(r"(?:[~\-–]\s*(\d{1,2})\s*일\s*(?:\(\s*([월화수목금토일])\s*\)?)?|(?<!\d)(\d{1,2})\s*일\s*\(\s*([월화수목금토일])\s*\)?)")
TIME = re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)")
PHONE = re.compile(r"0\d{1,2}\s*[-.)]\s*\d{3,4}\s*[-.]\s*\d{4}")
EDITION = re.compile(r"제\s*(\d{1,3})\s*회")
SEPARATOR = r"(?:\s+[|Il1丨ㅣ│]\s+|\||ㅣ|│|주최|주관|문의|후원|$)"
EXACT, NEAR, SURE = 0.95, 0.75, 0.93  # 충족 기준 일치율, 비슷한 문구 하한, '다르게 읽힌 것이 확실' 신뢰도 하한
RULES = "sim_check r4"  # r1: 첫 실행. r2: 못 읽은 부분과 다른 글자 구분, 문서 안 행사명 불일치는 확인 필요. r3: 줄 묶기·신뢰도 계산 버그 수정. r4: 글자 없는 줄 제외, 라벨 경로도 못 읽은 낱말은 확인 필요, 기간·회차 위반에 신뢰도 기준, 겹친 OCR 박스의 중복 글자 제거(모두 같은 40세트를 보고 고침)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ OCR 줄
def text_conf(records):
    """판독 신뢰도: 글자가 있는 박스만 본다(장식에서 나온 문장부호 박스는 뺀다)."""
    texts = [r["confidence"] for r in records if key(r["text"])]
    return min(texts) if texts else min((r["confidence"] for r in records), default=0.0)


def rows_of(records):
    """글자 높이가 비슷하고 세로로 겹치는 OCR 박스끼리 한 줄로 묶는다(왼쪽→오른쪽). 큰 배경 숫자는 따로 줄이 된다."""
    rows = []
    for r in sorted(records, key=lambda r: (r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2):
        x0, y0, x1, y1 = r["bbox_xyxy"]
        h = y1 - y0
        for row in rows:
            rh = statistics.median(m["bbox_xyxy"][3] - m["bbox_xyxy"][1] for m in row["records"])
            overlap = min(y1, row["y1"]) - max(y0, row["y0"])
            if min(h, rh) / max(h, rh) > .5 and overlap > .5 * min(h, rh):
                row["records"].append(r)
                row["y0"], row["y1"] = min(row["y0"], y0), max(row["y1"], y1)
                break
        else:
            rows.append({"y0": y0, "y1": y1, "records": [r]})
    for row in rows:
        row["records"].sort(key=lambda r: r["bbox_xyxy"][0])
        row["text"] = " ".join(join_texts(row["records"]))
        row["conf"] = text_conf(row["records"])
    return sorted(rows, key=lambda row: row["y0"])


def join_texts(records):
    """가로로 겹친 OCR 박스는 겹친 폭만큼(글자 폭 기준) 신뢰도가 낮은 쪽 글자를 빼고 잇는다(같은 글자를 두 번 읽은 경우)."""
    texts = [r["text"] for r in records]
    for i in range(len(records) - 1):
        a, b = records[i], records[i + 1]
        overlap = a["bbox_xyxy"][2] - b["bbox_xyxy"][0]
        width = (a["bbox_xyxy"][2] - a["bbox_xyxy"][0]) / max(1, len(a["text"]))
        n = round(overlap / width) if overlap > 0 and width > 0 else 0
        if n < 1:
            continue
        if a["confidence"] <= b["confidence"] and n < len(texts[i]):
            texts[i] = texts[i][:-n].rstrip()
        elif n < len(texts[i + 1]):
            texts[i + 1] = texts[i + 1][n:].lstrip()
    return [x for x in texts if x]


def box_of(records):
    xs0, ys0, xs1, ys1 = zip(*(r["bbox_xyxy"] for r in records))
    return [round(min(xs0), 1), round(min(ys0), 1), round(max(xs1), 1), round(max(ys1), 1)]


def evidence(records):
    return [{"id": r["id"], "text": r["text"], "confidence": round(r["confidence"], 4), "bbox": r["bbox_xyxy"]} for r in records]


# ------------------------------------------------------------------ 날짜
def points(text, year_hint=None):
    """'2026. 10. 3.(토) ~ 10. 5.(월)', '10월 3일(토) ~ 5일(월)' 같은 문구 → [(연, 월, 일, 요일)]."""
    found, taken, year = [], [], year_hint
    for m in POINT.finditer(text):
        y, mo, d = (int(m.group(1)) if m.group(1) else None), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            year = y or year
            found.append((m.start(), [y, mo, d, m.group(4) or m.group(5)]))
            taken.append((m.start(), m.end()))
    if found:
        month = found[-1][1][1]
        for m in DAY_ONLY.finditer(text):  # '~ 5일(월)'처럼 월을 생략한 끝 날짜
            d, w = int(m.group(1) or m.group(3)), m.group(2) or m.group(4)
            if m.start() > found[0][0] and not any(a <= m.start() < b for a, b in taken) and 1 <= d <= 31:
                found.append((m.start(), [None, month, d, w]))
    return [(p[0] or year, p[1], p[2], p[3]) for _, p in sorted(found, key=lambda x: x[0])]


def calendar_weekday(y, m, d):
    try:
        return WEEKDAY[date(y, m, d).weekday()]
    except (TypeError, ValueError):
        return None


def fmt_points(ps):
    return " ~ ".join(f"{m}.{d}" + (f"({w})" if w else "") for _, m, d, w in ps) if ps else ""


# ------------------------------------------------------------------ 기준 문서 → 조건
def parse_document(payload):
    records = payload["records"]
    rows = rows_of(records)
    header = next((r for row in rows for r in row["records"] if key(r["text"]) == "항목"), None)
    value_x = next((r["bbox_xyxy"][0] for row in rows for r in row["records"] if key(r["text"]) == "표기내용"), None)
    note_x = next((r["bbox_xyxy"][0] for row in rows for r in row["records"] if key(r["text"]) == "비고"), None)
    width = payload["normalized_dimensions"][0]
    value_x = value_x or width * .3
    note_x = note_x or width * .76
    top = header["bbox_xyxy"][3] if header else 0
    mentions = []  # 계약명·용역 대상 칸에 다시 나오는 행사명
    for row in rows:
        if row["y0"] < top and row["records"] and key(row["records"][0]["text"]) in ("계약명", "용역대상"):
            text = " ".join(r["text"] for r in row["records"][1:])
            if "홍보" in text:
                mentions.append(EDITION.sub("", text.split("홍보")[0]).strip())
    found = {}
    for row in rows:
        if row["y0"] < top:
            continue
        cells = [r for r in row["records"] if r["bbox_xyxy"][0] < value_x - 8]
        values = [r for r in row["records"] if r["bbox_xyxy"][0] >= value_x - 8 and r["bbox_xyxy"][0] < note_x - 8]
        label = key(" ".join(r["text"] for r in cells))
        for name, text in DOC_LABELS:
            if name not in found and label == key(text) and values:
                found[name] = {"text": " ".join(r["text"] for r in values).strip(), "records": values, "label_records": cells}
    conditions = []
    for name, text in DOC_LABELS:
        item = found.get(name)
        base = {"key": name, "label": CONDITION_LABEL[name], "kind": "visual" if name == "logo" else "info",
                "expected": item["text"] if item else None, "source": evidence(item["records"]) if item else [],
                "source_box": box_of(item["records"] + item["label_records"]) if item else None}
        if name == "title" and item:
            m = EDITION.search(item["text"])
            core = EDITION.sub("", item["text"]).strip()
            variants = sorted({v for v in [core, *mentions] if key(v)}, key=lambda v: v != core)
            conditions.append({**base, "expected": core, "doc_variants": variants if len({key(v) for v in variants}) > 1 else []})
            if m:
                conditions.append({**base, "key": "edition", "label": CONDITION_LABEL["edition"], "expected": f"제{int(m.group(1))}회", "number": int(m.group(1))})
            continue
        if name == "schedule" and item:
            ps = points(item["text"])
            base.update(points=ps, year=ps[0][0] if ps else None)
        conditions.append(base)
    return conditions


# ------------------------------------------------------------------ 포스터 대조
def best_match(expected, rows, span=4):
    """기준 문구가 포스터 OCR(이어진 줄 최대 span개)에 가장 가깝게 나온 곳."""
    target = key(expected)
    if not target or not rows:
        return (0.0, [])
    by_width = []
    for width in range(1, min(span, len(rows)) + 1):
        scores = [(max(0.0, 1 - semiglobal(target, key(" ".join(row["text"] for row in rows[i:i + width]))) / len(target)), i)
                  for i in range(len(rows) - width + 1)]
        score, i = max(scores, key=lambda x: (x[0], -x[1]))
        by_width.append((score, width, i))
    top = max(s for s, _, _ in by_width)
    score, width, i = next(x for x in by_width if x[0] >= top - 1e-9)  # 같은 일치율이면 가장 좁은 범위
    return (score, rows[i:i + width])


def labelled_value(rows, label):
    for row in rows:
        m = re.search(rf"{label}\s*[:：]?\s*(.+?)\s*(?={SEPARATOR})", row["text"])
        if m and key(m.group(1)):
            return m.group(1).strip(), row
    return None, None


def diff_parts(expected, observed):
    """기준 문구와 포스터에서 읽은 문구를 맞춰 '다른 글자'(바뀜·끼어듦)와 '못 읽은 부분'(빠짐)을 나눈다."""
    a, b = key(expected), key(observed)
    blocks = [m for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if m.size]
    if not a or not blocks:
        return {"subs": [], "extra": [], "missing": [a] if a else [], "covered": 0.0}
    b = b[blocks[0].b:blocks[-1].b + blocks[-1].size]  # 맞춰진 범위 밖의 다른 문구는 뺀다
    subs, extra, missing = [], [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "replace":
            subs.append((a[i1:i2], b[j1:j2]))
        elif tag == "insert":
            extra.append(b[j1:j2])
        elif tag == "delete":
            missing.append(a[i1:i2])
    return {"subs": subs, "extra": extra, "missing": missing, "covered": 1 - sum(map(len, missing)) / len(a)}


def result(cond, status, reason, observed=None, records=(), method=""):
    return {"key": cond["key"], "label": cond["label"], "kind": cond["kind"], "expected": cond.get("expected"), "status": status,
            "reason": reason, "observed": observed, "evidence": evidence(records), "evidence_box": box_of(records) if records else None,
            "source": cond.get("source", []), "source_box": cond.get("source_box"), "method": method}


def judge_text(cond, rows):
    expected = cond["expected"]
    label = POSTER_LABEL.get(cond["key"])
    value, row = labelled_value(rows, label) if label else (None, None)
    if value:  # 포스터에 '주최·주관·장소' 표기가 있으면 그 옆 문구를 먼저 비교한다
        target = key(expected)
        score = max(0.0, 1 - semiglobal(target, key(value)) / len(target)) if target else 0.0
        stat = f"'{label}' 표기 옆 문구 · 문자 일치율 {score:.0%} · 판독 신뢰도 {row['conf']:.0%}"
        if score >= EXACT:
            return result(cond, "pass", "기준 문구가 포스터에 그대로 있습니다.", value, row["records"], stat)
        d = diff_parts(expected, value)
        words = {key(w) for w in expected.split()}
        if d["missing"] and not d["subs"] and not d["extra"] and d["covered"] >= .4 and all(m in words for m in d["missing"]):
            return result(cond, "review", f"포스터의 '{label}' 표기에서 '{' '.join(d['missing'])}' 부분을 읽지 못했습니다. 담당자 확인이 필요합니다.", value, row["records"], stat)
        if row["conf"] >= SURE - .03:
            return result(cond, "fail", f"포스터의 '{label}' 표기가 기준 문서와 다릅니다.", value, row["records"], stat)
        return result(cond, "review", f"포스터의 '{label}' 표기를 읽었지만 판독 신뢰도가 낮습니다.", value, row["records"], stat)
    score, used = best_match(expected, rows)
    recs = [r for row in used for r in row["records"]]
    observed = " / ".join(row["text"] for row in used)
    if score >= EXACT:
        return result(cond, "pass", "기준 문구가 포스터에 그대로 있습니다.", observed, recs, f"문자 일치율 {score:.0%}")
    d = diff_parts(expected, observed)
    conf = text_conf(recs) if recs else 0.0
    changed = [f"'{x}'→'{y}'" for x, y in d["subs"]] + [f"'{y}' 더 있음" for y in d["extra"]]
    stat = f"문자 일치율 {score:.0%} · 판독 신뢰도 {conf:.0%}"
    if changed and d["covered"] >= .6 and conf >= SURE:
        if cond.get("doc_variants"):
            return result(cond, "review", f"포스터와 다르지만({', '.join(changed)}) 기준 문서 안에서도 표기가 다르게 읽혀({' / '.join(cond['doc_variants'])}) 기준 확정이 필요합니다.",
                          observed, recs, stat)
        return result(cond, "fail", f"포스터 표기가 기준 문서와 다릅니다({', '.join(changed)}).", observed, recs, stat)
    words = {key(w) for w in expected.split()}
    if d["missing"] and not changed and d["covered"] >= .4 and all(m in words for m in d["missing"]):  # 띄어 쓴 낱말이 통째로 안 읽힘
        return result(cond, "review", f"포스터에서 '{' '.join(d['missing'])}' 부분을 읽지 못했습니다. 담당자 확인이 필요합니다.", observed, recs, stat)
    if score >= NEAR:
        return result(cond, "review", "비슷한 문구가 있지만 판독 신뢰도가 낮아 담당자 확인이 필요합니다.", observed, recs, stat)
    return result(cond, "review", "포스터에서 기준 문구를 찾지 못했습니다.", observed if score >= .5 else None, recs if score >= .5 else (), f"최대 문자 일치율 {score:.0%}")


def judge_schedule(cond, rows, poster_year):
    if not cond.get("points"):
        return result(cond, "review", "기준 문서의 행사 기간을 읽지 못했습니다.")
    year = cond["year"] or poster_year
    candidates = []
    for i, row in enumerate(rows):
        for j in (i + 1, i + 2):
            text = " ".join(r["text"] for r in rows[i:j])
            ps = points(text, year)
            if ps:
                candidates.append((len(ps), -(j - i), ps, rows[i:j]))
    if not candidates:
        return result(cond, "review", "포스터에서 날짜를 찾지 못했습니다.")
    _, _, ps, used = max(candidates, key=lambda c: (c[0], c[1]))
    recs = [r for row in used for r in row["records"]]
    want = [(m, d) for _, m, d, _ in cond["points"]]
    got = [(m, d) for _, m, d, _ in ps]
    want_span = (want[0], want[-1])
    got_span = (got[0], got[-1])
    bad_weekday = [f"{m}.{d}({w})" for y, m, d, w in ps if w and calendar_weekday(year, m, d) and calendar_weekday(year, m, d) != w]
    observed = " / ".join(row["text"] for row in used)
    conf = text_conf(recs)
    if (got_span != want_span or bad_weekday) and conf < SURE - .03:
        return result(cond, "review", f"기간으로 보이는 문구가 기준({fmt_points(cond['points'])})과 다르지만 판독 신뢰도가 낮습니다.", observed, recs,
                      f"포스터 {fmt_points(ps)} · 판독 신뢰도 {conf:.0%}")
    if got_span != want_span:
        note = " 포스터 요일도 달력과 맞지 않습니다(" + ", ".join(bad_weekday) + ")." if bad_weekday else ""
        return result(cond, "fail", f"행사 기간이 기준({fmt_points(cond['points'])})과 다릅니다.{note}", observed, recs, f"포스터 {fmt_points(ps)}")
    if bad_weekday:
        return result(cond, "fail", "날짜는 같지만 포스터 요일이 달력과 맞지 않습니다(" + ", ".join(bad_weekday) + ").", observed, recs, "달력 대조")
    return result(cond, "pass", "기준 기간과 같고 요일도 달력과 맞습니다.", observed, recs, f"포스터 {fmt_points(ps)}")


def judge_time(cond, rows):
    want = [f"{int(h):02d}:{mi}" for h, mi in TIME.findall(cond["expected"] or "")]
    if not want:
        return result(cond, "review", "기준 문서의 운영 시간을 읽지 못했습니다.")
    hits = [(row, [f"{int(h):02d}:{mi}" for h, mi in TIME.findall(row["text"])]) for row in rows]
    hits = [(row, found) for row, found in hits if found]
    for row, found in hits:
        if all(t in found for t in want):
            return result(cond, "pass", "기준 운영 시간과 같습니다.", row["text"], row["records"])
    pairs = [(row, found) for row, found in hits if len(found) >= 2]
    if pairs:
        row, found = pairs[0]
        if row["conf"] >= SURE - .03:
            return result(cond, "fail", f"운영 시간이 기준({' ~ '.join(want)})과 다릅니다.", row["text"], row["records"], "시각 비교")
        return result(cond, "review", "운영 시간으로 보이는 문구의 판독 신뢰도가 낮습니다.", row["text"], row["records"])
    return result(cond, "review", "포스터에서 운영 시간을 찾지 못했습니다.")


def judge_contact(cond, rows):
    want = re.sub(r"\D", "", cond["expected"] or "")
    if len(want) < 9:
        return result(cond, "review", "기준 문서의 문의처 번호를 읽지 못했습니다.")
    found = []
    for row in rows:
        for m in PHONE.finditer(row["text"]):
            found.append((re.sub(r"\D", "", m.group(0)), m.group(0), row))
    for digits, raw, row in found:
        if digits == want:
            return result(cond, "pass", "기준 문의처 번호와 같습니다.", raw, row["records"])
    if found:
        digits, raw, row = found[0]
        if row["conf"] >= SURE - .03:
            return result(cond, "fail", "포스터 문의처 번호가 기준 문서와 다릅니다.", raw, row["records"], "숫자 비교")
        return result(cond, "review", "전화번호로 보이는 문구의 판독 신뢰도가 낮습니다.", raw, row["records"])
    return result(cond, "review", "포스터에서 문의처 번호를 찾지 못했습니다.")


def judge_edition(cond, rows):
    found = [(int(m.group(1)), row) for row in rows for m in EDITION.finditer(row["text"])]
    for number, row in found:
        if number == cond["number"]:
            return result(cond, "pass", "기준 회차와 같습니다.", row["text"], row["records"])
    if found:
        number, row = found[0]
        if row["conf"] < SURE - .03:
            return result(cond, "review", f"회차로 보이는 문구(제{number}회)의 판독 신뢰도가 낮습니다.", row["text"], row["records"], f"판독 신뢰도 {row['conf']:.0%}")
        return result(cond, "fail", f"포스터 회차(제{number}회)가 기준({cond['expected']})과 다릅니다.", row["text"], row["records"], "숫자 비교")
    return result(cond, "review", "포스터에서 회차 표기를 찾지 못했습니다.")


def check_set(set_id, runs=RUNS, out_dir=CHECKS):
    doc_path, poster_path = runs / f"{set_id}-doc" / f"{VARIANT}.json", runs / f"{set_id}-poster" / f"{VARIANT}.json"
    if not (doc_path.exists() and poster_path.exists()):
        return None
    doc, poster = load(doc_path), load(poster_path)
    started = time.perf_counter()
    conditions = parse_document(doc)
    rows = [row for row in rows_of(poster["records"]) if key(row["text"])]  # 장식에서 나온 문장부호만 있는 줄은 뺀다
    years = [int(y) for row in rows for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", row["text"])]
    poster_year = max(set(years), key=years.count) if years else None
    results = []
    for cond in conditions:
        if cond["kind"] == "visual":
            results.append(result(cond, "review", "시각 조건은 담당자가 이미지에서 직접 확인합니다(이번 자동 검수 범위 밖).", method="시각 검수"))
        elif not cond.get("expected"):
            results.append(result(cond, "review", "기준 문서에서 이 항목의 값을 읽지 못했습니다."))
        elif cond["key"] == "schedule":
            results.append(judge_schedule(cond, rows, poster_year))
        elif cond["key"] == "time":
            results.append(judge_time(cond, rows))
        elif cond["key"] == "contact":
            results.append(judge_contact(cond, rows))
        elif cond["key"] == "edition":
            results.append(judge_edition(cond, rows))
        else:
            results.append(judge_text(cond, rows))
    counts = {s: sum(r["status"] == s for r in results) for s in ("pass", "fail", "review")}
    info_review = sum(r["status"] == "review" and r["kind"] == "info" for r in results)
    verdict = "fail" if counts["fail"] else ("review" if info_review else "pass")
    out = {"set": set_id, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "variant": VARIANT, "rules": RULES,
           "document": {"ocr_seconds": doc.get("inference_seconds"), "lines": len(doc["records"]), "size": doc["normalized_dimensions"],
                        "conditions": len(conditions)},
           "poster": {"ocr_seconds": poster.get("inference_seconds"), "lines": len(poster["records"]), "size": poster["normalized_dimensions"]},
           "results": results, "counts": counts, "verdict": verdict, "predicted_mismatch": counts["fail"] > 0,
           "check_seconds": round(time.perf_counter() - started, 3)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{set_id}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ------------------------------------------------------------------ 채점(정답 사용)
def evaluate(sets, checks):
    """세트 단위 혼동행렬, 불일치 유형별 탐지, 항목별 오탐, 문서 조건 판독 정확도."""
    cm = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    by_type, field_fp, doc_read, rows = {}, {}, {}, []
    truth_values = {"title": lambda d: d["title"], "edition": lambda d: f"제{d['edition']}회" if d["edition"] else None,
                    "schedule": lambda d: d["schedule"], "time": lambda d: d["time"], "venue": lambda d: d["venue"], "host": lambda d: d["host"],
                    "organizer": lambda d: d["organizer"], "contact": lambda d: d["contact"], "slogan": lambda d: d["slogan"]}
    for s in sets:
        c = checks.get(s["id"])
        if not c:
            continue
        truth = s["label"] == "mismatch"
        pred = c["predicted_mismatch"]
        cm[("tp" if pred else "fn") if truth else ("fp" if pred else "tn")] += 1
        fails = [r["key"] for r in c["results"] if r["status"] == "fail"]
        hit = bool(truth and s["mismatch"]["field"] in fails)
        if truth:
            t = by_type.setdefault(s["mismatch"]["type"], {"label": s["mismatch"]["description"], "n": 0, "caught": 0, "caught_field": 0})
            t["n"] += 1
            t["caught"] += pred
            t["caught_field"] += hit
        for f in fails:
            if not truth or f != s["mismatch"]["field"]:
                field_fp[f] = field_fp.get(f, 0) + 1
        for r in c["results"]:
            if r["key"] in truth_values:
                want = truth_values[r["key"]](s["document"])
                if want is None:
                    continue
                if r["key"] == "title":
                    want = s["document"]["title"]
                ok = key(r["expected"]) == key(want)
                d = doc_read.setdefault(r["key"], {"n": 0, "ok": 0})
                d["n"] += 1
                d["ok"] += ok
        rows.append({"set": s["id"], "truth": s["label"], "type": (s["mismatch"] or {}).get("type"), "predicted": "mismatch" if pred else "match",
                     "verdict": c["verdict"], "field_hit": hit, "fails": fails})
    n = sum(cm.values())
    acc = (cm["tp"] + cm["tn"]) / n if n else None
    recall = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else None
    precision = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else None
    return {"n": n, "confusion": cm, "accuracy": acc, "recall": recall, "precision": precision, "by_type": by_type,
            "field_false_alarms": field_fp, "document_read": doc_read, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--set", nargs="*")
    parser.add_argument("--runs", type=Path, default=RUNS)
    parser.add_argument("--out", type=Path, default=CHECKS)
    parser.add_argument("--sim", type=Path, default=SIM, help="가상 세트 폴더(sets.json이 있는 곳)")
    parser.add_argument("--eval-out", type=Path, help="채점 결과(evaluate)를 이 JSON 파일에 저장")
    args = parser.parse_args()
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sets = load(args.sim / "sets.json")["sets"]
    ids = [s["id"] for s in sets] if args.all else (args.set or [])
    checks = {}
    for set_id in ids:
        c = check_set(set_id, args.runs, args.out)
        if c:
            checks[set_id] = c
            print(set_id, c["verdict"], {r["key"]: r["status"] for r in c["results"]})
    report = evaluate([s for s in sets if s["id"] in checks], checks)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=1))
    if args.eval_out:
        args.eval_out.parent.mkdir(parents=True, exist_ok=True)
        args.eval_out.write_text(json.dumps({**report, "rules": RULES, "sim": args.sim.resolve().relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
