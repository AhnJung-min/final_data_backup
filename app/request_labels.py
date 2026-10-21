"""요청 의도 분류(ED03·D03) 라벨 지원: 원문·라벨 결합, 전처리 점검, 그룹 단위 분할, 사람 검토 기록, TF-IDF 기준 모델.

입력(읽기 전용, 고치지 않음)
  data/collection/raw/synthetic/requests/requests.jsonl      요청 원문(정답 없음)
  data/collection/evidence/request-labels.jsonl              의도 라벨(DRAFT, 작성자 자체 검토만 거침)
  data/collection/evidence/request-production.jsonl          생성 메타데이터(표현 템플릿 포함)
출력
  data/collection/evidence/request-split.v1.json             그룹 단위 층화 분할 명세 + 전처리 점검 결과
  data/collection/evidence/request-label-review.jsonl        사람 검토 기록(덧붙이기만, 마지막 결정이 유효)
  data/collection/evidence/request-baseline.draft.json       문자 2-4gram TF-IDF + 다항 로지스틱 회귀(진단용)
라벨이 DRAFT인 동안 기준 모델 수치는 진단용이다. 독립 성능 근거나 모델 선정 근거로 쓰지 않는다.
  .\\.venv\\Scripts\\python.exe -m app.request_labels split|baseline|status|preprocess|record
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import re
import sys
import threading
import time
import unicodedata

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/collection/evidence"
TEXTS = ROOT / "data/collection/raw/synthetic/requests/requests.jsonl"
LABELS = EVIDENCE / "request-labels.jsonl"
PRODUCTION = EVIDENCE / "request-production.jsonl"
SPLIT_OUT = EVIDENCE / "request-split.v1.json"
REVIEW_LOG = EVIDENCE / "request-label-review.jsonl"
BASELINE_OUT = EVIDENCE / "request-baseline.draft.json"
BASELINE_EFFECTIVE_OUT = EVIDENCE / "request-baseline.effective.json"
INTENTS = ["new_inspection", "reinspection", "criteria_change", "status_query", "other"]
INTENT_NAMES = {"new_inspection": "신규 검수", "reinspection": "재검수", "criteria_change": "기준 변경",
                "status_query": "상태 조회", "other": "기타"}
DECISIONS = {"approve": "approved", "relabel": "relabelled", "flag": "flagged"}
STATES = ["approved", "relabelled", "flagged", "pending"]
LABEL_STATUS = {"approved": "human_approved", "relabelled": "human_relabelled", "flagged": "flagged", "pending": "draft"}
SPLITS = ("train", "validation", "test")
SEED = 20261021
EVAL_SHARE = {"validation": 0.2, "test": 0.2}  # 의도별 그룹 비율(10그룹 → train 6·validation 2·test 2)
NEAR = 0.8  # 문자 3-gram Jaccard 근접 중복 기준
L2_GRID = (1e-4, 1e-3, 1e-2)
ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")
SPACE = re.compile(r"\s+")
TOKEN = re.compile(r"\w+")
PLACEHOLDER = re.compile(r"\{context\}|\{action\}")
DRAFT_WARNING = ("DRAFT 라벨(작성자 자체 검토, 사람 검토 대기) 기준 진단 결과다. "
                 "독립 성능 근거나 모델 선정 근거로 쓰지 않는다.")
TEMPLATE_CAVEAT = ("합성 요청은 의도마다 같은 표현 템플릿 문구(예: '보완본 확인을 요청합니다.')를 그 의도의 모든 그룹에 붙여 만들었다. "
                   "그룹 단위 분할로 같은 상황은 갈라지지 않지만 템플릿 문구는 train·validation·test에 모두 들어가므로 "
                   "원문(raw) 점수는 템플릿 암기로 부풀려진다. 템플릿 문구를 지운 변형(template_stripped) 점수를 함께 본다.")
_LOCK = threading.Lock()


# ---------- 공통 ----------

def rel(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_jsonl(path):
    """(행 목록, 읽지 못한 줄 번호 목록). 빈 줄은 건너뛴다."""
    rows, bad = [], []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad.append(number)
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad.append(number)
    return rows, bad


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def ordered(values):
    """의도 이름을 INTENTS 순서로, 모르는 값은 뒤에 사전순으로(None은 뺀다)."""
    values = {v for v in values if v is not None}
    return [i for i in INTENTS if i in values] + sorted(values - set(INTENTS))


def rank(intent):
    return INTENTS.index(intent) if intent in INTENTS else len(INTENTS)


def _num(x):
    return int(x) if float(x).is_integer() else round(float(x), 1)


def describe(values):
    if not values:
        return {"n": 0}
    a = np.asarray(values, dtype=float)
    q = np.percentile(a, [25, 50, 75])
    return {"n": len(a), "min": _num(a.min()), "p25": round(float(q[0]), 1), "median": round(float(q[1]), 1),
            "mean": round(float(a.mean()), 1), "p75": round(float(q[2]), 1), "max": _num(a.max()), "std": round(float(a.std()), 1)}


# ---------- 결합·전처리 ----------

def preprocess(text):
    """NFC 정규화, 폭 없는 문자 제거, 공백류(탭·줄바꿈·NBSP·전각 공백)를 한 칸으로 줄이고 앞뒤 공백 제거."""
    text = ZERO_WIDTH.sub("", unicodedata.normalize("NFC", str(text or "")))
    return SPACE.sub(" ", text).strip()


def loose_key(text):
    """느슨한 중복 비교용: NFKC·대소문자 무시·글자와 숫자만."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def wrappers(template):
    """표현 템플릿에서 {context}·{action}을 뺀 고정 문구 목록."""
    return [p for p in (preprocess(part) for part in PLACEHOLDER.split(template or "")) if p]


def strip_template(text, template):
    """요청 문장에서 템플릿 고정 문구를 지운 핵심 문장(상황+요청 행동)과 못 찾은 문구 목록."""
    missing = []
    for phrase in wrappers(template):
        if phrase in text:
            text = text.replace(phrase, " ", 1)
        else:
            missing.append(phrase)
    return preprocess(text), missing


def load(labels=LABELS, texts=TEXTS, production=PRODUCTION):
    """원문·라벨·생성 메타데이터를 request_id로 묶는다(원문∩라벨). 빠진 id·그룹 불일치·중복 id는 issues에 남긴다."""
    sources = {"texts": texts, "labels": labels, "production": production}
    issues = {"missing_text": [], "missing_label": [], "missing_production": [], "group_mismatch": [], "unknown_intent": [],
              "template_not_matched": [], "duplicate_ids": {}, "invalid_lines": {}, "rows_without_id": {}}
    tables = {}
    for name, path in sources.items():
        if name == "production" and (path is None or not Path(path).exists()):
            tables[name] = None
            continue
        rows, bad = read_jsonl(path)
        if bad:
            issues["invalid_lines"][name] = bad
        no_id = sum(1 for r in rows if not r.get("request_id"))
        if no_id:
            issues["rows_without_id"][name] = no_id
        seen = Counter(r["request_id"] for r in rows if r.get("request_id"))
        if any(v > 1 for v in seen.values()):
            issues["duplicate_ids"][name] = sorted(k for k, v in seen.items() if v > 1)
        tables[name] = {r["request_id"]: r for r in rows if r.get("request_id")}  # 중복이면 마지막 줄
    text_t, label_t, prod_t = tables["texts"], tables["labels"], tables["production"]
    issues["missing_label"] = sorted(set(text_t) - set(label_t))
    issues["missing_text"] = sorted(set(label_t) - set(text_t))
    if prod_t is not None:
        issues["missing_production"] = sorted((set(text_t) | set(label_t)) - set(prod_t))
    out = []
    for rid in sorted(set(text_t) & set(label_t)):
        t, l, p = text_t[rid], label_t[rid], (prod_t or {}).get(rid) or {}
        group_ids = {"texts": t.get("request_group_id"), "labels": l.get("request_group_id"), "production": p.get("request_group_id")}
        if len({g for g in group_ids.values() if g}) > 1:
            issues["group_mismatch"].append({"request_id": rid, **group_ids})
        intent = l.get("intent_label")
        if intent not in INTENTS:
            issues["unknown_intent"].append(rid)
        raw = t.get("text") or ""
        text = preprocess(raw)
        template = p.get("paraphrase_template")
        core, missing = strip_template(text, template) if template else (text, [])
        if missing:
            issues["template_not_matched"].append({"request_id": rid, "phrases": missing})
        out.append({"request_id": rid, "request_group_id": l.get("request_group_id") or t.get("request_group_id"),
                    "intent": intent, "text": text, "text_changed": text != raw, "core_text": core, "template": template,
                    "variant": p.get("variant"), "situation": p.get("situation"), "case_group_id": t.get("case_group_id"),
                    "label_status": l.get("label_status"), "human_review_status": l.get("human_review_status")})
    by_intent = Counter(r["intent"] for r in out)
    counts = {"texts": len(text_t), "labels": len(label_t), "production": None if prod_t is None else len(prod_t),
              "joined": len(out), "groups": len({r["request_group_id"] for r in out}),
              "by_intent": {i: by_intent[i] for i in ordered(by_intent)}}
    ok = not any(v for v in issues.values())
    return {"rows": out, "issues": issues, "counts": counts, "ok": ok,
            "sources": {name: (rel(path) if path else None) for name, path in sources.items()}}


def shingles(text, n=3):
    text = text.casefold()
    return {text[i:i + n] for i in range(max(1, len(text) - n + 1))}


def jaccard_matrix(texts, n=3):
    """문자 n-gram 집합 Jaccard 행렬(이진 행렬 곱으로 한 번에)."""
    sets = [shingles(t, n) for t in texts]
    vocab = {g: i for i, g in enumerate(sorted(set().union(*sets)))} if sets else {}
    m = np.zeros((len(texts), len(vocab)), dtype=np.float32)
    for row, s in enumerate(sets):
        m[row, [vocab[g] for g in s]] = 1
    inter = m @ m.T
    size = m.sum(1)
    union = size[:, None] + size[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def duplicates(rows):
    """정규화 후 완전 중복, 느슨한 중복(문장부호·대소문자 무시), 템플릿 제거 후 핵심 문장 중복."""
    def groups_of(field):
        by = defaultdict(list)
        for r in rows:
            by[field(r)].append(r["request_id"])
        return [ids for ids in by.values() if len(ids) > 1]
    exact, loose, core = groups_of(lambda r: r["text"]), groups_of(lambda r: loose_key(r["text"])), groups_of(lambda r: r["core_text"])
    group_of = {r["request_id"]: r["request_group_id"] for r in rows}
    core_cross = [ids for ids in core if len({group_of[i] for i in ids}) > 1]
    return {"exact": {"sets": len(exact), "requests": sum(map(len, exact)), "examples": exact[:10]},
            "loose": {"sets": len(loose), "requests": sum(map(len, loose)), "examples": loose[:10]},
            "core_text": {"unique": len({r["core_text"] for r in rows}), "sets": len(core), "requests": sum(map(len, core)),
                          "cross_group_sets": len(core_cross), "cross_group_examples": core_cross[:10],
                          "note": "템플릿 고정 문구를 지우면 같은 그룹의 표현 변형이 같은 문장이 되는지 본다(실질 표본 수 = 고유 핵심 문장 수)."}}


def near_duplicates(rows, threshold=NEAR, split=None, top=20):
    """문자 3-gram Jaccard로 그룹 안·그룹 간 근접 중복을 센다. split(그룹→분할)이 있으면 분할 간 최대 유사도도 본다."""
    n = len(rows)
    if n < 2:
        return {"pairs": 0}
    sim = jaccard_matrix([r["text"] for r in rows])
    gid = np.array([r["request_group_id"] for r in rows])
    intent = np.array([r["intent"] for r in rows])
    iu = np.triu_indices(n, 1)
    vals = sim[iu]
    same = gid[iu[0]] == gid[iu[1]]
    edges = np.linspace(0, 1, 11)

    def summary(mask):
        v = vals[mask]
        if not v.size:
            return {"pairs": 0}
        return {"pairs": int(v.size), "mean": round(float(v.mean()), 4), "max": round(float(v.max()), 4),
                **{f"ge_{b}": int((v >= b).sum()) for b in (0.5, 0.7, threshold, 0.9)},
                "histogram": {"edges": [round(float(e), 1) for e in edges], "counts": np.histogram(v, bins=edges)[0].tolist()}}
    cross_idx = np.where(~same)[0]
    order = cross_idx[np.argsort(-vals[cross_idx], kind="stable")[:top]]
    other = sim.copy()
    other[gid[:, None] == gid[None, :]] = -1
    best = other.max(1)
    out = {"method": "문자 3-gram(공백 포함, 대소문자 무시) 집합 Jaccard", "threshold": threshold,
           "within_group": summary(same), "cross_group": summary(~same),
           "cross_group_same_intent": summary(~same & (intent[iu[0]] == intent[iu[1]])),
           "cross_group_other_intent": summary(~same & (intent[iu[0]] != intent[iu[1]])),
           "max_cross_group_per_request": describe([round(float(x) * 100, 1) for x in best]) | {"unit": "Jaccard×100"},
           "top_cross_group_pairs": [{"a": rows[iu[0][k]]["request_id"], "b": rows[iu[1][k]]["request_id"],
                                      "groups": [str(gid[iu[0][k]]), str(gid[iu[1][k]])],
                                      "intents": [str(intent[iu[0][k]]), str(intent[iu[1][k]])],
                                      "jaccard": round(float(vals[k]), 4)} for k in order]}
    if split:
        where = np.array([split.get(g) for g in gid])
        cross_split = where[iu[0]] != where[iu[1]]
        train = where == "train"
        held = np.where(where != "train")[0]
        to_train = sim[np.ix_(held, np.where(train)[0])].max(1) if held.size and train.any() else np.array([])
        out["cross_split"] = summary(cross_split) | {
            "held_out_max_vs_train": describe([round(float(x) * 100, 1) for x in to_train]) | {"unit": "Jaccard×100"},
            "held_out_ge_threshold_vs_train": int((to_train >= threshold).sum())}
    return out


def keyword_leakage(rows, min_df=5, min_groups=3, purity=0.95, top=30):
    """여러 그룹에 걸쳐 거의 한 의도에만 나오는 어절(공백 단위, 문장부호 제외) = 템플릿 누수 후보."""
    df, groups = defaultdict(Counter), defaultdict(set)
    size = Counter(r["intent"] for r in rows)
    template_tokens = defaultdict(set)
    tokens_of = {}
    for r in rows:
        toks = set(TOKEN.findall(r["text"].casefold()))
        tokens_of[r["request_id"]] = toks
        for t in toks:
            df[t][r["intent"]] += 1
            groups[t].add(r["request_group_id"])
        for phrase in wrappers(r["template"]):
            template_tokens[r["intent"]].update(TOKEN.findall(phrase.casefold()))
    leaky = []
    for t, c in df.items():
        total = sum(c.values())
        intent, k = c.most_common(1)[0]
        if total >= min_df and len(groups[t]) >= min_groups and k / total >= purity:
            leaky.append({"token": t, "intent": intent, "df": total, "groups": len(groups[t]), "purity": round(k / total, 4),
                          "coverage": round(k / size[intent], 4), "template_origin": t in template_tokens[intent]})
    leaky.sort(key=lambda x: (-x["groups"], -x["coverage"], -x["purity"], x["token"]))  # 여러 그룹에 퍼진 것(템플릿성)부터
    by_intent = {}
    for intent in ordered(size):
        mine = {x["token"] for x in leaky if x["intent"] == intent}
        mine_t = {x["token"] for x in leaky if x["intent"] == intent and x["template_origin"]}
        ids = [r["request_id"] for r in rows if r["intent"] == intent]
        by_intent[intent] = {"leaky_tokens": len(mine), "template_tokens": len(mine_t),
                             "requests_with_leaky_token": round(sum(bool(tokens_of[i] & mine) for i in ids) / len(ids), 4),
                             "requests_with_template_token": round(sum(bool(tokens_of[i] & mine_t) for i in ids) / len(ids), 4)}
    return {"method": f"어절 df≥{min_df}, 그룹 수≥{min_groups}, 한 의도 비율≥{purity}", "tokens": len(leaky),
            "template_origin_tokens": sum(x["template_origin"] for x in leaky), "by_intent": by_intent, "top": leaky[:top]}


def template_phrases(rows, split=None):
    """템플릿 고정 문구별 의도·그룹·분할 분포(의도 하나에만, 모든 분할에 나오면 누수)."""
    stats = defaultdict(lambda: {"intents": Counter(), "groups": set(), "splits": set()})
    for r in rows:
        for phrase in wrappers(r["template"]):
            s = stats[phrase]
            s["intents"][r["intent"]] += 1
            s["groups"].add(r["request_group_id"])
            if split:
                s["splits"].add(split.get(r["request_group_id"]))
    out = []
    for phrase, s in stats.items():
        intent, k = s["intents"].most_common(1)[0]
        total = sum(s["intents"].values())
        item = {"phrase": phrase, "intent": intent, "count": total, "purity": round(k / total, 4), "groups": len(s["groups"])}
        if split:
            item["splits"] = [x for x in SPLITS if x in s["splits"]]
        out.append(item)
    out.sort(key=lambda x: (rank(x["intent"]), str(x["intent"]), -x["count"], x["phrase"]))
    return out


def preprocess_report(data=None, threshold=NEAR, split=None):
    """D03 전처리 점검: 정규화 변경 수, 길이 통계, 중복·근접 중복, 어절 누수, 템플릿 문구."""
    data = data or load()
    rows = data["rows"]
    edges = list(range(0, (max((len(r["text"]) for r in rows), default=0) // 10 + 2) * 10, 10))
    hist = np.histogram([len(r["text"]) for r in rows], bins=edges)[0].tolist() if rows else []
    phrases = template_phrases(rows, split)
    return {"rows": len(rows),
            "normalization": {"method": "NFC + 폭 없는 문자 제거 + 공백류를 한 칸으로 + 앞뒤 공백 제거",
                              "changed": sum(r["text_changed"] for r in rows)},
            "lengths": {"chars": describe([len(r["text"]) for r in rows]), "eojeol": describe([len(r["text"].split()) for r in rows]),
                        "core_chars": describe([len(r["core_text"]) for r in rows]),
                        "chars_by_intent": {i: describe([len(r["text"]) for r in rows if r["intent"] == i]) for i in ordered(r["intent"] for r in rows)},
                        "histogram": {"bin_width": 10, "edges": edges, "counts": hist}},
            "duplicates": duplicates(rows),
            "near_duplicates": near_duplicates(rows, threshold, split),
            "keyword_leakage": keyword_leakage(rows),
            "template_phrases": {"phrases": len(phrases), "single_intent": sum(p["purity"] == 1 for p in phrases),
                                 "in_all_splits": sum(p.get("splits") == list(SPLITS) for p in phrases) if split else None,
                                 "items": phrases}}


# ---------- 분할 ----------

def make_split(data=None, seed=SEED, shares=EVAL_SHARE):
    """그룹 단위, 의도별 층화 분할. 그룹 안 의도가 둘 이상이거나 라벨이 없으면 멈춘다."""
    data = data or load()
    rows = data["rows"]
    if any(not r["intent"] for r in rows):
        raise ValueError("의도 라벨이 없는 요청이 있습니다: " + ", ".join(r["request_id"] for r in rows if not r["intent"])[:300])
    group_intents, members = defaultdict(set), defaultdict(list)
    for r in rows:
        group_intents[r["request_group_id"]].add(r["intent"])
        members[r["request_group_id"]].append(r["request_id"])
    conflicts = [{"request_group_id": g, "intents": sorted(v)} for g, v in sorted(group_intents.items()) if len(v) > 1]
    if conflicts:
        raise ValueError(f"한 그룹에 의도가 둘 이상입니다(층화 불가): {conflicts[:5]}")
    by_intent = defaultdict(list)
    for g, v in sorted(group_intents.items()):
        by_intent[next(iter(v))].append(g)
    rng = random.Random(seed)
    assign = {}
    for intent in ordered(by_intent):
        groups = sorted(by_intent[intent])
        rng.shuffle(groups)
        n_val = math.floor(len(groups) * shares["validation"] + 0.5)
        n_test = min(math.floor(len(groups) * shares["test"] + 0.5), max(0, len(groups) - n_val - 1))
        for k, g in enumerate(groups):
            assign[g] = "validation" if k < n_val else "test" if k < n_val + n_test else "train"
    intent_of = {g: next(iter(v)) for g, v in group_intents.items()}
    splits = {}
    for name in SPLITS:
        gs = sorted(g for g, s in assign.items() if s == name)
        ids = sorted(rid for g in gs for rid in members[g])
        splits[name] = {"groups": gs, "request_ids": ids, "counts": {
            "groups": len(gs), "requests": len(ids),
            "by_intent": {i: {"groups": sum(intent_of[g] == i for g in gs), "requests": sum(len(members[g]) for g in gs if intent_of[g] == i)}
                          for i in ordered(by_intent)}}}
    return {"seed": seed, "unit": "request_group_id", "method": "의도별로 그룹 id를 정렬한 뒤 random.Random(seed)로 섞고(의도 순서대로 같은 난수열 사용) 앞에서부터 validation·test·train에 배정",
            "shares": {"validation": shares["validation"], "test": shares["test"], "train": round(1 - shares["validation"] - shares["test"], 4)},
            "group_intent_check": {"groups": len(group_intents), "single_intent": True, "conflicts": []},
            "group_to_split": dict(sorted(assign.items())), "splits": splits}


def split_document(seed=SEED, labels=LABELS, texts=TEXTS, production=PRODUCTION, threshold=NEAR):
    """분할 명세 전체(입력 해시·분할·전처리 점검·누수 점검)를 만든다. 파일로 쓰지 않는다."""
    data = load(labels, texts, production)
    spec = make_split(data, seed)
    report = preprocess_report(data, threshold, spec["group_to_split"])
    inputs = {name: {"path": rel(path), "sha256": sha256(path), "rows": count}
              for name, path, count in (("labels", labels, data["counts"]["labels"]), ("texts", texts, data["counts"]["texts"]),
                                        ("production", production, data["counts"]["production"])) if path and Path(path).exists()}
    label_states = Counter(r["label_status"] for r in data["rows"])
    return {"schema_version": "1.0", "name": "request-split.v1", "generated_at": now(),
            "purpose": "요청 의도 분류(ED03) 학습·평가용 원본 그룹 단위 분할 명세(D03)",
            "inputs": inputs, "join": {"ok": data["ok"], "counts": data["counts"], "issues": data["issues"]},
            "label_source": {"path": rel(labels), "label_status": dict(label_states),
                             "human_review_status": dict(Counter(r["human_review_status"] for r in data["rows"])),
                             "note": "분할은 DRAFT 라벨의 그룹 의도로 층화했다. 사람 검토로 그룹 의도가 바뀌면 분할을 다시 만든다(v2)."},
            **spec, "preprocess": report,
            "notes": ["같은 request_group_id(한 상황의 5개 표현 변형)는 한 분할에만 들어간다.",
                      "근접 중복은 그룹 안에서만 높고 그룹 간에는 낮은지 near_duplicates로 확인한다.",
                      TEMPLATE_CAVEAT]}


def write_split(out=SPLIT_OUT, seed=SEED, labels=LABELS, texts=TEXTS, production=PRODUCTION):
    doc = split_document(seed, labels, texts, production)
    write_json(out, doc)
    return doc


def split_spec(path=SPLIT_OUT, compute_if_missing=True):
    """저장된 분할 명세. 파일이 없으면 메모리에서 계산해 persisted=False로 돌려준다(쓰지 않음)."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")) | {"persisted": True}
    return split_document() | {"persisted": False} if compute_if_missing else None


# ---------- 사람 검토 기록 ----------

def _replay(entries, draft):
    """검토 기록을 순서대로 적용한다. 마지막 결정이 유효, approve·relabel만 라벨을 바꾸고 flag는 상태만 바꾼다."""
    state = {rid: {"intent": d.get("intent_label"), "state": "pending", "last": None, "decisions": 0} for rid, d in draft.items()}
    ignored = 0
    for e in entries:
        s = state.get(e.get("request_id"))
        decision = e.get("decision")
        if s is None or decision not in DECISIONS or (decision in ("approve", "relabel") and e.get("intent") not in INTENTS):
            ignored += 1
            continue
        if decision in ("approve", "relabel"):
            s["intent"] = e["intent"]
        s.update(state=DECISIONS[decision], last=e, decisions=s["decisions"] + 1)
    return state, ignored


def record(request_id, decision, intent=None, reviewer="", note="", log=REVIEW_LOG, labels=LABELS):
    """검토 결정 한 건을 덧붙인다. approve=현재 라벨 확정, relabel=다른 의도로 변경, flag=보류(메모 필수, intent는 제안값)."""
    if decision not in DECISIONS:
        raise ValueError("decision은 approve·relabel·flag 중 하나입니다.")
    if intent is not None and intent not in INTENTS:
        raise ValueError(f"intent는 {', '.join(INTENTS)} 중 하나입니다.")
    reviewer, note = preprocess(reviewer)[:80], preprocess(note)[:1000]
    if not reviewer:
        raise ValueError("검토자 이름이 필요합니다.")
    draft = {r["request_id"]: r for r in read_jsonl(labels)[0] if r.get("request_id")}
    if request_id not in draft:
        raise ValueError(f"{request_id}: 라벨 파일에 없는 request_id입니다.")
    log = Path(log)
    with _LOCK:
        entries = read_jsonl(log)[0] if log.exists() else []
        current = _replay(entries, draft)[0][request_id]["intent"]
        if decision == "approve":
            if intent is not None and intent != current:
                raise ValueError(f"approve는 현재 라벨({current})을 확정합니다. 다른 의도로 바꾸려면 relabel을 쓰세요.")
            intent = current
        elif decision == "relabel":
            if intent is None:
                raise ValueError("relabel에는 바꿀 intent가 필요합니다.")
            if intent == current:
                raise ValueError(f"현재 라벨({current})과 같습니다. 확정은 approve를 쓰세요.")
        elif not note:
            raise ValueError("flag에는 보류 사유(note)가 필요합니다.")
        entry = {"schema_version": "1.0", "request_id": request_id, "request_group_id": draft[request_id].get("request_group_id"),
                 "decision": decision, "intent": intent, "previous_intent": current, "draft_intent": draft[request_id].get("intent_label"),
                 "reviewer": reviewer, "note": note, "reviewed_at": now()}
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def status(log=REVIEW_LOG, labels=LABELS, texts=TEXTS):
    """검토 현황: 상태별·의도별 건수, 요청별 유효 라벨(마지막 결정 우선, 검토 전은 draft), 그룹 의도 충돌."""
    draft = {r["request_id"]: r for r in read_jsonl(labels)[0] if r.get("request_id")}
    log = Path(log)
    entries, bad = read_jsonl(log) if log.exists() else ([], [])
    state, ignored = _replay(entries, draft)
    text_of = {}
    if texts and Path(texts).exists():
        text_of = {r["request_id"]: r["text"] for r in load(labels, texts, None)["rows"]}
    intents = ordered([d.get("intent_label") for d in draft.values() if d.get("intent_label")] + [s["intent"] for s in state.values() if s["intent"]])
    empty = lambda: {**{s: 0 for s in STATES}, "total": 0}
    by_intent, by_draft = {i: empty() for i in intents}, {i: empty() for i in intents}
    matrix = [[0] * len(intents) for _ in intents]
    requests, group_intents = [], defaultdict(set)
    for rid in sorted(draft):
        s, d = state[rid], draft[rid].get("intent_label")
        for table, key in ((by_intent, s["intent"]), (by_draft, d)):
            if key in table:
                table[key][s["state"]] += 1
                table[key]["total"] += 1
        if d in intents and s["intent"] in intents:
            matrix[intents.index(d)][intents.index(s["intent"])] += 1
        group_intents[draft[rid].get("request_group_id")].add(s["intent"])
        last = s["last"] or {}
        requests.append({"request_id": rid, "request_group_id": draft[rid].get("request_group_id"), "draft_intent": d,
                         "intent": s["intent"], "state": s["state"], "label_status": LABEL_STATUS[s["state"]],
                         "changed_from_draft": s["intent"] != d, "decisions": s["decisions"],
                         "suggested_intent": last.get("intent") if s["state"] == "flagged" else None,
                         "reviewer": last.get("reviewer"), "reviewed_at": last.get("reviewed_at"), "note": last.get("note"),
                         "text": text_of.get(rid)})
    totals = {k: sum(v[k] for v in by_intent.values()) for k in (*STATES, "total")}
    return {"log": rel(log), "log_exists": log.exists(), "entries": len(entries), "invalid_lines": bad, "ignored_entries": ignored,
            "totals": totals, "by_intent": by_intent, "by_draft_intent": by_draft,
            "relabel_matrix": {"labels": intents, "rows": "DRAFT 라벨", "columns": "유효 라벨", "matrix": matrix},
            "group_conflicts": [{"request_group_id": g, "intents": sorted(v)} for g, v in sorted(group_intents.items()) if len(v) > 1],
            "requests": requests,
            "notes": ["유효 라벨 = 마지막 approve·relabel 결정의 의도, 검토 전은 DRAFT 라벨(label_status=draft).",
                      "flag는 라벨을 바꾸지 않고 상태만 보류로 둔다(학습 effective 모드에서 제외).",
                      "by_intent는 유효 라벨 기준, by_draft_intent는 DRAFT 라벨 기준 집계."]}


# ---------- 기준 모델(numpy만) ----------

def ngrams(text, low=2, high=4):
    text = f" {text.casefold()} "
    return Counter(text[i:i + n] for n in range(low, high + 1) for i in range(len(text) - n + 1))


def transform(tfidf, docs, counts=None):
    """부선형 tf(1+log) × 평활 idf, 행 L2 정규화. 학습 어휘에 없는 n-gram은 버린다."""
    counts = counts if counts is not None else [ngrams(d, tfidf["low"], tfidf["high"]) for d in docs]
    x = np.zeros((len(counts), len(tfidf["vocab"])))
    for row, c in enumerate(counts):
        for g, k in c.items():
            j = tfidf["vocab"].get(g)
            if j is not None:
                x[row, j] = 1 + math.log(k)
    x *= tfidf["idf"]
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    return np.divide(x, norm, out=np.zeros_like(x), where=norm > 0)


def fit_tfidf(docs, min_df=2, low=2, high=4):
    counts = [ngrams(d, low, high) for d in docs]
    df = Counter(g for c in counts for g in c)
    vocab = {g: i for i, g in enumerate(sorted(g for g, k in df.items() if k >= min_df))}
    n = len(docs)
    tfidf = {"vocab": vocab, "idf": np.array([math.log((1 + n) / (1 + df[g])) + 1 for g in vocab]), "low": low, "high": high}
    return tfidf, transform(tfidf, docs, counts)


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def train_logreg(x, y, k, l2=1e-3, iterations=400, lr=4.0, seed=SEED, log_every=50):
    """다항 로지스틱 회귀: 전체 배치 경사하강, 고정 반복, L2(편향 제외).

    W = Xᵀα로 두고 α를 갱신한다(문장 수 n ≪ 특징 수일 때 빠름). W ← W − lr(XᵀG + λW)와 같은 경로이고,
    초기값은 W0 = Xᵀα0, α0 ~ N(0, 0.01)(seed 고정)이다.
    """
    y = np.asarray(y)
    rng = np.random.default_rng(seed)
    n = len(y)
    gram = x @ x.T
    a, b = rng.normal(0, 0.01, (n, k)), np.zeros(k)
    onehot, log = np.eye(k)[y], []
    for it in range(iterations + 1):
        ka = gram @ a
        p = softmax(ka + b)
        if it % log_every == 0 or it == iterations:
            loss = -np.log(p[np.arange(n), y] + 1e-12).mean() + 0.5 * l2 * float((a * ka).sum())
            log.append({"iteration": it, "loss": round(float(loss), 5), "train_accuracy": round(float((p.argmax(1) == y).mean()), 4)})
        if it == iterations:
            break
        g = (p - onehot) / n
        a -= lr * (g + l2 * a)
        b -= lr * g.sum(0)
    return {"w": x.T @ a, "b": b}, log


def predict_proba(model, x):
    return softmax(x @ model["w"] + model["b"])


def metrics(y_true, y_pred, labels):
    """정확도, macro-F1(정답·예측에 나온 의도 평균), 의도별 정밀도·재현율·F1, 혼동행렬(행=정답, 열=예측)."""
    k = len(labels)
    cm = np.bincount(np.asarray(y_true, dtype=int) * k + np.asarray(y_pred, dtype=int), minlength=k * k).reshape(k, k)
    per, f1s = {}, []
    for i, name in enumerate(labels):
        tp, support, predicted = int(cm[i, i]), int(cm[i].sum()), int(cm[:, i].sum())
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else None
        f1 = 2 * precision * recall / (precision + recall) if recall and precision + recall else 0.0
        per[name] = {"precision": round(precision, 4), "recall": None if recall is None else round(recall, 4), "f1": round(f1, 4), "support": support}
        if support or predicted:
            f1s.append(f1)
    n = int(cm.sum())
    return {"n": n, "accuracy": round(float(np.trace(cm)) / n, 4) if n else None,
            "macro_f1": round(float(np.mean(f1s)), 4) if f1s else None, "per_intent": per,
            "confusion": {"labels": labels, "rows": "정답", "columns": "예측", "matrix": cm.tolist()}}


def group_bootstrap(y_true, y_pred, groups, labels, seed=SEED, rounds=1000):
    """원본 그룹을 복원 추출해 macro-F1 95% 구간을 낸다(같은 그룹 문장은 함께 움직임)."""
    by = defaultdict(list)
    for i, g in enumerate(groups):
        by[g].append(i)
    keys = sorted(by)
    if len(keys) < 2:
        return [None, None]
    rng = random.Random(seed)
    stats = []
    for _ in range(rounds):
        idx = [i for _ in keys for i in by[keys[rng.randrange(len(keys))]]]
        stats.append(metrics([y_true[i] for i in idx], [y_pred[i] for i in idx], labels)["macro_f1"])
    stats.sort()
    return [round(stats[int(.025 * rounds)], 4), round(stats[int(.975 * rounds) - 1], 4)]


def evaluate(model, tfidf, rows, labels, field, seed=SEED, max_errors=30, bootstrap=True):
    ids = {name: i for i, name in enumerate(labels)}
    proba = predict_proba(model, transform(tfidf, [r[field] for r in rows]))
    pred = proba.argmax(1).tolist()
    y = [ids[r["intent"]] for r in rows]
    out = metrics(y, pred, labels)
    out["log_loss"] = round(float(-np.log(proba[np.arange(len(y)), y] + 1e-12).mean()), 5) if y else None
    if bootstrap:
        out["macro_f1_ci95_group_bootstrap"] = group_bootstrap(y, pred, [r["request_group_id"] for r in rows], labels, seed)
    if field == "text":  # 원문 변형만: 템플릿 고정 문구가 붙은 문장과 안 붙은 문장의 정확도 차이 = 템플릿 의존 정도
        kinds = defaultdict(lambda: [0, 0])
        for r, t, p in zip(rows, y, pred):
            kind = "unknown" if r["template"] is None else "wrapped" if wrappers(r["template"]) else "wrapper_free"
            kinds[kind][0] += 1
            kinds[kind][1] += t == p
        out["by_template"] = {k: {"n": n, "accuracy": round(c / n, 4)} for k, (n, c) in sorted(kinds.items())}
    out["errors"] = [{"request_id": r["request_id"], "request_group_id": r["request_group_id"], "true": labels[t], "pred": labels[p],
                      "confidence": round(float(proba[i, p]), 4), "text": r[field]}
                     for i, (r, t, p) in enumerate(zip(rows, y, pred)) if t != p][:max_errors]
    return out


def timing(model, tfidf, raw_texts):
    """문장 한 개씩 전처리→n-gram→TF-IDF→예측 시간(ms). 첫 문장으로 예열한 뒤 잰다."""
    def one(text):
        return int(predict_proba(model, transform(tfidf, [preprocess(text)])).argmax(1)[0])
    if not raw_texts:
        return {"sentences": 0}
    one(raw_texts[0])
    times = []
    for text in raw_texts:
        t0 = time.perf_counter()
        one(text)
        times.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    predict_proba(model, transform(tfidf, [preprocess(t) for t in raw_texts])).argmax(1)
    batch = (time.perf_counter() - t0) * 1000 / len(raw_texts)
    a = np.asarray(times)
    return {"sentences": len(times), "includes": "전처리+문자 n-gram 추출+TF-IDF 변환+예측",
            "per_sentence_ms": {"mean": round(float(a.mean()), 4), "median": round(float(np.median(a)), 4),
                                "p95": round(float(np.percentile(a, 95)), 4), "max": round(float(a.max()), 4)},
            "batch_ms_per_sentence": round(batch, 4),
            "environment": {"python": platform.python_version(), "numpy": np.__version__, "machine": platform.machine(),
                            "processor": platform.processor() or None}}


def top_features(model, tfidf, labels, phrases, top=10):
    """의도별 가중치 상위 n-gram과 그것이 그 의도의 템플릿 고정 문구 안에 있는지."""
    inv = [None] * len(tfidf["vocab"])
    for g, j in tfidf["vocab"].items():
        inv[j] = g
    out = {}
    for k, name in enumerate(labels):
        order = np.argsort(-model["w"][:, k], kind="stable")[:top]
        items = [{"ngram": inv[j], "weight": round(float(model["w"][j, k]), 4),
                  "in_template": bool(inv[j].strip()) and any(inv[j].strip() in p for p in phrases.get(name, ()))} for j in order]
        out[name] = {"template_share": round(sum(x["in_template"] for x in items) / len(items), 4) if items else None, "items": items}
    return out


def fit_variant(parts, labels, field, seed, min_df, iterations, lr, grid):
    """train으로 학습, validation macro-F1로 l2 선택(동점이면 validation log loss가 낮은 쪽), test는 선택 후 한 번만 본다."""
    train = parts["train"]
    tfidf, xtr = fit_tfidf([r[field] for r in train], min_df)
    ids = {name: i for i, name in enumerate(labels)}
    ytr = [ids[r["intent"]] for r in train]
    trials = []
    for l2 in grid:
        model, log = train_logreg(xtr, ytr, len(labels), l2, iterations, lr, seed)
        val = evaluate(model, tfidf, parts["validation"], labels, field, seed, bootstrap=False) if parts["validation"] else {}
        trials.append({"l2": l2, "validation_macro_f1": val.get("macro_f1"), "validation_log_loss": val.get("log_loss"),
                       "final_loss": log[-1]["loss"], "final_train_accuracy": log[-1]["train_accuracy"], "_model": model, "_log": log})
    best = max(trials, key=lambda t: (t["validation_macro_f1"] or 0, -(t["validation_log_loss"] or 0), t["l2"]))
    phrases = defaultdict(set)
    for r in train:
        phrases[r["intent"]].update(p.casefold() for p in wrappers(r["template"]))
    eval_rows = parts["validation"] + parts["test"]
    return {"field": field, "vocab_size": len(tfidf["vocab"]), "chosen_l2": best["l2"],
            "l2_selection": [{k: v for k, v in t.items() if not k.startswith("_")} for t in trials],
            "train_log": best["_log"],
            "train": {"n": len(train), "accuracy": best["_log"][-1]["train_accuracy"]},
            "validation": evaluate(best["_model"], tfidf, parts["validation"], labels, field, seed) if parts["validation"] else None,
            "test": evaluate(best["_model"], tfidf, parts["test"], labels, field, seed) if parts["test"] else None,
            "top_features": top_features(best["_model"], tfidf, labels, phrases),
            "inference": timing(best["_model"], tfidf, [r[field] for r in eval_rows])}


def run_baseline(split_path=SPLIT_OUT, out=BASELINE_OUT, labels=LABELS, texts=TEXTS, production=PRODUCTION, log=REVIEW_LOG,
                 label_source="draft", seed=SEED, min_df=2, iterations=400, lr=4.0, grid=L2_GRID):
    """분할 명세대로 원문(raw)·템플릿 제거(template_stripped) 두 변형을 학습·평가해 out에 쓴다."""
    spec = json.loads(Path(split_path).read_text(encoding="utf-8"))
    data = load(labels, texts, production)
    rows = data["rows"]
    excluded = []
    if label_source == "effective":
        eff = {r["request_id"]: r for r in status(log, labels, None)["requests"]}
        excluded = [r["request_id"] for r in rows if eff[r["request_id"]]["state"] == "flagged"]
        rows = [dict(r, intent=eff[r["request_id"]]["intent"], label_status=eff[r["request_id"]]["label_status"])
                for r in rows if r["request_id"] not in excluded]
    elif label_source != "draft":
        raise ValueError("label_source는 draft 또는 effective입니다.")
    group_sets = {s: set(spec["splits"][s]["groups"]) for s in SPLITS}
    overlap = sorted((group_sets["train"] & group_sets["validation"]) | (group_sets["train"] & group_sets["test"])
                     | (group_sets["validation"] & group_sets["test"]))
    if overlap:
        raise ValueError(f"분할 사이에 겹치는 그룹이 있습니다: {overlap[:5]}")
    where = spec["group_to_split"]
    parts = {s: [r for r in rows if where.get(r["request_group_id"]) == s] for s in SPLITS}
    unassigned = sorted(r["request_id"] for r in rows if r["request_group_id"] not in where)
    if not parts["train"]:
        raise ValueError("train 분할이 비어 있습니다.")
    labels_order = ordered(r["intent"] for r in rows)
    current = {name: sha256(path) for name, path in (("labels", labels), ("texts", texts), ("production", production)) if path and Path(path).exists()}
    stale = sorted(name for name, item in spec.get("inputs", {}).items() if current.get(name) != item.get("sha256"))
    states = Counter(r["label_status"] for r in rows)
    is_draft = label_source == "draft" or any(s in ("draft", None) for s in states)
    variants = {"raw": fit_variant(parts, labels_order, "text", seed, min_df, iterations, lr, grid),
                "template_stripped": fit_variant(parts, labels_order, "core_text", seed, min_df, iterations, lr, grid)}
    variants["raw"]["description"] = "정규화한 요청 원문 전체"
    variants["template_stripped"]["description"] = "생성 메타데이터의 표현 템플릿 고정 문구를 지운 핵심 문장(상황+요청 행동)"

    test_counts = spec["splits"]["test"]["counts"]["by_intent"].values()
    small = (min((c["groups"] for c in test_counts), default=0), min((c["requests"] for c in test_counts), default=0))

    def head(v):
        test, val = v["test"] or {}, v["validation"] or {}
        return {"chosen_l2": v["chosen_l2"], "validation_macro_f1": val.get("macro_f1"), "test_macro_f1": test.get("macro_f1"),
                "test_macro_f1_ci95": test.get("macro_f1_ci95_group_bootstrap"), "test_accuracy": test.get("accuracy"),
                "test_recall": {k: x["recall"] for k, x in test.get("per_intent", {}).items()},
                "inference_ms_per_sentence": v["inference"].get("per_sentence_ms", {}).get("mean")}
    doc = {"schema_version": "1.0", "name": out and Path(out).stem, "generated_at": now(),
           "label_status": "draft" if is_draft else "human_reviewed", "diagnostic_only": is_draft,
           "warning": DRAFT_WARNING if is_draft else "사람 검토 라벨 기준. 합성 자료라 서비스 성능 추정은 아니다.",
           "caveats": [DRAFT_WARNING if is_draft else "사람 검토 라벨 기준 결과다.", TEMPLATE_CAVEAT,
                       "템플릿을 지우면 같은 그룹의 표현 변형이 같은 문장이 되어 template_stripped의 실제 평가 표본은 분할당 상황(그룹) 수와 같다.",
                       f"test 분할은 의도별 최소 {small[0]}그룹·{small[1]}문장이라 한 그룹을 틀리면 그 의도 재현율이 크게 떨어진다(2그룹이면 0.5). 그룹 부트스트랩 95% 구간을 함께 본다.",
                       "모든 문장은 같은 작성 과정(AI 작성 상황·표현 + 결정적 렌더링)에서 나온 합성 자료다. 실제 사용자 요청의 분포·오탈자·짧은 문장을 반영하지 않는다.",
                       "validation은 l2 선택에 썼으므로 보고값은 test 기준이다. test로 설정을 고르지 않았다.",
                       "추론 시간은 이 컴퓨터 CPU에서 한 번 잰 값(numpy, 스레드 수 고정 안 함)이다."],
           "split": {"path": rel(split_path), "sha256": sha256(split_path), "seed": spec.get("seed"),
                     "counts": {s: spec["splits"][s]["counts"] for s in SPLITS}, "inputs_changed_since_split": stale,
                     "unassigned_requests": unassigned},
           "labels": {"source": label_source, "label_status_counts": dict(states), "excluded_flagged": excluded, "intents": labels_order,
                      "intent_names": {i: INTENT_NAMES.get(i, i) for i in labels_order}},
           "model": {"features": "문자 2-4gram TF-IDF(공백 포함, 문장 앞뒤 공백 1칸 덧댐, 대소문자 무시)", "min_df": min_df,
                     "tf": "1+log(tf)", "idf": "ln((1+n)/(1+df))+1", "norm": "l2",
                     "classifier": "다항 로지스틱 회귀(numpy 전체 배치 경사하강, 편향 제외 L2)", "iterations": iterations,
                     "learning_rate": lr, "l2_grid": list(grid), "selection": "validation macro-F1 최대(동점이면 validation log loss 최소)",
                     "seed": seed, "init": "W0 = Xᵀα0, α0 ~ N(0, 0.01), 편향 0(W = Xᵀα로 갱신, 원 공간 경사하강과 같은 경로)",
                     "top_features_note": "in_template=그 n-gram이 그 의도의 템플릿 고정 문구에도 들어 있음. 템플릿 제거 변형에서는 상황 문장에도 같은 표현이 있다는 뜻이다."},
           "summary": {name: head(v) for name, v in variants.items()},
           "variants": variants}
    if out:
        write_json(out, doc)
    return doc


def baseline_result(path=BASELINE_OUT):
    """저장된 기준 모델 결과(없으면 None)."""
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------- CLI ----------

def _pct(x):
    return "-" if x is None else f"{x:.3f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("split", help="그룹 단위 의도별 층화 분할 명세 저장")
    s.add_argument("--seed", type=int, default=SEED)
    s.add_argument("--out", type=Path, default=SPLIT_OUT)
    b = sub.add_parser("baseline", help="문자 2-4gram TF-IDF + 로지스틱 회귀 기준 모델(DRAFT 라벨 진단용)")
    b.add_argument("--split", type=Path, default=SPLIT_OUT)
    b.add_argument("--out", type=Path)
    b.add_argument("--labels", choices=["draft", "effective"], default="draft")
    st = sub.add_parser("status", help="사람 검토 진행 현황")
    st.add_argument("--json", action="store_true")
    sub.add_parser("preprocess", help="전처리 점검 결과를 출력(파일로 쓰지 않음)")
    r = sub.add_parser("record", help="검토 결정 한 건 기록")
    r.add_argument("request_id")
    r.add_argument("decision", choices=sorted(DECISIONS))
    r.add_argument("--intent", choices=INTENTS)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--note", default="")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "split":
        doc = write_split(args.out, args.seed)
        print(f"분할 명세 저장: {rel(args.out)} (seed {doc['seed']}, 그룹 단위, 의도별 층화)")
        for name in SPLITS:
            c = doc["splits"][name]["counts"]
            print(f"  {name}: {c['groups']}그룹 {c['requests']}건 · " + ", ".join(f"{INTENT_NAMES.get(i, i)} {v['groups']}" for i, v in c["by_intent"].items()))
        pre = doc["preprocess"]
        nd = pre["near_duplicates"]
        ge = f"ge_{nd['threshold']}"
        print(f"  결합 이상 없음: {doc['join']['ok']} · 정규화로 바뀐 문장 {pre['normalization']['changed']}건 · "
              f"고유 핵심 문장 {pre['duplicates']['core_text']['unique']}개")
        print(f"  근접 중복(Jaccard≥{nd['threshold']}): 그룹 안 {nd['within_group'][ge]}/{nd['within_group']['pairs']}쌍, "
              f"그룹 간 {nd['cross_group'][ge]}/{nd['cross_group']['pairs']}쌍, "
              f"평가 문장의 train 최대 유사도 {nd['cross_split']['held_out_max_vs_train']['max'] / 100:.3f}")
        tp = pre["template_phrases"]
        print(f"  템플릿 고정 문구 {tp['phrases']}개 중 한 의도 전용 {tp['single_intent']}개, 세 분할 모두 등장 {tp['in_all_splits']}개(누수 주의)")
    elif args.command == "baseline":
        out = args.out or (BASELINE_OUT if args.labels == "draft" else BASELINE_EFFECTIVE_OUT)
        if not Path(args.split).exists():
            raise SystemExit(f"{rel(args.split)} 없음. 먼저 split을 실행하세요.")
        doc = run_baseline(args.split, out, label_source=args.labels)
        print(f"기준 모델 결과 저장: {rel(out)}")
        print(f"  ※ {doc['warning']}")
        for name, h in doc["summary"].items():
            print(f"  [{name}] l2={h['chosen_l2']} · validation macro-F1 {_pct(h['validation_macro_f1'])} · "
                  f"test macro-F1 {_pct(h['test_macro_f1'])} (그룹 부트스트랩 95% {h['test_macro_f1_ci95']}) · "
                  f"추론 {h['inference_ms_per_sentence']} ms/문장")
            print("    test 재현율: " + ", ".join(f"{INTENT_NAMES.get(i, i)} {_pct(v)}" for i, v in h["test_recall"].items()))
        if doc["split"]["inputs_changed_since_split"]:
            print(f"  경고: 분할 이후 바뀐 입력 {doc['split']['inputs_changed_since_split']}. split을 다시 실행하세요.")
    elif args.command == "status":
        st = status()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
            return
        t = st["totals"]
        print(f"검토 기록 {st['entries']}건({st['log']}{'' if st['log_exists'] else ', 아직 없음'}) · "
              f"확정 {t['approved']} · 변경 {t['relabelled']} · 보류 {t['flagged']} · 대기 {t['pending']} / {t['total']}")
        for intent, c in st["by_intent"].items():
            print(f"  {INTENT_NAMES.get(intent, intent)}({intent}): 확정 {c['approved']} · 변경 {c['relabelled']} · 보류 {c['flagged']} · 대기 {c['pending']}")
        if st["group_conflicts"]:
            print(f"  그룹 의도 충돌 {len(st['group_conflicts'])}건: 분할을 다시 만들어야 합니다.")
    elif args.command == "preprocess":
        print(json.dumps(preprocess_report(), ensure_ascii=False, indent=1))
    else:
        entry = record(args.request_id, args.decision, args.intent, args.reviewer, args.note)
        print(json.dumps(entry, ensure_ascii=False))


if __name__ == "__main__":
    main()
