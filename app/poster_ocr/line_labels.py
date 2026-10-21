"""줄 정답 초안: 사람 검토 정답(gold-v2h)의 값을 저장된 OCR 줄 기록에 맞춰 줄 단위 전사 초안을 만들고, 검토 기록을 모은다.

정렬(포스터·정답 값마다): 정답 문구를 품은 OCR 줄 1개(단일) 또는 정답 문구 안에 들어가는 이웃 줄 여러 개(다중, 정답 순서로 이음) 중
정규화(NFC·casefold 뒤 글자·숫자만) 편집거리 비율이 가장 작은 것을 고르고 0.3 이하만 받는다. 줄마다 가장 좋은 라벨 하나를 남긴다.
등급: A_clean 줄 글자 전체가 정답 일부와 맞고 가장자리 누락 없음 / B_edge_missing 정답 가장자리 글자가 줄에 없거나 다중 조각 사이에
틈·겹침이 있음(잘린 글자일 수 있음) / C_partial 줄에 정답과 무관한 글자가 섞임(전사 초안이 줄 일부만 덮음).
human_checked: 사람 검토 8칸(human_review.FIELDS: 축제명·연도·회차·시작/종료일·요일·장소)에서 온 값만 참이다.
슬로건·기관·연락처·주소·프로그램·배지 등은 AI 보조 판독값이라 거짓이다.

  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.line_labels build --engine paddleocr --variant rgb_2x
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.line_labels queue --source paddleocr-rgb_2x
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.line_labels stats --source paddleocr-rgb_2x
출력 <out>/<engine>-<variant>/: <case_id>.lines.json, summary.json, confusions.jsonl, 검토용 queue.json·review.jsonl.
포스터 원본(KOGL 3유형, 내부 평가 전용)은 잘라 저장하지 않는다. crop()은 검토 화면용 PNG를 메모리에서만 만든다.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import functools
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys
import threading
import unicodedata

from .evaluate import key
from .human_review import FIELDS as HUMAN_SLOTS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
GOLD = ROOT / "data/benchmarks/gold-v2h"
REVIEW = ROOT / "data/benchmarks/human-review"
RUNS = ROOT / "data/runtime/poster-ocr/runs"
OUT = ROOT / "data/runtime/poster-ocr/lines-v2"
ACCEPT = 0.3          # 값 단위 수용 기준(정규화 CER)
PIECE_MAX = 0.34      # 다중 조각: 줄 글자가 정답 안에 cost/len(줄) ≤ 이 값으로 들어가야 조각이 된다
MIN_GOLD = 2          # 정규화 뒤 2자 미만인 정답 값은 건너뛴다
MIN_PIECE = 2         # 이보다 짧은 줄은 정확히 같은 1글자일 때만 다중 조각에 낀다(기준 조각은 못 됨)
LENIENT_EDGE = 0      # A·B 등급에서 줄 가장자리에 허용하는 정답 밖 OCR 글자 수
ONE_CHAR_PIECES = True
SMALL_PX = 10         # small_text: 원본 픽셀 상자 높이 < 10
PAD = 0.12            # crop 여백(상자 높이 비율, hybrid.prepare_crop와 같은 12%)
SAMPLE = 150          # 검토 큐에 넣는 A 일치 줄 표본 수
PLANT_RATE = 0.05     # 검토 큐에 일부러 틀린 제안을 심는 비율
SEED = 20261021
TIER_RANK = {"A_clean": 0, "B_edge_missing": 1, "C_partial": 2}
# 한 줄에 여러 값이 맞을 때 라벨로 고를 필드 순서(등급·일치가 같을 때)
TAG_PRIORITY = ("title.official", "title.raw", "edition.raw", "schedule.raw", "schedule.times", "venue.items", "venue.raw",
                "address.raw", "contacts", "links", "budget.raw", "admission.raw", "organizations.text",
                "organizations.logo", "slogans", "programs", "notices", "badges")
FIELD_GROUP = {"title.official": "title", "title.raw": "title", "edition.raw": "edition", "schedule.raw": "schedule",
               "schedule.times": "time", "venue.items": "venue", "venue.raw": "venue", "address.raw": "address",
               "contacts": "contact", "links": "link", "budget.raw": "budget", "admission.raw": "admission",
               "organizations.text": "organization", "organizations.logo": "organization", "slogans": "slogan",
               "programs": "program", "notices": "notice", "badges": "badge"}
GROUP_LABEL = {"title": "축제명", "edition": "회차", "schedule": "일정", "time": "시간", "venue": "장소", "address": "주소",
               "contact": "연락처", "link": "링크", "budget": "예산", "admission": "입장", "organization": "기관",
               "slogan": "문구", "program": "프로그램", "notice": "안내", "badge": "배지"}
# 정렬 필드 → 그 값을 확인한 사람 검토 칸(human_review.FIELDS). 여기 없는 필드는 AI 보조 판독값이다.
HUMAN_SOURCE = {"title.official": ("title",), "edition.raw": ("edition",),
                "schedule.raw": ("year", "start", "start_weekday", "end", "end_weekday"),
                "venue.raw": ("venue",), "venue.items": ("venue",)}
# 사람이 문자열 자체를 확정한 필드. 회차·일정 원문은 AI 전사이고 사람은 숫자·날짜·요일을 확인했으므로 그 칸을 고쳤으면 원문이 낡았을 수 있다.
DIRECT = {"title.official", "venue.raw", "venue.items"}
assert {s for slots in HUMAN_SOURCE.values() for s in slots} <= set(HUMAN_SLOTS)
# 줄 경계에 걸린 문장부호를 나눌 때: 여는 부호는 뒤 줄, 닫는 부호는 앞 줄에 붙는다고 본다(OCR 줄에 보이는 부호가 우선).
OPENERS = set("([{<「『《〈【〔“‘")
CLOSERS = set(")]}>」』》〉】〕”’.,:;!?%…")
SEGMENTS = ("A_incorrect", "B_edge_missing", "A_correct_sample")
DECISIONS = ("ok", "fix", "exclude")
HEIGHT_BINS = (0, 8, 10, 12, 16, 24, 40)
RULES = {
    "tier": {"A_clean": "줄 글자 전체가 정답 일부와 정렬되고 가장자리 누락 없음(줄 전사 정답 후보)",
             "B_edge_missing": "정답 가장자리 글자가 줄에 없거나 다중 조각 사이에 틈·겹침이 있음(잘린 글자일 수 있어 확인 필요)",
             "C_partial": "줄에 정답과 무관한 글자가 섞임(text는 줄의 일부만 덮음, 줄 전사 정답으로 쓰지 않음)"},
    "human_checked": "사람 검토 8칸에서 온 값만 참: title.official·venue.raw·venue.items는 항상, edition.raw는 회차 칸을, schedule.raw는 "
                     "연도·시작/종료일·요일 칸을 사람이 고치지 않았을 때만(원문은 AI 전사라 고친 경우 낡았을 수 있음). 나머지는 AI 보조 판독값.",
    "text": "정답 원문(NFC)에서 이 줄에 해당하는 부분. 단일 줄 정렬은 정답 원문 전체, 다중 줄 정렬은 조각마다 나누되 글자에 바로 붙은 "
            "가장자리 문장부호는 남기고(OCR 줄에 보이는 쪽 우선, 없으면 닫는 부호는 앞 줄·여는 부호는 뒤 줄), 공백으로 떨어진 부호는 "
            "OCR 줄에 보일 때만 붙인다. text_key는 evaluate.key(NFKC·casefold·글자/숫자만).",
    "line_correct": "정렬용 정규화(NFC·casefold·글자/숫자만)로 OCR 줄과 정답 부분이 같은가",
    "small_text": f"상자 높이(원본 픽셀, 사각형 좌우 변 길이 평균) < {SMALL_PX}",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ 정규화·편집거리
def norm_map(raw):
    """NFC + casefold 후 글자·숫자만. (정규화 문자열, NFC 원문 위치 목록, NFC 원문)."""
    s = unicodedata.normalize("NFC", raw or "")
    out, idx = [], []
    for i, ch in enumerate(s):
        if ch.isalnum():
            for cc in ch.casefold():
                out.append(cc)
                idx.append(i)
    return "".join(out), idx, s


def semiglobal(p, t):
    """p를 t의 임의 부분 문자열에 맞출 때의 최소 편집거리와 그 부분의 (시작, 끝)."""
    m, n = len(p), len(t)
    if m == 0:
        return 0, 0, 0
    if n == 0:
        return m, 0, 0
    prev = [0] * (n + 1)
    prev_s = list(range(n + 1))
    for i in range(1, m + 1):
        pi = p[i - 1]
        cur = [i] * (n + 1)
        cur_s = [0] * (n + 1)
        for j in range(1, n + 1):
            best = prev[j - 1] + (pi != t[j - 1])
            bs = prev_s[j - 1]
            v = prev[j] + 1
            if v < best:
                best, bs = v, prev_s[j]
            v = cur[j - 1] + 1
            if v < best:
                best, bs = v, cur_s[j - 1]
            cur[j] = best
            cur_s[j] = bs
        prev, prev_s = cur, cur_s
    end = min(range(n + 1), key=lambda j: (prev[j], j))
    return prev[end], prev_s[end], end


def lev(a, b):
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lev_ops(a, b):
    """a(OCR) → b(정답) 정렬: (op, a 위치|None, b 위치|None) 목록. 동점이면 대각선을 택해 삽입·삭제를 앞쪽으로 민다."""
    m, n = len(a), len(b)
    d = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        d[i][0] = i
    for j in range(n + 1):
        d[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    ops = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            ops.append(("eq" if a[i - 1] == b[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(("ins", i - 1, None))      # OCR에만 있는 글자
            i -= 1
        else:
            ops.append(("del", None, j - 1))      # OCR에 없는 정답 글자
            j -= 1
    ops.reverse()
    return ops


def near(a, b):
    """두 상자 [x0,y0,x1,y1]가 줄 높이 기준으로 이웃인가."""
    h = max(a[3] - a[1], b[3] - b[1], 1.0)
    gx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return gx <= 2.0 * h and gy <= 1.5 * h


# ------------------------------------------------------------------ 정답 값 목록
def human_fields(doc, review):
    """이 정답 문서에서 human_checked가 참인 정렬 필드(RULES['human_checked'])."""
    version = ((doc.get("extraction") or {}).get("extractor") or {}).get("version") or ""
    if not version.endswith("human-reviewed"):
        return set()
    changed = set(review.get("changed") or []) if review else None
    return {f for f, slots in HUMAN_SOURCE.items() if f in DIRECT or (changed is not None and not changed & set(slots))}


def gold_values(doc, human=frozenset()):
    out = []

    def add(field, raw):
        if raw:
            out.append((field, raw))

    if doc["title"]["status"] == "present":
        add("title.raw", doc["title"].get("raw"))
        add("title.official", doc["title"].get("official"))
    if doc["edition"]["status"] == "present":
        add("edition.raw", doc["edition"].get("raw"))
    for it in doc["slogans"].get("items") or []:
        add("slogans", it.get("raw"))
    if doc["schedule"]["status"] == "present":
        add("schedule.raw", doc["schedule"].get("raw"))
        for t in doc["schedule"].get("times") or []:
            add("schedule.times", t.get("raw"))
    if doc["venue"]["status"] == "present":
        add("venue.raw", doc["venue"].get("raw"))
        vr = norm_map(doc["venue"].get("raw"))[0]
        for it in doc["venue"].get("items") or []:
            if norm_map(it)[0] != vr:
                add("venue.items", it)
    if doc["address"]["status"] == "present":
        add("address.raw", doc["address"].get("raw"))
    for it in doc["organizations"].get("items") or []:
        if it.get("name"):
            add("organizations.text" if it.get("form") == "text" else "organizations.logo", it["name"])
    for it in doc["contacts"].get("items") or []:
        add("contacts", it.get("raw"))
    for it in doc["links"].get("items") or []:
        add("links", it.get("raw"))
    for it in doc["programs"].get("items") or []:
        add("programs", it.get("name"))
    for it in doc["notices"].get("items") or []:
        add("notices", it.get("raw"))
    for it in doc["badges"].get("items") or []:
        add("badges", it.get("raw"))
    if doc["admission"]["status"] == "present":
        add("admission.raw", doc["admission"].get("raw"))
    if doc["budget_disclosure"]["status"] == "present":
        add("budget.raw", doc["budget_disclosure"].get("raw"))
    seen, res = set(), []
    for field, raw in out:
        g, gmap, graw = norm_map(raw)
        if len(g) < MIN_GOLD or (field, g) in seen:
            continue
        seen.add((field, g))
        res.append({"field": field, "raw": graw, "norm": g, "map": gmap, "human": field in human})
    return res


# ------------------------------------------------------------------ 정렬
def match_value(g, lines):
    """단일(정답이 한 줄 안) 또는 다중(이웃 줄들이 정답 안, 정답 순서로 이음) 중 가장 나은 정렬."""
    gset = set(g)
    best_a = None
    for li, ln in enumerate(lines):
        t = ln["norm"]
        if not t or not (gset & set(t)):
            continue
        c, s, e = semiglobal(g, t)
        cand = (c / len(g), c, li, s, e)
        if best_a is None or cand[:2] < best_a[:2] or (cand[:2] == best_a[:2] and len(t) < len(lines[best_a[2]]["norm"])):
            best_a = cand
    pieces = []
    for li, ln in enumerate(lines):
        t = ln["norm"]
        if not t or len(t) > len(g) + 2 or not (gset & set(t)):
            continue
        if len(t) < MIN_PIECE:
            # 1글자 줄(동그라미 요일 '목' 등)은 정답의 같은 글자 자리마다 정확 일치 조각으로만 낀다
            if ONE_CHAR_PIECES:
                for k, ch in enumerate(g):
                    if ch == t:
                        pieces.append({"li": li, "s": k, "e": k + 1, "c": 0, "one": True})
            continue
        c, s, e = semiglobal(t, g)
        if c / len(t) <= PIECE_MAX and e > s:
            pieces.append({"li": li, "s": s, "e": e, "c": c, "one": False})
    best_b = None
    if len(pieces) >= 2:
        anchors = sorted([p for p in pieces if not p["one"]], key=lambda p: (-(p["e"] - p["s"]), p["c"]))[:6]
        for anchor in anchors:
            cluster = [anchor]
            concat = lambda cl: "".join(lines[p["li"]]["norm"] for p in sorted(cl, key=lambda p: (p["s"], p["e"])))
            cur = lev(concat(cluster), g)
            while True:
                add, add_d = None, cur
                for p in pieces:
                    if any(p["li"] == q["li"] for q in cluster):
                        continue
                    if any(min(p["e"], q["e"]) - max(p["s"], q["s"]) >
                           (0 if p["one"] or q["one"] else max(1, 0.5 * min(p["e"] - p["s"], q["e"] - q["s"])))
                           for q in cluster):
                        continue
                    if not any(near(lines[p["li"]]["bbox"], lines[q["li"]]["bbox"]) for q in cluster):
                        continue
                    d = lev(concat(cluster + [p]), g)
                    if d < add_d:
                        add, add_d = p, d
                if add is None:
                    break
                cluster.append(add)
                cur = add_d
            if len(cluster) >= 2:
                cand = (cur / len(g), cur, sorted(cluster, key=lambda p: (p["s"], p["e"])))
                if best_b is None or cand[:2] < best_b[:2]:
                    best_b = cand
    if best_a is None and best_b is None:
        return None
    # CER 동점이면 단일 줄에 무관한 글자가 2자 이상 섞인 경우 다중을 택한다
    a_key = (best_a[0], 1 if len(lines[best_a[2]]["norm"]) - (best_a[4] - best_a[3]) >= 2 else 0) if best_a else None
    if best_b is None or (best_a is not None and a_key <= (best_b[0], 0)):
        cer, c, li, s, e = best_a
        return {"kind": "single", "cer": cer, "cost": c, "lines": [li], "span": (s, e)}
    cer, c, cluster = best_b
    return {"kind": "multi", "cer": cer, "cost": c, "lines": [p["li"] for p in cluster],
            "spans": [(p["s"], p["e"]) for p in cluster]}


def nospace(s):
    return "".join(ch for ch in s if not ch.isspace())


def edge_punct(text, end):
    """OCR 줄 앞(end=False)·뒤(end=True) 가장자리의 글자·숫자 아닌 문자(공백 제외)."""
    s = unicodedata.normalize("NFC", text or "")
    run = []
    for ch in (reversed(s) if end else s):
        if ch.isalnum():
            break
        run.append(ch)
    return nospace("".join(reversed(run)) if end else "".join(run))


def split_gap(gap, left, right):
    """두 조각 사이 정답 원문 틈(gap, 글자·숫자 없음)을 (앞 줄 몫, 뒤 줄 몫)으로 나눈다.
    left/right는 앞·뒤 줄 OCR의 가장자리 부호(그쪽에 조각이 없으면 None). 글자에 바로 붙은 부호는 버리지 않고,
    공백으로 떨어진 부호는 OCR 줄에 보일 때만 붙인다. 그 밖에는 닫는 부호는 앞 줄, 여는 부호는 뒤 줄로 보낸다."""
    n, best = len(gap), None
    for i in (range(n + 1) if left is not None else (0,)):
        for j in (range(i, n + 1) if right is not None else (n,)):
            drop = [p for p in range(i, j) if not gap[p].isspace()]
            if left is not None and any(not any(c.isspace() for c in gap[:p]) for p in drop):
                continue  # 앞 줄 글자에 붙은 부호는 버릴 수 없다
            if right is not None and any(not any(c.isspace() for c in gap[p + 1:]) for p in drop):
                continue
            a, b = nospace(gap[:i]), nospace(gap[j:])
            cost = (lev(a, left) if left is not None else 0) + (lev(b, right) if right is not None else 0)
            cost += sum(ch in OPENERS for ch in a) + sum(ch in CLOSERS for ch in b)
            k = (cost, len(drop), -i, j)
            if best is None or k < best[0]:
                best = (k, i, j)
    return gap[:best[1]], gap[best[2]:]


def piece_texts(graw, gmap, spans, ocr_texts):
    """다중 정렬 조각마다 정답 원문 부분(가장자리 문장부호 포함). 조각 경계 틈의 부호는 split_gap으로 나눈다."""
    n = len(gmap)
    before, after = [""] * len(spans), [""] * len(spans)
    for x in sorted({s for s, _ in spans} | {e for _, e in spans}):
        lo = gmap[x - 1] + 1 if x > 0 else 0
        hi = gmap[x] if x < n else len(graw)
        gap = graw[lo:hi]
        if not gap.strip():
            continue
        lefts = [k for k, (s, e) in enumerate(spans) if e == x and e > s]
        rights = [k for k, (s, e) in enumerate(spans) if s == x and e > s]
        if not lefts and not rights:
            continue
        to_left, to_right = split_gap(gap, edge_punct(ocr_texts[lefts[0]], True) if lefts else None,
                                      edge_punct(ocr_texts[rights[0]], False) if rights else None)
        for k in lefts:
            after[k] = to_left
        for k in rights:
            before[k] = to_right
    out = []
    for k, (s, e) in enumerate(spans):
        out.append((before[k] + graw[gmap[s]: gmap[e - 1] + 1] + after[k]).strip() if e > s else "")
    return out


def line_rows(gv, m, lines):
    """정렬된 줄마다: 배정된 정답 부분(정규화·원문), 등급, 일치 여부, 편집 연산."""
    g, gmap, graw = gv["norm"], gv["map"], gv["raw"]
    if m["kind"] == "single":
        li = m["lines"][0]
        t = lines[li]["norm"]
        s, e = m["span"]
        seg = t[s:e]
        ops = lev_ops(seg, g)
        extra = len(t) - (e - s)
        full = extra <= LENIENT_EDGE                       # 줄의 모든 글자가 정답과 정렬됨('및'·'장소' 같은 무관 글자 없음)
        edge_missing = bool(ops) and (ops[0][0] == "del" or ops[-1][0] == "del")
        tier = "C_partial" if not full else ("B_edge_missing" if edge_missing else "A_clean")
        return [{"li": li, "gold_norm": g, "text": graw.strip(), "clipped": graw.strip(), "ocr_seg": seg, "tier": tier,
                 "extra_chars": extra, "correct": t == g, "ops": ops}]
    # 조각은 정렬 중 찾은 정답 구간을 그대로 받는다. 어느 조각도 덮지 않은 정답 글자(미탐지·잘림)에 닿거나
    # 다른 조각과 겹치는 조각은 B 등급으로 둔다
    spans = m["spans"]
    cover = [0] * len(g)
    for s, e in spans:
        for x in range(s, e):
            cover[x] += 1
    ambiguous = set()
    for k, (s, e) in enumerate(spans):
        if any(cover[x] > 1 for x in range(s, e)):
            ambiguous.add(k)
        if (s > 0 and cover[s - 1] == 0) or (e < len(g) and cover[e] == 0):
            ambiguous.add(k)
    texts = piece_texts(graw, gmap, spans, [lines[li]["text"] for li in m["lines"]])
    rows = []
    for k, li in enumerate(m["lines"]):
        s, e = spans[k]
        gseg = g[s:e]
        t = lines[li]["norm"]
        pops = lev_ops(t, gseg)
        lead = next((i for i, op in enumerate(pops) if op[0] != "ins"), len(pops))
        trail = next((i for i, op in enumerate(reversed(pops)) if op[0] != "ins"), len(pops))
        extra = sum(1 for op in pops if op[0] == "ins")
        full = lead <= LENIENT_EDGE and trail <= LENIENT_EDGE and e > s   # 가장자리의 정답 밖 OCR 글자는 대개 라벨 없는 글자
        tier = "C_partial" if not full else ("B_edge_missing" if k in ambiguous else "A_clean")
        rows.append({"li": li, "gold_norm": gseg, "text": texts[k], "clipped": graw[gmap[s]: gmap[e - 1] + 1] if e > s else "",
                     "ocr_seg": t, "tier": tier, "extra_chars": extra, "correct": t == gseg, "ops": pops})
    return rows


# ------------------------------------------------------------------ 자모 혼동
CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
PARTS = ("초성", "중성", "종성")
# 일부러 틀린 제안을 심을 때 쓰는 닮은 자모(눈으로 헷갈릴 만한 짝)
LOOKALIKE = (
    {"ㄱ": "ㅋㄲ", "ㅋ": "ㄱ", "ㄲ": "ㄱ", "ㄷ": "ㅌㄸ", "ㅌ": "ㄷ", "ㄸ": "ㄷ", "ㅂ": "ㅍㅃ", "ㅍ": "ㅂ", "ㅃ": "ㅂ", "ㅈ": "ㅊㅉ",
     "ㅊ": "ㅈ", "ㅉ": "ㅈ", "ㅅ": "ㅆ", "ㅆ": "ㅅ", "ㄴ": "ㄹ", "ㄹ": "ㄴ", "ㅇ": "ㅎ", "ㅎ": "ㅇ", "ㅁ": "ㅂ"},
    {"ㅏ": "ㅑㅓ", "ㅑ": "ㅏ", "ㅓ": "ㅕㅏ", "ㅕ": "ㅓ", "ㅗ": "ㅛㅜ", "ㅛ": "ㅗ", "ㅜ": "ㅠㅗ", "ㅠ": "ㅜ", "ㅐ": "ㅔ", "ㅔ": "ㅐ",
     "ㅡ": "ㅜ", "ㅣ": "ㅏ", "ㅒ": "ㅖ", "ㅖ": "ㅒ", "ㅘ": "ㅝ", "ㅝ": "ㅘ", "ㅙ": "ㅞ", "ㅞ": "ㅙ", "ㅚ": "ㅟ", "ㅟ": "ㅚ", "ㅢ": "ㅡ"},
    {"ㄴ": "ㅇㄹ", "ㅇ": "ㄴㅁ", "ㅁ": "ㅇ", "ㄹ": "ㄴ", "ㄱ": "ㅇ", "ㅂ": "ㅁ", "ㅅ": "ㄷ", "ㄷ": "ㅅ", "ㅆ": "ㅅ", "ㅈ": "ㅊ",
     "ㅊ": "ㅈ", "ㅌ": "ㄷ", "ㅍ": "ㅂ", "ㅎ": "ㅇ", "ㄲ": "ㄱ", "ㅋ": "ㄱ"},
)


def jamo(ch):
    o = ord(ch) - 0xAC00
    if 0 <= o < 11172:
        return CHO[o // 588], JUNG[(o % 588) // 28], JONG[o % 28]
    return None


def confusion_kind(g, o):
    a, b = jamo(g), jamo(o)
    if a and b:
        return "한글 " + "+".join(n for n, x, y in zip(PARTS, a, b) if x != y) + " 다름"
    if a or b:
        return "한글<->비한글"
    if g.isdigit() or o.isdigit():
        return "숫자/영문"
    return "기타"


def plant(text, rng, avoid=()):
    """한 음절의 자모 하나를 닮은 자모로 바꾼 전사(검토 주의 확인용). 바꿀 한글 음절이 없거나 결과가 avoid key와 같으면 None."""
    spots = [i for i, ch in enumerate(text) if jamo(ch)]
    rng.shuffle(spots)
    for i in spots:
        o = ord(text[i]) - 0xAC00
        parts = [o // 588, (o % 588) // 28, o % 28]
        slots = [0, 1] + ([2] if parts[2] else [])
        rng.shuffle(slots)
        for slot in slots:
            table = (CHO, JUNG, JONG)[slot]
            choices = list(LOOKALIKE[slot].get(table[parts[slot]], ""))
            rng.shuffle(choices)
            for c in choices:
                new = list(parts)
                new[slot] = table.index(c)
                ch = chr(0xAC00 + new[0] * 588 + new[1] * 28 + new[2])
                out = text[:i] + ch + text[i + 1:]
                if key(out) not in avoid:
                    return {"text": out, "detail": {"index": i, "from": text[i], "to": ch, "part": PARTS[slot]}}
    return None


def auroc(pos, neg):
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    i, rank_sum = 0, 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        rank_sum += sum((i + j + 1) / 2 for k in range(i, j) if allv[k][1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# ------------------------------------------------------------------ 줄 기하
def quad_size(polygon, bbox):
    """(가로, 세로): 사각형이면 위아래 변·좌우 변 길이 평균, 아니면 bbox."""
    if polygon and len(polygon) == 4:
        p = polygon
        d = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
        return (d(p[0], p[1]) + d(p[3], p[2])) / 2, (d(p[0], p[3]) + d(p[1], p[2])) / 2
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def height_bin(h):
    lo = max(b for b in HEIGHT_BINS if b <= h)
    hi = next((b for b in HEIGHT_BINS if b > h), None)
    return f"{lo}-{hi}" if hi is not None else f"{lo}+"


# ------------------------------------------------------------------ 생성
def build(engine, variant, gold=GOLD, runs=RUNS, out=OUT, manifest=MANIFEST, review_dir=REVIEW):
    """한 OCR 출처(<engine>-<variant>)의 줄 정답 초안을 쓰고 요약을 돌려준다."""
    source = f"{engine}-{variant}"
    target = Path(out) / source
    target.mkdir(parents=True, exist_ok=True)
    cases = load(manifest)["cases"]
    field_rows = collections.defaultdict(list)     # field -> [(split, cer, exact_sub, exact_equal, kind, human)]
    all_lines, records, conf_rows, missing, written = [], [], [], [], set()
    posters = collections.Counter()
    for case in cases:
        cid, split = case["id"], case["split"]
        gpath, opath = Path(gold) / f"{cid}.poster.json", Path(runs) / engine / cid / f"{variant}.json"
        if not gpath.exists() or not opath.exists():
            missing.append(cid)
            continue
        doc, ocr = load(gpath), load(opath)
        rpath = Path(review_dir) / f"{cid}.json"
        human = human_fields(doc, load(rpath) if rpath.exists() else None)
        posters[split] += 1
        lines = []
        for r in ocr["records"]:
            w, h = quad_size(r.get("polygon"), r["bbox_xyxy"])
            lines.append({"id": r["id"], "text": r["text"], "norm": norm_map(r["text"])[0], "conf": r.get("confidence"),
                          "bbox": r["bbox_xyxy"], "polygon": r.get("polygon"), "height": round(h, 2), "width": round(w, 2)})
        best = [None] * len(lines)
        fields_of = [set() for _ in lines]
        for gv in gold_values(doc, human):
            m = match_value(gv["norm"], lines)
            if m is None:
                field_rows[gv["field"]].append((split, 1.0, False, False, None, gv["human"]))
                continue
            exact_sub = m["cost"] == 0
            exact_equal = exact_sub and "".join(lines[li]["norm"] for li in m["lines"]) == gv["norm"]
            field_rows[gv["field"]].append((split, m["cer"], exact_sub, exact_equal, m["kind"], gv["human"]))
            if m["cer"] > ACCEPT:
                continue
            for row in line_rows(gv, m, lines):
                li = row["li"]
                fields_of[li].add(gv["field"])
                # 줄마다 가장 좋은 라벨: 등급 A < B < C, 같은 등급이면 OCR 일치, 그다음 필드 순서
                cand = (TIER_RANK[row["tier"]], not row["correct"], TAG_PRIORITY.index(gv["field"]))
                if best[li] is None or cand < best[li][0]:
                    best[li] = (cand, row, gv, m)
        case_records = []
        for li, ln in enumerate(lines):
            all_lines.append({"split": split, "height": ln["height"], "alnum": bool(ln["norm"]), "matched": best[li] is not None})
            if best[li] is None:
                continue
            _, row, gv, m = best[li]
            rec = {"id": f"{cid}.{ln['id']}", "case_id": cid, "split": split,
                   "ocr_ref": {"engine": engine, "variant": variant, "ocr_id": ln["id"]},
                   "polygon": ln["polygon"], "bbox": ln["bbox"], "height": ln["height"], "small_text": ln["height"] < SMALL_PX,
                   "text": unicodedata.normalize("NFC", row["text"]), "text_key": key(row["text"]),
                   "ocr_text": ln["text"], "confidence": ln["conf"], "field": gv["field"], "field_group": FIELD_GROUP[gv["field"]],
                   "human_checked": gv["human"], "tier": row["tier"], "line_correct": row["correct"],
                   "fields": sorted(fields_of[li], key=TAG_PRIORITY.index), "match_kind": m["kind"],
                   "value_cer": round(m["cer"], 4), "gold_value": gv["raw"], "extra_chars": row["extra_chars"],
                   "source": "auto_align", "review_status": "unreviewed"}
            case_records.append(rec)
            records.append((rec, row))
            if row["tier"] == "A_clean" and not row["correct"]:
                for op, ai, bi in row["ops"]:
                    if op == "eq":
                        continue
                    g_ch = row["gold_norm"][bi] if bi is not None else None
                    o_ch = row["ocr_seg"][ai] if ai is not None else None
                    conf_rows.append({"line_id": rec["id"], "case_id": cid, "split": split, "field": rec["field"],
                                      "field_group": rec["field_group"], "human_checked": rec["human_checked"], "op": op,
                                      "gold": g_ch, "ocr": o_ch,
                                      "kind": confusion_kind(g_ch, o_ch) if op == "sub" else ("누락" if op == "del" else "추가"),
                                      "gold_index": bi, "text": rec["text"], "ocr_text": ln["text"], "confidence": ln["conf"]})
        payload = {"case_id": cid, "split": split, "source": source, "engine": engine, "variant": variant, "gold": Path(gold).name,
                   "image": {"width": (ocr.get("normalized_dimensions") or [None, None])[0],
                             "height": (ocr.get("normalized_dimensions") or [None, None])[1]},
                   "coordinate_space": "normalized_original_pixels", "lines_total": len(lines),
                   "lines_matched": len(case_records), "records": case_records}
        name = f"{cid}.lines.json"
        (target / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        written.add(name)
    for stale in target.glob("*.lines.json"):
        if stale.name not in written:
            stale.unlink()  # 이번 생성에 없는 사례(정답·OCR 파일이 사라짐)의 옛 초안
    summary = summarize(source, engine, variant, Path(gold).name, posters, missing, all_lines, records, field_rows, conf_rows)
    with (target / "confusions.jsonl").open("w", encoding="utf-8") as fh:
        for r in conf_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def tier_counts(recs):
    c = collections.Counter(r["tier"] for r in recs)
    return {"lines_matched": len(recs), "tiers": {t: c[t] for t in TIER_RANK},
            "A_correct": sum(1 for r in recs if r["tier"] == "A_clean" and r["line_correct"]),
            "A_incorrect": sum(1 for r in recs if r["tier"] == "A_clean" and not r["line_correct"]),
            "B_correct": sum(1 for r in recs if r["tier"] == "B_edge_missing" and r["line_correct"])}


def summarize(source, engine, variant, gold_name, posters, missing, all_lines, records, field_rows, conf_rows):
    recs = [r for r, _ in records]
    med = lambda xs: round(statistics.median(xs), 4) if xs else None
    fields = {}
    for f in TAG_PRIORITY:
        rows, mine = field_rows.get(f, []), [r for r in recs if r["field"] == f]
        fields[f] = {"field_group": FIELD_GROUP[f], "human_checked_values": sum(1 for r in rows if r[5]),
                     "gold_present": len(rows), "auto_matched": sum(1 for r in rows if r[1] <= ACCEPT),
                     "exact": sum(1 for r in rows if r[3]), "exact_substring": sum(1 for r in rows if r[2]),
                     "median_cer": med([r[1] for r in rows]),
                     "multi_line": sum(1 for r in rows if r[1] <= ACCEPT and r[4] == "multi"),
                     "by_split": {s: {"gold_present": sum(1 for r in rows if r[0] == s),
                                      "auto_matched": sum(1 for r in rows if r[0] == s and r[1] <= ACCEPT),
                                      "exact": sum(1 for r in rows if r[0] == s and r[3])} for s in sorted(posters)},
                     **tier_counts(mine)}
    groups = {g: tier_counts([r for r in recs if r["field_group"] == g]) for g in GROUP_LABEL}
    splits = {}
    for s in sorted(posters):
        ls = [l for l in all_lines if l["split"] == s]
        splits[s] = {"posters": posters[s], "lines_total": len(ls), **tier_counts([r for r in recs if r["split"] == s])}
    conf_c = [r["confidence"] for r in recs if r["tier"] == "A_clean" and r["line_correct"] and r["confidence"] is not None]
    conf_i = [r["confidence"] for r in recs if r["tier"] == "A_clean" and not r["line_correct"] and r["confidence"] is not None]
    bins = {}
    for l in all_lines:
        b = bins.setdefault(height_bin(l["height"]), {"lines": 0, "matched": 0, "A_correct": 0, "A_incorrect": 0})
        b["lines"] += 1
        b["matched"] += l["matched"]
    for r in recs:
        if r["tier"] == "A_clean":
            bins[height_bin(r["height"])]["A_correct" if r["line_correct"] else "A_incorrect"] += 1
    order = [height_bin(b) for b in HEIGHT_BINS]
    hist = [{"range": name, **bins.get(name, {"lines": 0, "matched": 0, "A_correct": 0, "A_incorrect": 0})} for name in order]
    for h in hist:
        n = h["A_correct"] + h["A_incorrect"]
        h["A_accuracy"] = round(h["A_correct"] / n, 4) if n else None
    subs = [c for c in conf_rows if c["op"] == "sub"]
    pairs = collections.Counter((c["gold"], c["ocr"]) for c in subs)
    pair_posters = collections.defaultdict(set)
    example = {}
    for c in subs:
        pair_posters[(c["gold"], c["ocr"])].add(c["case_id"])
        example.setdefault((c["gold"], c["ocr"]), c)
    top = [{"gold": g, "ocr": o, "count": n, "posters": len(pair_posters[(g, o)]), "kind": confusion_kind(g, o),
            "example": {k: example[(g, o)][k] for k in ("line_id", "field", "text", "ocr_text")}}
           for (g, o), n in pairs.most_common(40)]
    fingerprint = hashlib.sha256("\n".join(f"{r['id']}\t{r['text']}\t{r['tier']}" for r in recs).encode("utf-8")).hexdigest()[:16]
    return {
        "source": source, "engine": engine, "variant": variant, "gold": gold_name, "built_at": now(), "fingerprint": fingerprint,
        "posters": sum(posters.values()), "missing": missing,
        "thresholds": {"accept_cer": ACCEPT, "piece_max": PIECE_MAX, "min_gold": MIN_GOLD, "min_piece": MIN_PIECE,
                       "lenient_edge": LENIENT_EDGE, "one_char_pieces": ONE_CHAR_PIECES, "small_text_px": SMALL_PX},
        "rules": RULES, "group_labels": GROUP_LABEL,
        "lines": {"lines_total": len(all_lines), "lines_with_alnum": sum(1 for l in all_lines if l["alnum"]),
                  "unmatched_lines_with_alnum": sum(1 for l in all_lines if l["alnum"] and not l["matched"]),
                  **tier_counts(recs),
                  "human_checked": {"true": tier_counts([r for r in recs if r["human_checked"]]),
                                    "false": tier_counts([r for r in recs if not r["human_checked"]])},
                  "small_text": sum(1 for r in recs if r["small_text"]),
                  "conf_mean_correct": round(sum(conf_c) / len(conf_c), 4) if conf_c else None,
                  "conf_mean_incorrect": round(sum(conf_i) / len(conf_i), 4) if conf_i else None,
                  "conf_auroc_correct_vs_incorrect": round(auroc(conf_c, conf_i), 4) if conf_c and conf_i else None,
                  "text_edge_punct_kept": sum(1 for r, row in records if row["text"] != row["clipped"]),
                  "key_disagrees_line_correct": sum(1 for r in recs if (r["text_key"] == key(r["ocr_text"])) != r["line_correct"]),
                  "posters_with_any_matched_line": len({r["case_id"] for r in recs})},
        "splits": splits, "fields": fields, "groups": groups,
        "small_text": {"threshold_px": SMALL_PX, "matched_small": sum(1 for r in recs if r["small_text"]),
                       "all_small": sum(1 for l in all_lines if l["height"] < SMALL_PX), "histogram": hist},
        "confusions": {"scope": "A_clean 등급 OCR 불일치 줄만(줄마다 고른 라벨 1개)",
                       "lines": len({c["line_id"] for c in conf_rows}),
                       "ops": dict(collections.Counter(c["op"] for c in conf_rows)),
                       "by_kind": dict(collections.Counter(c["kind"] for c in subs).most_common()), "top_pairs": top},
    }


def discover(runs=RUNS, manifest=MANIFEST):
    """<runs>/<engine>/<case>/<variant>.json이 하나라도 있는 (engine, variant). 이름이 -tmp로 끝나는 임시 폴더는 뺀다."""
    ids = {c["id"] for c in load(manifest)["cases"]}
    found = set()
    for engine in sorted(p for p in Path(runs).iterdir() if p.is_dir() and not p.name.endswith("-tmp")):
        for case_dir in engine.iterdir():
            if case_dir.is_dir() and case_dir.name in ids:
                found.update((engine.name, f.stem) for f in case_dir.glob("*.json"))
    return sorted(found)


# ------------------------------------------------------------------ 검토 흐름
_LOCK = threading.RLock()
_LINES = {}


def source_dir(source, out=OUT):
    if not re.fullmatch(r"[a-z0-9_]+(?:[+-][a-z0-9_]+)*", str(source or "")):
        raise ValueError("알 수 없는 출처 이름")
    path = Path(out) / source
    if not (path / "summary.json").exists():
        raise ValueError(f"{source}: 줄 정답 초안이 없습니다. 먼저 build를 실행하세요.")
    return path


def sources(out=OUT):
    """생성된 출처 목록과 줄 요약(대시보드 선택 목록용)."""
    res = []
    for path in sorted(Path(out).glob("*/summary.json")):
        s = load(path)
        res.append({"source": s["source"], "engine": s["engine"], "variant": s["variant"], "posters": s["posters"],
                    "built_at": s["built_at"], "has_queue": (path.parent / "queue.json").exists(),
                    **{k: s["lines"][k] for k in ("lines_total", "lines_matched", "tiers", "A_correct", "A_incorrect")}})
    return res


def load_lines(source, out=OUT):
    """출처의 줄 기록 {line_id: record}(사례·OCR 순서). summary.json이 그대로면 다시 읽지 않는다."""
    folder = source_dir(source, out)
    stamp = (folder / "summary.json").stat().st_mtime_ns
    hit = _LINES.get(str(folder))
    if hit and hit[0] == stamp:
        return hit[1]
    lines = {}
    for path in sorted(folder.glob("*.lines.json")):
        for r in load(path)["records"]:
            lines[r["id"]] = r
    _LINES[str(folder)] = (stamp, lines)
    return lines


def make_queue(source, out=OUT, sample=SAMPLE, seed=SEED, rate=PLANT_RATE):
    """큐 순서: A 불일치 → B 전부 → A 일치 표본(seed). 큐 항목의 rate 비율에 자모 하나를 바꾼 제안을 심는다."""
    lines = load_lines(source, out)
    order = list(lines.values())
    a_bad = [r for r in order if r["tier"] == "A_clean" and not r["line_correct"]]
    b_all = [r for r in order if r["tier"] == "B_edge_missing"]
    a_ok = [r for r in order if r["tier"] == "A_clean" and r["line_correct"]]
    picked = {r["id"] for r in random.Random(seed).sample(a_ok, min(sample, len(a_ok)))}
    items = [{"line_id": r["id"], "segment": seg, "case_id": r["case_id"], "original": r["text"]}
             for seg, rs in zip(SEGMENTS, (a_bad, b_all, [r for r in a_ok if r["id"] in picked])) for r in rs]
    eligible = []
    for i, it in enumerate(items):
        p = plant(it["original"], random.Random(f"{seed}:{it['line_id']}"),
                  avoid={key(it["original"]), key(lines[it["line_id"]]["ocr_text"])})
        if p:
            eligible.append((i, p))
    chosen = dict(random.Random(seed + 1).sample(eligible, min(round(rate * len(items)), len(eligible))))
    for i, it in enumerate(items):
        p = chosen.get(i)
        it.update(proposal=p["text"] if p else it["original"], planted=p is not None, plant=p["detail"] if p else None)
    summary = load(source_dir(source, out) / "summary.json")
    return {"source": source, "created_at": now(), "seed": seed, "sample": sample, "plant_rate": rate,
            "fingerprint": summary.get("fingerprint"), "segments": dict(collections.Counter(it["segment"] for it in items)),
            "planted_total": len(chosen), "items": items}


def _queue_data(source, out=OUT):
    path = source_dir(source, out) / "queue.json"
    if not path.exists():
        raise ValueError(f"{source}: 검토 큐가 없습니다. 먼저 queue()를 부르세요.")
    return load(path)


def _latest(source, out=OUT):
    """review.jsonl의 줄별 마지막 결정(나중에 쓴 줄이 이긴다)."""
    path = source_dir(source, out) / "review.jsonl"
    latest = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                latest[row["line_id"]] = row
    return latest


def client_item(position, item, rec, review, show_ocr=True):
    """화면에 보내는 큐 항목. 심은 여부·원래 제안·큐 구간·정답 값 전체(gold_value)·line_correct는 보내지 않는다.
    show_ocr=False면 OCR 문자열·신뢰도도 뺀다(A 일치 표본에서는 OCR 문자열이 원래 제안과 같아 심은 항목이 드러날 수 있음)."""
    item_out = {"position": position, "line_id": item["line_id"], "case_id": rec["case_id"], "split": rec["split"],
                "field": rec["field"], "field_group": rec["field_group"], "field_label": GROUP_LABEL[rec["field_group"]],
                "human_checked": rec["human_checked"], "tier": rec["tier"], "text": item["proposal"],
                "polygon": rec["polygon"], "bbox": rec["bbox"], "height": rec["height"], "small_text": rec["small_text"],
                "review": {k: review[k] for k in ("decision", "text", "reviewer", "note", "reviewed_at")} if review else None}
    if show_ocr:
        item_out.update(ocr_text=rec["ocr_text"], confidence=rec["confidence"])
    return item_out


def queue(source, out=OUT, sample=SAMPLE, seed=SEED, rate=PLANT_RATE, rebuild=False, show_ocr=True):
    """검토 큐(화면용 항목 목록). queue.json이 없거나 rebuild면 새로 만들고, 있으면 그대로 쓴다(심은 항목이 바뀌지 않도록)."""
    with _LOCK:
        path = source_dir(source, out) / "queue.json"
        if rebuild or not path.exists():
            path.write_text(json.dumps(make_queue(source, out, sample, seed, rate), ensure_ascii=False, indent=1), encoding="utf-8")
        data = load(path)
    lines, latest = load_lines(source, out), _latest(source, out)
    return [client_item(i, it, lines[it["line_id"]], latest.get(it["line_id"]), show_ocr)
            for i, it in enumerate(data["items"]) if it["line_id"] in lines]


def record(source, line_id, decision, text=None, reviewer=None, note=None, out=OUT):
    """검토 결정 하나를 review.jsonl에 덧붙인다. ok는 보여 준 제안을, fix는 고친 전사를, exclude는 빈 값을 남긴다."""
    if decision not in DECISIONS:
        raise ValueError("결정은 ok·fix·exclude 중 하나여야 합니다.")
    with _LOCK:
        items = {it["line_id"]: it for it in _queue_data(source, out)["items"]}
        if line_id not in items:
            raise ValueError("검토 큐에 없는 줄입니다.")
        if decision == "fix":
            text = unicodedata.normalize("NFC", str(text or "")).strip()[:300]
            if not text:
                raise ValueError("fix에는 고친 전사가 필요합니다.")
        else:
            text = items[line_id]["proposal"] if decision == "ok" else None
        row = {"line_id": line_id, "decision": decision, "text": text,
               "reviewer": str(reviewer or "").strip()[:60] or None, "note": str(note or "").strip()[:1000] or None,
               "reviewed_at": now()}
        with (source_dir(source, out) / "review.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def review_stats(source, out=OUT):
    """구간별 검토·남은 수, 심은 제안 잡은 비율(fix·exclude면 잡음), 바뀐 제안 수."""
    data, latest = _queue_data(source, out), _latest(source, out)
    summary = load(source_dir(source, out) / "summary.json")
    blank = lambda: {"total": 0, "reviewed": 0, "remaining": 0, "ok": 0, "fix": 0, "exclude": 0, "changed": 0, "changed_key": 0}
    segments = {s: blank() for s in SEGMENTS}
    planted = {"total": 0, "reviewed": 0, "caught": 0, "restored": 0, "by_segment": {s: {"total": 0, "reviewed": 0, "caught": 0} for s in SEGMENTS}}
    changed = {"total": 0, "planted": 0, "genuine": 0, "genuine_key": 0}
    reviewers = collections.Counter()
    for it in data["items"]:
        seg, r = segments[it["segment"]], latest.get(it["line_id"])
        seg["total"] += 1
        p = planted["by_segment"][it["segment"]]
        if it["planted"]:
            planted["total"] += 1
            p["total"] += 1
        if not r:
            seg["remaining"] += 1
            continue
        seg["reviewed"] += 1
        seg[r["decision"]] += 1
        reviewers[r["reviewer"] or "이름 없음"] += 1
        is_changed = r["decision"] == "fix" and r["text"] != it["proposal"]
        if is_changed:
            seg["changed"] += 1
            seg["changed_key"] += key(r["text"]) != key(it["proposal"])
            changed["total"] += 1
            changed["planted" if it["planted"] else "genuine"] += 1
            if not it["planted"]:
                changed["genuine_key"] += key(r["text"]) != key(it["proposal"])
        if it["planted"]:
            planted["reviewed"] += 1
            p["reviewed"] += 1
            caught = r["decision"] in ("fix", "exclude")
            planted["caught"] += caught
            p["caught"] += caught
            planted["restored"] += r["decision"] == "fix" and key(r["text"]) == key(it["original"])
    planted["caught_rate"] = round(planted["caught"] / planted["reviewed"], 4) if planted["reviewed"] else None
    for seg in segments.values():
        seg["fix_rate"] = round(seg["fix"] / seg["reviewed"], 4) if seg["reviewed"] else None
    reviewed = sum(s["reviewed"] for s in segments.values())
    return {"source": source, "queue_total": len(data["items"]), "reviewed": reviewed, "remaining": len(data["items"]) - reviewed,
            "segments": segments, "planted": planted, "changed": changed, "reviewers": dict(reviewers),
            "stale": data.get("fingerprint") != summary.get("fingerprint")}


# ------------------------------------------------------------------ 잘라 보기(메모리 전용)
@functools.lru_cache(maxsize=4)
def _cases(path, stamp):
    return {c["id"]: c for c in load(path)["cases"]}


@functools.lru_cache(maxsize=8)
def _rgb(path, stamp):
    from .ocr_worker import normalized_rgb
    return normalized_rgb(path)


def _coeffs(dst, src):
    """출력 사각형 꼭짓점 dst → 원본 꼭짓점 src로 가는 PIL PERSPECTIVE 계수 8개."""
    import numpy as np
    a, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    return [float(v) for v in np.linalg.solve(np.array(a, dtype=float), np.array(b, dtype=float))]


def crop(case_id, polygon, pad=PAD, manifest=MANIFEST, min_height=64, max_width=1600):
    """원본을 ocr_worker.normalized_rgb로 정규화하고 4점 다각형을 원근 보정해 자른 PNG 바이트. 파일로 저장하지 않는다."""
    from PIL import Image
    if not re.fullmatch(r"(?:v2|r2)-\d{3}", str(case_id or "")):
        raise ValueError("알 수 없는 사례")
    cases = _cases(str(manifest), Path(manifest).stat().st_mtime_ns)
    if case_id not in cases:
        raise ValueError("매니페스트에 없는 사례")
    path = Path(cases[case_id]["image"])
    path = path if path.is_absolute() else ROOT / path
    pts = [(float(x), float(y)) for x, y in (polygon or [])]
    if len(pts) != 4 or not all(math.isfinite(v) for p in pts for v in p):
        raise ValueError("다각형은 유한한 꼭짓점 4개여야 합니다.")
    p0, p1, p2, p3 = pts
    if (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0]) < 0:
        p1, p3 = p3, p1  # 반시계 순서면 좌상→우상→우하→좌하로 돌린다
    w, h = quad_size([p0, p1, p2, p3], None)
    if w < 1 or h < 1:
        raise ValueError("다각형이 너무 작습니다.")
    unit = lambda vx, vy: (vx / (math.hypot(vx, vy) or 1), vy / (math.hypot(vx, vy) or 1))
    ux, uy = unit(p1[0] - p0[0] + p2[0] - p3[0], p1[1] - p0[1] + p2[1] - p3[1])   # 글자 진행 방향
    vx, vy = unit(p3[0] - p0[0] + p2[0] - p1[0], p3[1] - p0[1] + p2[1] - p1[1])   # 줄 높이 방향
    m = max(2.0, h * pad)
    src = [(p0[0] - (ux + vx) * m, p0[1] - (uy + vy) * m), (p1[0] + (ux - vx) * m, p1[1] + (uy - vy) * m),
           (p2[0] + (ux + vx) * m, p2[1] + (uy + vy) * m), (p3[0] - (ux - vx) * m, p3[1] - (uy - vy) * m)]
    scale = min(4.0, max(1.0, min_height / (h + 2 * m)), max_width / (w + 2 * m))  # 작은 글자는 키워서 보여 준다
    size = (max(1, round((w + 2 * m) * scale)), max(1, round((h + 2 * m) * scale)))
    dst = [(0, 0), (size[0], 0), (size[0], size[1]), (0, size[1])]
    rgb = _rgb(str(path), path.stat().st_mtime_ns)
    image = rgb.transform(size, Image.Transform.PERSPECTIVE, _coeffs(dst, src), Image.Resampling.BICUBIC, fillcolor="white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def crop_line(source, line_id, pad=PAD, out=OUT, manifest=MANIFEST):
    """줄 기록의 다각형으로 crop(). 화면이 좌표를 보내지 않아도 되게 서버 쪽에서 찾는다."""
    rec = load_lines(source, out).get(line_id)
    if rec is None:
        raise ValueError("알 수 없는 줄")
    return crop(rec["case_id"], rec["polygon"], pad, manifest)


# ------------------------------------------------------------------ CLI
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="줄 정답 초안 생성")
    b.add_argument("--engine", nargs="+", help="기본: runs 아래 모든 엔진")
    b.add_argument("--variant", nargs="+", help="기본: 있는 모든 변형")
    b.add_argument("--gold", type=Path, default=GOLD)
    b.add_argument("--runs", type=Path, default=RUNS)
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--manifest", type=Path, default=MANIFEST)
    b.add_argument("--review-dir", type=Path, default=REVIEW)
    for name, text in (("queue", "검토 큐 만들기·확인"), ("stats", "검토 현황")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--source", required=True, help="예: paddleocr-rgb_2x")
        p.add_argument("--out", type=Path, default=OUT)
        if name == "queue":
            p.add_argument("--rebuild", action="store_true", help="기존 queue.json을 버리고 다시 만든다(심은 항목이 바뀜)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "build":
        pairs = [(e, v) for e, v in discover(args.runs, args.manifest)
                 if (not args.engine or e in args.engine) and (not args.variant or v in args.variant)]
        if not pairs:
            raise SystemExit("맞는 OCR 실행 결과가 없습니다.")
        for engine, variant in pairs:
            s = build(engine, variant, args.gold, args.runs, args.out, args.manifest, args.review_dir)
            ln = s["lines"]
            print(f"{s['source']}: 포스터 {s['posters']}장(빠짐 {len(s['missing'])}), OCR 줄 {ln['lines_total']:,}개, 정답과 맞춘 줄 "
                  f"{ln['lines_matched']:,}개 — A {ln['tiers']['A_clean']:,}(일치 {ln['A_correct']:,} / 불일치 {ln['A_incorrect']:,}), "
                  f"B {ln['tiers']['B_edge_missing']:,}, C {ln['tiers']['C_partial']:,}; 사람 확인 값 줄 "
                  f"{ln['human_checked']['true']['lines_matched']:,}개, 작은 글자 줄 {ln['small_text']:,}개, "
                  f"가장자리 부호 살림 {ln['text_edge_punct_kept']:,}개 → {Path(args.out) / s['source']}")
    elif args.command == "queue":
        items = queue(args.source, args.out, rebuild=args.rebuild)
        data = _queue_data(args.source, args.out)
        print(f"{args.source}: 검토 큐 {len(items)}개 {data['segments']}, 심은 제안 {data['planted_total']}개(화면에는 알리지 않음)")
    else:
        print(json.dumps(review_stats(args.source, args.out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
