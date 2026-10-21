"""줄 신뢰도 보정 진단: 줄 정답 초안(lines-v2)의 A_clean 줄에서 OCR 신뢰도가 줄 정답 여부(line_correct)를 얼마나 맞게 말하는지 잰다.

진단용이다. posters-v2는 내부 평가 전용(internal_eval_only) 자료이고, 여기서 맞춘 온도·등위 회귀 값은 팀이 정하기 전까지 서비스에 넣지 않는다.

지표(출처마다, A_clean 줄만, 라벨 = line_correct, 점수 = 줄 confidence): AUROC, Brier, ECE(같은 폭 10칸), 신뢰도 칸(개수·평균 신뢰도·정답률),
위험-처리율 곡선, 오류율 1%·2%에서의 처리율. 보정: (가) 온도 σ(logit(c)/T), (나) 등위 회귀(PAV, numpy), (참고) 플랫 σ(a·logit(c)+b).
dev+test만으로 시리즈 묶음 5겹 교차검증을 하고 접힘 밖 예측으로 보정 전후를 비교한다. 예비분(reserve)은 dev+test 전체로 맞춘 보정을
한 번만 적용해 '확인용(이미 노출된 예비분)'으로 따로 적는다. 사람 확인 값 줄(human_checked)과 AI 보조 판독 줄을 나눠 보고하지만
보정은 전체 줄로 맞춘다(서비스는 이 구분을 모른다).

시리즈(series_id) 휴리스틱: 정답 문서(gold-v2h)의 표지가 같은 포스터를 합집합 찾기로 잇고, 이어진 덩어리를 한 시리즈로 본다.
 1) 제목 문구: key(title.official)에 '국가유산야행'·'미디어아트'·'야외도서관' 또는 궁궐·왕릉 낱말(경복궁·창경궁·덕수궁·창덕궁·경희궁·종묘·왕릉·궁중)이 있으면 같은 문구끼리.
 2) 기관 틀: 기관 이름에 국가유산청·국가유산진흥원·궁능유적본부가 있으면 '국가유산청 틀'끼리(야행·미디어아트·궁궐 활용 사업의 공통 로고 띠).
 3) 같은 축제: 연도·회차 표기(evaluate.TITLE_MARKS)를 뺀 key(title.official)가 같으면.
 4) 같은 지역 주최: 1)·2)에 걸리지 않은 포스터만, 주최·주관 기관(역할이 없으면 역할 없는 기관) 가운데 중앙 부처·광역 시도·국가유산 기관을
    건너뛴 첫 기관 key(끝의 시·군·구·특례시·구청 등을 뗀 것)가 같으면. 국가유산청 틀 포스터의 공동 주최 시·군은 틀의 일부라 잇지 않는다.
 '축제'·'페스티벌'·'청년'·'북'·'국화'·'빵' 같은 흔한 분류 낱말은 쓰지 않는다. 접힘은 시리즈 단위로 포스터 수를 고르게 나눈다(큰 시리즈부터,
 크기가 같으면 sha256(seed:series_id) 순으로 포스터가 가장 적은 접힘에). 한 포스터의 모든 줄과 한 시리즈의 모든 포스터가 같은 접힘에 든다.

  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.calibration [--lines data/runtime/poster-ocr/lines-v2] [--out data/runtime/poster-ocr/calib]
출력: <out>/<source>.json(출처별 전체 결과), <out>/summary.json(시리즈·접힘과 출처별 비교표).
엔진마다 정답과 맞춘 줄 집합이 달라 이 줄 정확도·CER·보정 지표로 엔진끼리 견주면 안 된다. 엔진 비교는 evaluate.py의 판독률(reads.all.mean_char_accuracy)을 쓴다.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

from .evaluate import TITLE_MARKS, key, wilson
from .line_labels import lev, norm_map

ROOT = Path(__file__).resolve().parents[2]
LINES = ROOT / "data/runtime/poster-ocr/lines-v2"
OUT = ROOT / "data/runtime/poster-ocr/calib"
GOLD = ROOT / "data/benchmarks/gold-v2h"
SCHEMA = "poster-ocr.calibration.v1"
BINS = 10               # ECE·신뢰도 칸 수(같은 폭)
FOLDS = 5
SEED = 20261021
RISKS = (0.01, 0.02)    # 처리율을 잴 목표 오류율
BOOT = 1000             # 포스터 단위 부트스트랩 반복 수
EPS = 1e-6              # logit 계산 전 신뢰도 자르기
RIDGE = 1e-3            # 로지스틱 맞춤의 아주 작은 능선(항등 보정 쪽으로, 한쪽 라벨만 있는 접힘에서도 값이 유한하도록)
FIT_SPLITS = ("dev", "test")
SPLITS = ("dev", "test", "reserve")
NOTICE = "진단용: 내부 평가 전용 자료(posters-v2, internal_eval_only)로 맞춘 값입니다. 팀이 정하기 전까지 서비스 신뢰도에 적용하지 않습니다."
COMPARE_NOTE = ("엔진마다 정답과 맞춘 줄 집합이 달라 줄 정확도·CER·보정 지표는 그 출처 안에서만 의미가 있습니다. "
                "엔진끼리 비교할 때는 evaluate.py의 필드 판독률(reads.all.mean_char_accuracy)을 쓰세요.")
SCOPES = {"all": "A_clean 줄 전체", "human_checked": "사람 확인 값 줄", "ai_assisted": "AI 보조 판독 값 줄"}
METHODS = {"raw": "보정 전(OCR 신뢰도 그대로)", "temperature": "온도 보정 σ(logit(c)/T)", "isotonic": "등위 회귀(PAV)",
           "platt": "참고: 플랫 보정 σ(a·logit(c)+b)"}
EVALS = {"oof": "교차검증 접힘 밖 예측(dev+test, 시리즈 묶음 5겹)", "reserve": "확인용(이미 노출된 예비분)"}
LABELS = {
    "scopes": SCOPES, "methods": METHODS, "evaluations": EVALS,
    "metrics": {"auroc": "AUROC: 맞은 줄이 틀린 줄보다 점수가 높을 확률(동점 1/2, 보정해도 순서는 거의 그대로)",
                "brier": "Brier 점수: (점수-정답)² 평균, 낮을수록 좋음", "ece": f"ECE: 같은 폭 {BINS}칸의 |정답률-평균 점수| 가중 평균, 낮을수록 좋음",
                "ece_equal_mass": f"참고 ECE: 줄 수가 같은 {BINS}칸", "aurc": "위험-처리율 곡선 아래 넓이(낮을수록 좋음), eaurc는 완벽한 순서와의 차이",
                "ci95": "포스터 단위 부트스트랩 95% 구간(보정 모델은 고정, 맞춤 변동은 넣지 않음)",
                "vs_raw": "같은 부트스트랩 표본에서 잰 (보정 - 보정 전) 차이와 그 95% 구간"},
    "selective": {"oracle": "사후 최적 문턱: 이 자료의 라벨로 고른 문턱에서 오류율 ≤ 목표인 최대 처리율(낙관적 상한)",
                  "transfer": "학습 자료(교차검증은 학습 접힘, 예비분은 dev+test)의 라벨로 고른 문턱을 그대로 쓴 실제 처리율·오류율",
                  "expected": "라벨 없이 점수만으로 고른 문턱: 받아들인 줄의 예상 오류율 mean(1-점수) ≤ 목표. 보정 전은 OCR 신뢰도를 확률로 그대로 믿은 경우"},
}
# 시리즈 휴리스틱(모듈 설명 참고)
SERIES_PHRASES = (("국가유산야행", "국가유산 야행"), ("미디어아트", "미디어아트"), ("야외도서관", "야외도서관"),
                  *((w, "궁궐·왕릉") for w in ("경복궁", "창경궁", "덕수궁", "창덕궁", "경희궁", "종묘", "왕릉", "궁중")))
HERITAGE = ("국가유산청", "국가유산진흥원", "궁능유적본부")
NATIONAL = {"문화체육관광부", "행정안전부", "국방부", "대한민국국방부", "산림청", "농림축산식품부", "해양수산부", "환경부", "교육부",
            "한국관광공사", "unesco", "서울시"}
REGION = re.compile(r"(?:서울|부산|대구|인천|광주|대전|울산)(?:특별시|광역시)|세종특별자치시|전남광주통합특별시"
                    r"|(?:경기|강원|충청북|충청남|전라북|전라남|경상북|경상남)도|(?:강원|전북|제주)특별자치도")
LOCAL_SUFFIX = re.compile(r"(?:특례시|시청|구청|군청|시|군|구)$")
TAG_ORDER = ("기관틀", "문구", "축제", "주최")
SERIES_RULES = [
    "제목 문구: key(title.official)에 '국가유산야행'·'미디어아트'·'야외도서관' 또는 궁궐·왕릉 낱말(경복궁·창경궁·덕수궁·창덕궁·경희궁·종묘·왕릉·궁중)이 있으면 같은 문구끼리 잇는다.",
    "기관 틀: 기관 이름에 국가유산청·국가유산진흥원·궁능유적본부가 있으면 '국가유산청 틀'끼리 잇는다(야행·미디어아트·궁궐 사업의 공통 로고 띠).",
    "같은 축제: 연도·회차 표기를 뺀 key(title.official)가 같으면 잇는다.",
    "같은 지역 주최: 위 두 규칙에 걸리지 않은 포스터만, 주최·주관 기관 가운데 중앙 부처·광역 시도·국가유산 기관을 건너뛴 첫 기관 key"
    "(끝의 시·군·구·특례시·구청 등을 뗌)가 같으면 잇는다.",
    "이어진 포스터 덩어리 하나가 시리즈 하나다. 흔한 분류 낱말(축제·페스티벌·청년·북·국화·빵 등)은 쓰지 않는다.",
    "접힘: dev+test 포스터를 시리즈 단위로 5겹에 나눈다(큰 시리즈부터, 같은 크기는 sha256(seed:series_id) 순, 포스터가 가장 적은 접힘에).",
]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def r4(value, digits=4):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def rk(risk):
    return f"{risk:.2f}"


# ------------------------------------------------------------------ 시리즈·접힘
def lead_organiser(doc):
    """주최·주관 기관(역할이 없으면 역할 없는 기관) 가운데 중앙 부처·광역 시도·국가유산 기관을 건너뛴 첫 기관 key(끝의 시·군·구 등을 뗌)."""
    items = [i for i in (doc.get("organizations") or {}).get("items") or [] if i.get("name")]
    hosts = [i for i in items if set(i.get("roles") or []) & {"host", "organizer"}] or [i for i in items if not i.get("roles")]
    for item in hosts:
        k = key(item["name"])
        if not k or k in NATIONAL or REGION.fullmatch(k) or any(h in k for h in HERITAGE):
            continue
        short = LOCAL_SUFFIX.sub("", k)
        return short if len(short) >= 2 else k
    return None


def series_tags(doc):
    """포스터 하나의 시리즈 표지. 같은 표지를 가진 포스터끼리 잇는다."""
    title = doc.get("title") or {}
    official = title.get("official") or title.get("raw") or ""
    tags = []
    phrase = next((name for word, name in SERIES_PHRASES if word in key(official)), None)
    if phrase:
        tags.append(f"문구:{phrase}")
    orgs = [key(i.get("name")) for i in (doc.get("organizations") or {}).get("items") or [] if i.get("name")]
    if any(h in k for k in orgs for h in HERITAGE):
        tags.append("기관틀:국가유산청")
    base = key(TITLE_MARKS.sub(" ", official))
    if base:
        tags.append(f"축제:{base}")
    if not phrase and "기관틀:국가유산청" not in tags:   # 문구·기관 틀이 없을 때만 지역 주최로 잇는다
        lead = lead_organiser(doc)
        if lead:
            tags.append(f"주최:{lead}")
    return tags


def derive_series(golds):
    """{case_id: 정답 문서} → {case_id: {series_id, tags(덩어리가 공유한 표지), size}}."""
    parent = {c: c for c in golds}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tags = {c: series_tags(doc) for c, doc in golds.items()}
    members = collections.defaultdict(list)
    for c in sorted(tags):
        for t in tags[c]:
            members[t].append(c)
    shared = {t for t, ids in members.items() if len(ids) > 1}
    for t in shared:
        for c in members[t][1:]:
            parent[find(c)] = find(members[t][0])
    groups = collections.defaultdict(list)
    for c in sorted(golds):
        groups[find(c)].append(c)
    out = {}
    for ids in groups.values():
        names = sorted({t for c in ids for t in tags[c] if t in shared}, key=lambda t: (TAG_ORDER.index(t.split(":")[0]), t))
        for c in ids:
            out[c] = {"series_id": names[0] if names else f"포스터:{ids[0]}", "tags": names, "size": len(ids)}
    return out


def assign_folds(series, splits, k=FOLDS, seed=SEED, fit_splits=FIT_SPLITS):
    """dev+test 포스터를 시리즈 단위로 k겹에 나눈다(포스터 수 균형). {case_id: 접힘}, 예비분은 들어가지 않는다."""
    groups = collections.defaultdict(list)
    for c, s in sorted(splits.items()):
        if s in fit_splits:
            groups[(series.get(c) or {}).get("series_id") or f"포스터:{c}"].append(c)
    order = sorted(groups, key=lambda g: (-len(groups[g]), hashlib.sha256(f"{seed}:{g}".encode("utf-8")).hexdigest()))
    sizes, fold = [0] * k, {}
    for g in order:
        f = min(range(k), key=lambda i: (sizes[i], i))
        sizes[f] += len(groups[g])
        for c in groups[g]:
            fold[c] = f
    return fold


# ------------------------------------------------------------------ 보정기
def logit(c):
    c = np.clip(np.asarray(c, float), EPS, 1 - EPS)
    return np.log(c) - np.log1p(-c)


def sigmoid(z):
    return 0.5 * (1 + np.tanh(0.5 * np.asarray(z, float)))


def fit_logistic(z, y, intercept, ridge=RIDGE):
    """음의 로그우도 + 능선 최소화(뉴턴법, 되짚기). intercept=False면 기울기 β 하나(온도 T = 1/β)."""
    X = np.column_stack([z, np.ones_like(z)]) if intercept else np.asarray(z, float)[:, None]
    w0 = np.array([1.0, 0.0])[: X.shape[1]]
    loss = lambda w: float(np.sum(np.logaddexp(0, X @ w) - y * (X @ w)) + ridge / 2 * np.sum((w - w0) ** 2))
    w = w0.copy()
    for _ in range(100):
        p = sigmoid(X @ w)
        g = X.T @ (p - y) + ridge * (w - w0)
        h = (X * (p * (1 - p))[:, None]).T @ X + ridge * np.eye(len(w))
        step, t, base = np.linalg.solve(h, g), 1.0, loss(w)
        while t > 1e-8 and loss(w - t * step) > base:
            t /= 2
        w = w - t * step
        if np.max(np.abs(t * step)) < 1e-10:
            break
    return w


def pav(y, w):
    """가중 PAV: 정렬된 y에 맞춘 비감소 최소제곱(블록 평균)."""
    vals, wts, sizes = [], [], []
    for v, ww in zip(np.asarray(y, float), np.asarray(w, float)):
        vals.append(v)
        wts.append(ww)
        sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, s2 = vals.pop(), wts.pop(), sizes.pop()
            vals[-1] = (vals[-1] * wts[-1] + v2 * w2) / (wts[-1] + w2)
            wts[-1] += w2
            sizes[-1] += s2
    return np.repeat(vals, sizes)


def fit_isotonic(c, y):
    """같은 신뢰도는 먼저 묶고 PAV. 블록 양 끝 점만 남긴다(np.interp로 예측, 범위 밖은 끝 값)."""
    ux, inv = np.unique(np.asarray(c, float), return_inverse=True)
    cnt = np.bincount(inv)
    fitted = pav(np.bincount(inv, weights=y) / cnt, cnt)
    change = fitted[1:] != fitted[:-1]
    keep = np.r_[True, change] | np.r_[change, True]
    return {"x": ux[keep].tolist(), "y": fitted[keep].tolist()}


def fit(method, c, y):
    if method == "raw":
        return {}
    if method == "isotonic":
        return fit_isotonic(c, y)
    w = fit_logistic(logit(c), y, method == "platt")
    return {"a": float(w[0]), "b": float(w[1])} if method == "platt" else {"T": 1 / max(float(w[0]), 1e-3)}


def predict(method, params, c):
    c = np.asarray(c, float)
    if method == "raw":
        return c
    if method == "temperature":
        return sigmoid(logit(c) / params["T"])
    if method == "platt":
        return sigmoid(params["a"] * logit(c) + params["b"])
    return np.interp(c, params["x"], params["y"])


def describe(method, params, digits=6):
    if method == "isotonic":
        return {"blocks": len(set(params["y"])), "x": [r4(v, digits) for v in params["x"]], "y": [r4(v, digits) for v in params["y"]]}
    return {k: r4(v, digits) for k, v in params.items()}


# ------------------------------------------------------------------ 지표(W: (반복, 줄) 가중치 행렬, 점 추정은 1행)
def _auroc(p, y, W):
    o = np.argsort(p, kind="stable")
    s, t, Wo = p[o], y[o], W[:, o]
    starts = np.flatnonzero(np.r_[True, s[1:] != s[:-1]])
    pos = np.add.reduceat(Wo * t, starts, axis=1)
    neg = np.add.reduceat(Wo * (1 - t), starts, axis=1)
    num = (pos * (np.cumsum(neg, axis=1) - neg + 0.5 * neg)).sum(1)
    den = pos.sum(1) * neg.sum(1)
    return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


def _brier(p, y, W):
    return W @ ((p - y) ** 2) / W.sum(1)


def bin_index(p, bins=BINS):
    return np.clip((np.asarray(p, float) * bins).astype(int), 0, bins - 1)


def _ece(p, y, W, bins=BINS):
    onehot = np.eye(bins)[bin_index(p, bins)]
    return np.abs(W @ (onehot * y[:, None]) - W @ (onehot * p[:, None])).sum(1) / W.sum(1)


METRICS = {"auroc": _auroc, "brier": _brier, "ece": _ece}


def cluster_weights(cases, rounds=BOOT, seed=SEED):
    """포스터 단위 부트스트랩: 포스터를 복원 추출한 횟수를 그 포스터의 모든 줄 가중치로 쓴다."""
    uniq, inv = np.unique(np.asarray(cases), return_inverse=True)
    draws = np.random.default_rng(seed).integers(0, len(uniq), size=(rounds, len(uniq)))
    counts = np.zeros((rounds, len(uniq)))
    np.add.at(counts, (np.arange(rounds)[:, None], draws), 1)
    return counts[:, inv]


def ci(values):
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    return [r4(np.percentile(v, 2.5)), r4(np.percentile(v, 97.5))] if len(v) else [None, None]


def rel_path(path):
    """저장소 안 경로는 저장소 기준 상대 경로로 남긴다(사용자 폴더 이름이 결과 파일에 남지 않게)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def reliability(p, y, bins=BINS):
    b = bin_index(p, bins)
    rows = []
    for i in range(bins):
        m = b == i
        n = int(m.sum())
        rows.append({"bin": i, "lo": i / bins, "hi": (i + 1) / bins, "count": n, "mean_confidence": r4(p[m].mean()) if n else None,
                     "accuracy": r4(y[m].mean()) if n else None, "gap": r4(y[m].mean() - p[m].mean()) if n else None})
    return rows


def reliability_equal_mass(p, y, bins=BINS):
    """줄 수가 같은 칸(점수 순). (칸 목록, 그 칸으로 잰 ECE)."""
    rows, total = [], 0.0
    for i, part in enumerate(np.array_split(np.argsort(p, kind="stable"), bins)):
        if len(part):
            gap = y[part].mean() - p[part].mean()
            total += len(part) * abs(gap)
            rows.append({"bin": i, "lo": r4(p[part].min()), "hi": r4(p[part].max()), "count": len(part),
                         "mean_confidence": r4(p[part].mean()), "accuracy": r4(y[part].mean()), "gap": r4(gap)})
    return rows, r4(total / len(p))


def _desc(p, y):
    """점수 내림차순 동점 묶음 끝마다 (문턱, 받아들인 수, 오류 수, 예상 오류 합 Σ(1-점수))."""
    o = np.argsort(-p, kind="stable")
    s = p[o]
    end = np.flatnonzero(np.r_[s[1:] != s[:-1], True])
    return s[end], end + 1, np.cumsum(1 - y[o])[end], np.cumsum(1 - s)[end]


def oracle(p, y, target):
    """이 자료의 라벨로 고른 문턱: 오류율 ≤ target인 최대 처리율."""
    s, n, err, _ = _desc(p, y)
    ok = np.flatnonzero(err / n <= target + 1e-12)
    if not len(ok):
        return {"coverage": 0.0, "risk": None, "threshold": None, "accepted": 0, "errors": 0}
    i = ok[-1]
    return {"coverage": r4(n[i] / len(p)), "risk": r4(err[i] / n[i]), "threshold": float(s[i]), "accepted": int(n[i]), "errors": int(err[i])}


def expected(p, y, target):
    """라벨 없이 고른 문턱: 예상 오류율 mean(1-점수) ≤ target인 최대 상위 묶음. 실제 오류율은 라벨로 잰다."""
    s, n, err, exp = _desc(p, y)
    ok = np.flatnonzero(exp / n <= target + 1e-12)
    if not len(ok):
        return {"coverage": 0.0, "risk": None, "expected_risk": None, "threshold": None, "accepted": 0, "errors": 0}
    i = ok[-1]
    return {"coverage": r4(n[i] / len(p)), "risk": r4(err[i] / n[i]), "expected_risk": r4(exp[i] / n[i]), "threshold": r4(s[i], 6),
            "accepted": int(n[i]), "errors": int(err[i])}


def accepted(mask, y):
    n, e = int(mask.sum()), int((1 - y[mask]).sum())
    return {"coverage": r4(n / len(y)) if len(y) else None, "risk": r4(e / n) if n else None, "accepted": n, "errors": e}


def risk_coverage(p, y, points=100):
    """위험-처리율 곡선(동점 묶음 끝, 처리율 1% 간격으로 줄임)과 AURC·완벽한 순서의 AURC·E-AURC."""
    s, n, err, _ = _desc(p, y)
    total, cov, risk = len(p), n / len(p), err / n
    aurc = float((np.diff(np.r_[0, n]) * risk).sum() / total)
    k = np.arange(1, total + 1)
    best = float((np.maximum(0, k - y.sum()) / k).mean())
    picks = sorted(({int(np.searchsorted(cov, g + 1e-12, side="right")) - 1 for g in np.linspace(1 / points, 1, points)} - {-1})
                   | {len(n) - 1})
    return {"aurc": r4(aurc), "aurc_optimal": r4(best), "eaurc": r4(aurc - best),
            "points": [{"coverage": r4(cov[i]), "risk": r4(risk[i]), "threshold": r4(s[i], 6)} for i in picks]}


def evaluate_set(scores, y, cases, masks, risks=RISKS, seed=SEED, boot=BOOT):
    """한 평가 묶음(접힘 밖 또는 예비분)·한 범위의 방법별 지표. masks: {방법: {목표: 학습 자료 문턱으로 받아들인 줄}}."""
    n = len(y)
    if n == 0:
        return {"n": 0}
    W = cluster_weights(cases, boot, seed) if boot else None
    one = np.ones((1, n))
    out = {"n": n, "posters": len(set(cases.tolist())), "correct": int(y.sum()), "errors": int(n - y.sum()), "accuracy": r4(y.mean()),
           "methods": {}}
    point, draws = {}, {}
    for m, p in scores.items():
        point[m] = {f: float(func(p, y, one)[0]) for f, func in METRICS.items()}
        row = {"mean_score": r4(p.mean()), **{f: r4(v) for f, v in point[m].items()}}
        if W is not None:
            draws[m] = {f: func(p, y, W) for f, func in METRICS.items()}
            row.update({f"{f}_ci95": ci(v) for f, v in draws[m].items()})
            if m != "raw" and "raw" in draws:
                row["vs_raw"] = {f: {"diff": r4(point[m][f] - point["raw"][f]), "ci95": ci(draws[m][f] - draws["raw"][f])}
                                 for f in ("brier", "ece")}
        rows, ece_mass = reliability_equal_mass(p, y)
        row.update(ece_equal_mass=ece_mass, reliability=reliability(p, y), reliability_equal_mass=rows, risk_coverage=risk_coverage(p, y))
        sel = {}
        for r in risks:
            o = oracle(p, y, r)
            o["threshold"] = r4(o["threshold"], 6)
            sel[rk(r)] = {"oracle": o, "transfer": accepted(masks[m][r], y) if m in masks else None, "expected": expected(p, y, r)}
        row["selective"] = sel
        out["methods"][m] = row
    return out


# ------------------------------------------------------------------ 교차검증·출처 분석
def crossval(c, y, fold, methods=tuple(METHODS), risks=RISKS):
    """접힘 밖 예측 점수, 학습 접힘의 라벨로 고른 문턱으로 받아들인 줄, 접힘별 매개변수."""
    oof = {m: np.full(len(c), np.nan) for m in methods}
    masks = {m: {r: np.zeros(len(c), bool) for r in risks} for m in methods}
    rows = []
    for k in sorted(set(fold.tolist())):
        te, tr = fold == k, fold != k
        row = {"fold": int(k), "fit_lines": int(tr.sum()), "fit_errors": int((1 - y[tr]).sum()), "eval_lines": int(te.sum()),
               "eval_errors": int((1 - y[te]).sum())}
        for m in methods:
            prm = fit(m, c[tr], y[tr])
            oof[m][te] = predict(m, prm, c[te])
            inside = predict(m, prm, c[tr])
            th = {}
            for r in risks:
                t = oracle(inside, y[tr], r)["threshold"]
                masks[m][r][te] = oof[m][te] >= t if t is not None else False
                th[rk(r)] = r4(t, 6)
            info = {"blocks": len(set(prm["y"]))} if m == "isotonic" else describe(m, prm)
            row[m] = {**info, "thresholds": th}
        rows.append(row)
    return oof, masks, rows


def line_accuracy(records):
    """A_clean 줄의 줄 완전 일치율과 CER(정렬용 정규화 NFC·casefold·글자/숫자만, 편집거리/정답 글자 수)."""
    edits = [(lev(norm_map(r["ocr_text"])[0], norm_map(r["text"])[0]), len(norm_map(r["text"])[0])) for r in records]
    out = {"check_cer_zero_vs_line_correct": sum(1 for r, (e, _) in zip(records, edits) if (e == 0) != bool(r["line_correct"])), "scopes": {}}
    keep = {"all": lambda r: True, "human_checked": lambda r: bool(r.get("human_checked")), "ai_assisted": lambda r: not r.get("human_checked")}
    for scope, ok in keep.items():
        out["scopes"][scope] = {}
        for name, splits in (("dev", ("dev",)), ("test", ("test",)), ("reserve", ("reserve",)), ("dev+test", FIT_SPLITS), ("all", SPLITS)):
            pick = [(r, e) for r, e in zip(records, edits) if r["split"] in splits and ok(r)]
            exact = sum(bool(r["line_correct"]) for r, _ in pick)
            chars = sum(ch for _, (_, ch) in pick)
            out["scopes"][scope][name] = {
                "lines": len(pick), "exact": exact, "exact_rate": r4(exact / len(pick)) if pick else None, "exact_ci95": wilson(exact, len(pick)),
                "chars": chars, "cer_micro": r4(sum(e for _, (e, _) in pick) / chars) if chars else None,
                "cer_mean": r4(sum(e / max(1, ch) for _, (e, ch) in pick) / len(pick)) if pick else None}
    return out


def read_records(folder):
    return [r for path in sorted(Path(folder).glob("*.lines.json")) for r in load(path)["records"]]


def analyse(source, summary, records, fold_of, poster_fold, seed=SEED, boot=BOOT, lines_dir=None):
    """한 출처의 보정 진단 전체(출처별 JSON 내용)."""
    tier_a = [r for r in records if r.get("tier") == "A_clean"]
    usable = [r for r in tier_a if isinstance(r.get("confidence"), (int, float)) and 0 <= r["confidence"] <= 1]
    c = np.array([float(r["confidence"]) for r in usable])
    y = np.array([float(bool(r["line_correct"])) for r in usable])
    split = np.array([r["split"] for r in usable], dtype=object)
    cases = np.array([r["case_id"] for r in usable], dtype=object)
    human = np.array([bool(r.get("human_checked")) for r in usable], dtype=bool)
    fit_m, res_m = np.isin(split, FIT_SPLITS), split == "reserve"
    scope_m = {"all": np.ones(len(usable), bool), "human_checked": human, "ai_assisted": ~human}
    cf, yf, casef = c[fit_m], y[fit_m], cases[fit_m]
    fold = np.array([fold_of[x] for x in casef], int)
    oof, oof_masks, fold_rows = crossval(cf, yf, fold)
    final = {m: fit(m, cf, yf) for m in METHODS}
    thresholds = {m: {r: oracle(predict(m, final[m], cf), yf, r)["threshold"] for r in RISKS} for m in METHODS}
    cr, yr, caser = c[res_m], y[res_m], cases[res_m]
    res_scores = {m: predict(m, final[m], cr) for m in METHODS}
    res_masks = {m: {r: (res_scores[m] >= t) if t is not None else np.zeros(len(cr), bool) for r, t in thresholds[m].items()}
                 for m in METHODS}
    evaluation = {name: {"label": label, "scopes": {}} for name, label in EVALS.items()}
    for scope, sm in scope_m.items():
        mf, mr = sm[fit_m], sm[res_m]
        evaluation["oof"]["scopes"][scope] = evaluate_set({m: v[mf] for m, v in oof.items()}, yf[mf], casef[mf],
                                                          {m: {r: v[mf] for r, v in d.items()} for m, d in oof_masks.items()}, seed=seed, boot=boot)
        evaluation["reserve"]["scopes"][scope] = evaluate_set({m: v[mr] for m, v in res_scores.items()}, yr[mr], caser[mr],
                                                              {m: {r: v[mr] for r, v in d.items()} for m, d in res_masks.items()}, seed=seed, boot=boot)
    # 접힘을 포스터 단위로만 묶었을 때(시리즈 무시)와의 차이: 시리즈 묶음이 결과를 얼마나 바꾸는지
    pfold = np.array([poster_fold[x] for x in casef], int)
    poof, _, _ = crossval(cf, yf, pfold, risks=())
    one = np.ones((1, len(yf)))
    sensitivity = {"label": "접힘을 포스터 단위로만 묶은 교차검증(시리즈 무시), A_clean 줄 전체",
                   "methods": {m: {f: r4(func(v, yf, one)[0]) for f, func in METRICS.items()} for m, v in poof.items()}}
    by_split = {s: {"lines": int((split == s).sum()), "correct": int(y[split == s].sum()), "posters": len(set(cases[split == s].tolist()))}
                for s in SPLITS}
    folds = [{"fold": k, "posters": len(set(casef[fold == k].tolist())), "lines": int((fold == k).sum()), "errors": int((1 - yf[fold == k]).sum())}
             for k in sorted(set(fold.tolist()))]
    return {
        "schema": SCHEMA, "created_at": now(), "diagnostic_only": True, "notice": NOTICE, "source": source,
        "engine": summary.get("engine"), "variant": summary.get("variant"),
        "inputs": {"lines_dir": rel_path(lines_dir) if lines_dir else None, "gold": summary.get("gold"), "built_at": summary.get("built_at"),
                   "fingerprint": summary.get("fingerprint")},
        "label": {"tier": "A_clean", "positive": "line_correct=true(정렬용 정규화로 OCR 줄과 정답 부분이 같음)", "score": "OCR 줄 confidence(0-1)"},
        "settings": {"bins": BINS, "folds": len(folds), "seed": seed, "risks": list(RISKS), "bootstrap": boot, "eps": EPS, "ridge": RIDGE,
                     "fit_splits": list(FIT_SPLITS), "cv_group": "series_id(포스터 포함)"},
        "labels": LABELS, "compare_note": COMPARE_NOTE,
        "counts": {"records": len(records), "A_clean": len(tier_a), "usable": len(usable), "missing_confidence": len(tier_a) - len(usable),
                   "by_split": by_split,
                   "by_scope": {s: {"lines": int(m.sum()), "correct": int(y[m].sum())} for s, m in scope_m.items()}},
        "folds": folds,
        "line_accuracy": {"note": COMPARE_NOTE, "tier": "A_clean", **line_accuracy(tier_a)},
        "fit": {"note": "dev+test 전체로 맞춘 최종 보정(예비분 확인용에만 씀). " + NOTICE,
                "final": {m: describe(m, p) for m, p in final.items() if m != "raw"},
                "thresholds": {"note": "dev+test의 라벨로 고른 문턱(각 방법의 점수 척도). 예비분 selective.transfer에 그대로 씀",
                               **{m: {rk(r): r4(t, 6) for r, t in th.items()} for m, th in thresholds.items()}},
                "cv_folds": fold_rows},
        "evaluation": evaluation,
        "sensitivity": {"poster_only_folds": sensitivity},
        "notes": [NOTICE, COMPARE_NOTE,
                  "보정은 A_clean 줄 전체로 맞추고 범위(사람 확인·AI 보조)별로 나눠 잰다. AI 보조 판독 줄의 '틀림'에는 정답 쪽 오류가 섞일 수 있다.",
                  "온도 보정은 점수 순서를 바꾸지 않아 AUROC·사후 최적 처리율은 거의 그대로다(접힘마다 T가 달라 조금 움직일 수 있음). "
                  "보정의 효과는 ECE·Brier와 라벨 없이 고르는 문턱(selective.expected)에서 본다.",
                  "온도 보정은 logit 0(점수 0.5)을 고정한 채 기울기만 바꾸므로 전체 치우침은 고치지 못한다. 플랫 보정은 이를 보려고 참고로 넣었다.",
                  "등위 회귀는 계단 함수라 동점이 생기고, 맨 위 블록이 1.0이면 예상 오류 0으로 보여 selective.expected가 너무 많이 받아들일 수 있다.",
                  "예비분은 이전 작업에서 이미 열어 본 자료라 '확인용(이미 노출된 예비분)'이다. 같은 시리즈 포스터가 dev+test에 있을 수 있다(summary.json series.reserve_overlap)."],
    }


# ------------------------------------------------------------------ 전체 실행
def brief(scope):
    """비교표용 요약: 방법별 ECE·Brier·AUROC와 1%·2% 처리율."""
    if not scope.get("n"):
        return {"n": 0}
    out = {"n": scope["n"], "errors": scope["errors"], "accuracy": scope["accuracy"], "methods": {}}
    for m, row in scope["methods"].items():
        out["methods"][m] = {"ece": row["ece"], "brier": row["brier"], "auroc": row["auroc"],
                             "coverage": {r: {"oracle": s["oracle"]["coverage"], "transfer": (s["transfer"] or {}).get("coverage"),
                                              "transfer_risk": (s["transfer"] or {}).get("risk"), "expected": s["expected"]["coverage"],
                                              "expected_risk": s["expected"]["risk"]} for r, s in row["selective"].items()}}
    return out


def series_report(series, fold_of, splits, golds):
    groups = collections.defaultdict(list)
    for c in sorted(splits):
        groups[series[c]["series_id"]].append(c)
    multi = sorted((g for g in groups.items() if len(g[1]) > 1), key=lambda g: (-len(g[1]), g[0]))
    fit_series = {series[c]["series_id"] for c, s in splits.items() if s in FIT_SPLITS}
    overlap = sorted(c for c, s in splits.items() if s == "reserve" and series[c]["series_id"] in fit_series)
    k = sorted(set(fold_of.values()))
    return {
        "rules": SERIES_RULES, "posters": len(splits), "series_total": len(groups), "multi_series": len(multi),
        "posters_in_multi": sum(len(ids) for _, ids in multi),
        "groups": [{"series_id": sid, "posters": ids, "size": len(ids), "tags": series[ids[0]]["tags"],
                    "splits": dict(collections.Counter(splits[c] for c in ids)),
                    "fold": next((fold_of[c] for c in ids if c in fold_of), None),
                    "titles": [((golds.get(c) or {}).get("title") or {}).get("official") for c in ids]} for sid, ids in multi],
        "folds": [{"fold": f, "posters": sum(1 for v in fold_of.values() if v == f),
                   "series": len({series[c]["series_id"] for c, v in fold_of.items() if v == f}),
                   **{s: sum(1 for c, v in fold_of.items() if v == f and splits[c] == s) for s in FIT_SPLITS}} for f in k],
        "reserve_overlap": {"note": "예비분 포스터 가운데 같은 시리즈가 dev+test에도 있는 것(보정이 비슷한 틀을 이미 봤을 수 있음)",
                            "posters": len(overlap), "case_ids": overlap},
        "by_case": {c: {"series_id": series[c]["series_id"], "split": splits[c], "fold": fold_of.get(c)} for c in sorted(splits)},
    }


def run(lines=LINES, out=OUT, gold=GOLD, sources=None, folds=FOLDS, seed=SEED, boot=BOOT):
    """모든(또는 고른) 출처를 분석해 <out>/<source>.json과 <out>/summary.json을 쓰고 요약을 돌려준다."""
    lines, out, gold = Path(lines), Path(out), Path(gold)
    dirs = [p.parent for p in sorted(lines.glob("*/summary.json")) if not sources or p.parent.name in sources]
    if not dirs:
        raise SystemExit(f"{lines}: 줄 정답 초안(summary.json)이 있는 출처가 없습니다.")
    data = {d.name: read_records(d) for d in dirs}
    splits = {}
    for recs in data.values():
        for r in recs:
            splits.setdefault(r["case_id"], r["split"])
    golds = {c: load(gold / f"{c}.poster.json") for c in sorted(splits) if (gold / f"{c}.poster.json").exists()}
    series = derive_series(golds)
    for c in splits:
        series.setdefault(c, {"series_id": f"포스터:{c}", "tags": [], "size": 1})
    fold_of = assign_folds(series, splits, folds, seed)
    poster_fold = assign_folds({c: {"series_id": f"포스터:{c}"} for c in splits}, splits, folds, seed)
    out.mkdir(parents=True, exist_ok=True)
    table = []
    for d in dirs:
        res = analyse(d.name, load(d / "summary.json"), data[d.name], fold_of, poster_fold, seed, boot, d)
        (out / f"{d.name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        la = res["line_accuracy"]["scopes"]
        table.append({
            "source": res["source"], "engine": res["engine"], "variant": res["variant"], "file": f"{d.name}.json", "counts": res["counts"],
            "temperature_T": res["fit"]["final"]["temperature"]["T"], "platt": res["fit"]["final"]["platt"],
            "isotonic_blocks": res["fit"]["final"]["isotonic"]["blocks"],
            "line_accuracy": {scope: {name: {k: v[name][k] for k in ("lines", "exact_rate", "cer_micro")} for name in ("dev+test", "reserve")}
                              for scope, v in la.items()},
            "scopes": {scope: {ev: brief(res["evaluation"][ev]["scopes"][scope]) for ev in EVALS} for scope in SCOPES},
        })
    summary = {"schema": SCHEMA + ".summary", "created_at": now(), "diagnostic_only": True, "notice": NOTICE,
               "service_use": "적용하지 않음(팀 결정 전)", "compare_note": COMPARE_NOTE, "lines_dir": rel_path(lines), "out_dir": rel_path(out),
               "gold": rel_path(gold), "settings": {"bins": BINS, "folds": folds, "seed": seed, "risks": list(RISKS), "bootstrap": boot},
               "labels": LABELS, "series": series_report(series, fold_of, splits, golds), "sources": table}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lines", type=Path, default=LINES, help="줄 정답 초안 폴더(<source>/summary.json)")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--gold", type=Path, default=GOLD, help="시리즈를 정할 정답 문서 폴더")
    parser.add_argument("--sources", nargs="+", help="기본: 모든 출처")
    parser.add_argument("--folds", type=int, default=FOLDS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--bootstrap", type=int, default=BOOT, help="포스터 단위 부트스트랩 반복 수(0이면 구간 생략)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    s = run(args.lines, args.out, args.gold, args.sources, args.folds, args.seed, args.bootstrap)
    sr = s["series"]
    print(f"[진단용] 시리즈 {sr['series_total']}개(2장 이상 {sr['multi_series']}개, 포스터 {sr['posters_in_multi']}장), "
          f"접힘별 포스터 {[f['posters'] for f in sr['folds']]}, 예비분 중 dev+test와 같은 시리즈 {sr['reserve_overlap']['posters']}장")
    for row in s["sources"]:
        la = row["line_accuracy"]["all"]
        print(f"{row['source']}: A 줄 {row['counts']['usable']:,}개, 줄 일치 dev+test {pct(la['dev+test']['exact_rate'])} / 예비 "
              f"{pct(la['reserve']['exact_rate'])}, CER {la['dev+test']['cer_micro']} / {la['reserve']['cer_micro']}, T={row['temperature_T']}")
        for scope in SCOPES:
            for ev in EVALS:
                b = row["scopes"][scope][ev]
                if not b.get("n"):
                    continue
                ms = b["methods"]
                print(f"  {SCOPES[scope]} · {EVALS[ev]} n={b['n']} 오류 {b['errors']}: ECE " +
                      " → ".join(f"{m} {ms[m]['ece']}" for m in ("raw", "temperature", "isotonic")) +
                      f", Brier {ms['raw']['brier']} → {ms['temperature']['brier']} / {ms['isotonic']['brier']}, AUROC {ms['raw']['auroc']}, "
                      "1% 처리율(사후 최적 / 라벨 없는 문턱 실제 오류) " +
                      " · ".join(f"{m} {pct(ms[m]['coverage']['0.01']['oracle'])}/{pct(ms[m]['coverage']['0.01']['expected'])}"
                                 f"({pct(ms[m]['coverage']['0.01']['expected_risk'])})" for m in ("raw", "temperature", "isotonic")))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
