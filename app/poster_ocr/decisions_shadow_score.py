"""Decisions API 섀도 실험 채점: decisions-shadow/<데이터셋>/<세트>.json(모델 답)을 sets.json 정답·sim_check r4 판정과 비교한다.

r4 판정은 바꾸지 않는다. 정답(sets.json)은 여기서 채점할 때만 읽는다. 계산하는 것:
  - 오류 유형별 '잘못 고친' 비율: 일부러 넣은 오타·숫자 차이에 (c)가 P(true)>0.5를 준 비율
  - (a)가 기준 문서 OCR 오독 6건(v1 S09·S25·S38, v2 S05·S10·S32)을 잡는지
  - (b)가 맞는 줄을 고르는지(v2 S08 장소 줄 포함)
  - AUROC((a)·(c) 항목 단위, (c) 세트 단위), 보기 순서를 바꿨을 때 답이 달라지는 비율
  - 세 설계(판별 먼저 / 확인 필요만 2차 의견 / 병렬로 올리기만) × 모델 신호 두 가지((c) 직접 판정, (b) 줄 고르기 + r4 규칙)
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.decisions_shadow_score --md docs/reports/decisions-shadow/results.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics
import sys

from . import sim_check as sc
from .decisions_shadow import DATASETS, NONE, OUT, ROOT, response_path
from .evaluate import key
from .sim_data import TYPE_LABEL

TRUTH = {"title": lambda d: d["title"], "edition": lambda d: f"제{d['edition']}회" if d.get("edition") else None, "schedule": lambda d: d["schedule"],
         "time": lambda d: d["time"], "venue": lambda d: d["venue"], "host": lambda d: d["host"], "organizer": lambda d: d["organizer"],
         "contact": lambda d: d["contact"], "slogan": lambda d: d["slogan"]}
KNOWN_DOC_MISREADS = [("v1", "S09"), ("v1", "S25"), ("v1", "S38"), ("v2", "S05"), ("v2", "S10"), ("v2", "S32")]
KNOWN_CASES = {("v2", "S05", "title"): "r4 오탐(문서 OCR '걷'→'겉')", ("v2", "S08", "venue"): "r4 놓침(라벨 없는 장소 줄)",
               ("v2", "S13", "title"): "r4 놓침(오타 '국홤향'을 OCR이 0.89로 읽음)"}
TYPE_GROUP = {"title_typo": "한 글자 오타", "contact_diff": "숫자 차이", "edition_swap": "숫자 차이", "time_diff": "숫자 차이", "date_shift": "숫자 차이",
              "date_shift_stale_weekday": "숫자 차이", "venue_diff": "다른 낱말", "organizer_diff": "다른 낱말"}
SPAN = {"title": 4, "slogan": 3, "schedule": 2}  # 고른 줄 앞뒤로 함께 보는 줄 수(여러 줄에 걸친 표기)
P_LINE = 0.5  # 줄 고르기로 '다름'(위반)까지 내릴 때 필요한 선택 확률
DESIGNS = {"r4": "r4 그대로(기준)", "model_first": "판별 먼저", "second_opinion": "확인 필요만 2차 의견", "parallel": "병렬로 올리기만",
           "doc_gate": "r4 + 기준 문서 확인(a)"}
SIGNALS = {"direct": "(c) 직접 판정", "line": "(b) 줄 고르기 + r4 규칙"}
STATUS_KO = {"pass": "충족", "fail": "위반", "review": "확인 필요", None: "답 없음"}


# ------------------------------------------------------------------ 불러오기
def load_dataset(name, out_dir=OUT):
    sim, runtime = DATASETS[name]
    sets = sc.load(sim / "sets.json")["sets"]
    checks, responses = {}, {"ocr": {}, "shuffled": {}}
    for s in sets:
        p = runtime / "checks" / f"{s['id']}.json"
        if p.exists():
            checks[s["id"]] = sc.load(p)
        for order in responses:
            r = response_path(out_dir, name, s["id"], order)
            if r.exists():
                rec = sc.load(r)
                if rec.get("response") is not None:
                    responses[order][s["id"]] = rec
    return {"name": name, "sets": sets, "checks": checks, "responses": responses}


def answers(response):
    """응답 answers → {문항 이름: 정리한 답}. probabilities는 목록({value, probability})이든 사전이든 받는다."""
    out = {}
    for a in (response or {}).get("answers") or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        probs = a.get("probabilities")
        if isinstance(probs, list):
            probs = {str(p.get("value")): p.get("probability") for p in probs if isinstance(p, dict)}
        out[a["name"]] = {"type": a.get("type"), "probability": a.get("probability"), "choice": a.get("choice"), "probs": probs or {},
                          "confidence": a.get("confidence"), "score": a.get("score"), "refusal": a.get("type") == "refusal"}
    return out


def prob(ans):
    if not ans or ans["refusal"] or not isinstance(ans.get("probability"), (int, float)):
        return None
    return float(ans["probability"])


def doc_truth_raw(doc, field):
    if field == "title":
        return doc.get("official") or (f"제{doc['edition']}회 {doc['title']}" if doc.get("edition") else doc["title"])
    return TRUTH[field](doc)


def gold_lines(field, poster, rows):
    """(b) 정답 줄: 포스터에 실제로 찍힌 값(sets.json poster)을 담은 OCR 줄. 못 찾으면 None(채점 제외)."""
    value = TRUTH[field](poster)
    if not value:
        return None
    hit = set()
    if field == "schedule":
        want = {(int(x[5:7]), int(x[8:10])) for x in (poster["start"], poster["end"]) if x}
        hit = {r["value"] for r in rows if {(m, d) for _, m, d, _ in sc.points(r["text"], poster.get("year"))} & want}
    elif field == "time":
        first = [f"{int(h):02d}:{mi}" for h, mi in sc.TIME.findall(value)][:1]
        hit = {r["value"] for r in rows if first and first[0] in [f"{int(h):02d}:{mi}" for h, mi in sc.TIME.findall(r["text"])]}
    elif field == "contact":
        digits = re.sub(r"\D", "", value)
        hit = {r["value"] for r in rows if digits and digits in re.sub(r"\D", "", r["text"])}
    elif field == "edition":
        hit = {r["value"] for r in rows if any(int(n) == poster["edition"] for n in sc.EDITION.findall(r["text"]))}
    if not hit:
        score, used = sc.best_match(value, rows)
        if score >= .6:
            hit = {r["value"] for r in used}
    return hit or None


def item_rows(ds, order="ocr"):
    """(b)·(c)·(d) 항목 단위 표: 세트 × 항목(문서에서 기준 값을 읽은 항목)."""
    out = []
    for s in ds["sets"]:
        rec, check = ds["responses"][order].get(s["id"]), ds["checks"].get(s["id"])
        if not rec or not check:
            continue
        meta, ans = rec["meta"], answers(rec["response"])
        r4 = {r["key"]: r for r in check["results"]}
        mfield = (s["mismatch"] or {}).get("field")
        doc_answers = {q["item"]: ans.get(q["name"]) for q in meta["questions"] if q["probe"] == "a"}
        for f, item in meta["items"].items():
            cond = item["cond"]
            doc_ok = key(cond["expected"]) == key(TRUTH[f](s["document"]))
            out.append({"dataset": ds["name"], "set": s["id"], "field": f, "set_label": s["label"], "type": (s["mismatch"] or {}).get("type"),
                        "should_fail": f == mfield, "doc_ok": doc_ok, "truth_c": doc_ok and f != mfield,
                        "r4": (r4.get(f) or {}).get("status"), "cond": cond, "rows": meta["rows"],
                        "a": doc_answers.get("title" if f == "edition" else f), "b": ans.get(f"b_line_{f}"), "c": ans.get(f"c_direct_{f}"),
                        "d": ans.get(f"d_legible_{f}"), "gold": gold_lines(f, s["poster"], meta["rows"])})
    return out


def doc_rows(ds, order="ocr"):
    """(a) 표: 문서 OCR 값이 생성 정답과 같은지(ok)와 모델 P(true)."""
    out = []
    for s in ds["sets"]:
        rec = ds["responses"][order].get(s["id"])
        if not rec:
            continue
        ans = answers(rec["response"])
        for q in rec["meta"]["questions"]:
            if q["probe"] == "a":
                truth = doc_truth_raw(s["document"], q["item"])
                out.append({"dataset": ds["name"], "set": s["id"], "field": q["item"], "ocr": q["value"], "truth": truth,
                            "ok": key(q["value"]) == key(truth), "p": prob(ans.get(q["name"]))})
    return out


# ------------------------------------------------------------------ 지표
def auroc(pos, neg):
    """양성 점수가 음성 점수보다 클 확률(동점 0.5). pos·neg는 '불일치일수록 큰' 점수."""
    if not pos or not neg:
        return None
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def rate(a, b):
    return a / b if b else None


def miscorrection(items):
    """일부러 넣은 차이(문서 OCR이 맞게 읽은 항목만)에 (c)가 P(true)>0.5를 준 비율: 차이를 못 보고 '그대로 있다'고 본 비율."""
    by_type, excluded = {}, []
    for it in items:
        if not it["should_fail"]:
            continue
        if not it["doc_ok"]:
            excluded.append(f"{it['dataset']} {it['set']} {it['field']}")
            continue
        t = by_type.setdefault(it["type"], {"label": TYPE_LABEL[it["type"]], "group": TYPE_GROUP[it["type"]], "n": 0, "answered": 0,
                                            "miscorrected": 0, "cases": []})
        t["n"] += 1
        p = prob(it["c"])
        if p is None:
            continue
        t["answered"] += 1
        t["miscorrected"] += p > .5
        t["cases"].append({"set": f"{it['dataset']} {it['set']}", "p_true": round(p, 4)})
    for t in by_type.values():
        t["rate"] = rate(t["miscorrected"], t["answered"])
    groups = {}
    for t in by_type.values():
        g = groups.setdefault(t["group"], {"answered": 0, "miscorrected": 0})
        g["answered"] += t["answered"]
        g["miscorrected"] += t["miscorrected"]
    for g in groups.values():
        g["rate"] = rate(g["miscorrected"], g["answered"])
    same = [prob(it["c"]) for it in items if it["truth_c"] and prob(it["c"]) is not None]
    return {"by_type": by_type, "by_group": groups, "excluded_doc_misread": excluded,
            "false_alarm": {"answered": len(same), "p_le_half": sum(p <= .5 for p in same), "rate": rate(sum(p <= .5 for p in same), len(same))}}


def doc_check(drows):
    misreads = [r for r in drows if not r["ok"]]
    correct = [r for r in drows if r["ok"] and r["p"] is not None]
    found = sorted({(r["dataset"], r["set"]) for r in misreads})
    present = {(r["dataset"], r["set"]) for r in drows}
    known = sorted(k for k in KNOWN_DOC_MISREADS if k in present)
    cases = [{"dataset": r["dataset"], "set": r["set"], "field": r["field"], "ocr": r["ocr"], "truth": r["truth"],
              "p_true": None if r["p"] is None else round(r["p"], 4), "caught": r["p"] is not None and r["p"] < .5} for r in misreads]
    return {"misreads": cases, "caught": sum(c["caught"] for c in cases), "n": len(cases), "matches_known_list": found == known,
            "known_present": known, "false_alarm": {"answered": len(correct), "p_lt_half": sum(r["p"] < .5 for r in correct),
                                                    "rate": rate(sum(r["p"] < .5 for r in correct), len(correct))},
            "auroc": auroc([1 - r["p"] for r in misreads if r["p"] is not None], [1 - r["p"] for r in correct])}


def as_row(option):
    return {"text": option["text"], "conf": option["conf"], "records": option["records"]}


def line_rule(cond, rows, ans):
    """(b)로 고른 줄과 그 이웃 줄만 놓고 r4 판정 함수를 다시 돌린다. 근거 박스는 OCR 줄 박스가 그대로 남는다.
    r4가 '찾지 못함'으로 넘기던 라벨 없는 줄도, 모델이 그 줄을 항목으로 짚었고 판독 신뢰도가 높으면 '다름'(위반)으로 본다."""
    if not ans or ans["refusal"] or not ans.get("choice"):
        return None
    choice = str(ans["choice"])
    if choice == NONE:
        return sc.result(cond, "review", "모델이 포스터에 이 항목을 나타내는 줄이 없다고 골랐습니다.", method="줄 고르기")
    idx = next((i for i, r in enumerate(rows) if r["value"] == choice), None)
    if idx is None:
        return None
    span = SPAN.get(cond["key"], 1)
    window = [as_row(r) for r in rows[max(0, idx - span + 1): idx + span]]
    k = cond["key"]
    if k == "schedule":
        return sc.judge_schedule(cond, window, cond.get("year"))
    if k == "time":
        return sc.judge_time(cond, window)
    if k == "contact":
        return sc.judge_contact(cond, window)
    if k == "edition":
        return sc.judge_edition(cond, window)
    out = sc.judge_text(cond, window)
    label = sc.POSTER_LABEL.get(k)
    labelled = sc.labelled_value(window, label)[0] if label else None
    score, _ = sc.best_match(cond["expected"], window)
    chosen = as_row(rows[idx])
    p = ans["probs"].get(choice)
    if out["status"] == "review" and not labelled and score < sc.NEAR and chosen["conf"] >= sc.SURE - .03 and (p is None or p >= P_LINE):
        return sc.result(cond, "fail", f"모델이 {cond['label']} 줄로 고른 문구가 기준 문서와 다릅니다.", chosen["text"], chosen["records"],
                         f"줄 고르기 P {p if p is None else round(p, 2)} · 문자 일치율 {score:.0%} · 판독 신뢰도 {chosen['conf']:.0%}")
    return out


def line_check(items):
    per_field, scored, tops = {}, 0, []
    correct_total = 0
    for it in items:
        if it["gold"] is None or not it["b"] or it["b"]["refusal"] or not it["b"].get("choice"):
            continue
        ok = str(it["b"]["choice"]) in it["gold"]
        f = per_field.setdefault(it["field"], {"n": 0, "correct": 0})
        f["n"] += 1
        f["correct"] += ok
        scored += 1
        correct_total += ok
        p = it["b"]["probs"].get(str(it["b"]["choice"]))
        if isinstance(p, (int, float)):
            tops.append(p)
    for f in per_field.values():
        f["accuracy"] = rate(f["correct"], f["n"])
    accuracy = rate(correct_total, scored)
    mean_top = statistics.mean(tops) if tops else None
    return {"n": scored, "correct": correct_total, "accuracy": accuracy, "by_field": per_field, "mean_top_probability": mean_top,
            "overconfidence": None if mean_top is None or accuracy is None else mean_top - accuracy}


def case_view(items):
    """알려진 오답 사례(r4)에서 각 신호가 낸 답."""
    out = []
    for it in items:
        note = KNOWN_CASES.get((it["dataset"], it["set"], it["field"]))
        if not note:
            continue
        rows = {r["value"]: r["text"] for r in it["rows"]}
        b = it["b"] or {}
        lr = line_rule(it["cond"], it["rows"], it["b"])
        out.append({"case": f"{it['dataset']} {it['set']} {it['field']}", "note": note, "r4": it["r4"], "a_p_true": prob(it["a"]),
                    "c_p_true": prob(it["c"]), "b_choice": rows.get(str(b.get("choice")), b.get("choice")),
                    "b_p": (b.get("probs") or {}).get(str(b.get("choice"))), "b_correct": None if it["gold"] is None or not b.get("choice") else str(b["choice"]) in it["gold"],
                    "b_rule": None if lr is None else lr["status"], "d_score": (it["d"] or {}).get("score")})
    return out


def legibility(items):
    by_status = {}
    for it in items:
        s = (it["d"] or {}).get("score")
        if isinstance(s, (int, float)):
            by_status.setdefault(it["r4"], []).append(s)
    return {STATUS_KO.get(k, k): {"n": len(v), "mean_score": statistics.mean(v)} for k, v in by_status.items()}


def order_check(ds):
    first, second = ds["responses"]["ocr"], ds["responses"]["shuffled"]
    common = sorted(set(first) & set(second))
    b = {"n": 0, "flips": 0, "abs_dp": [], "cases": []}
    pred = {"n": 0, "cross_half": 0, "abs_dp": []}
    score = {"n": 0, "abs_d": []}
    for sid in common:
        A, B = answers(first[sid]["response"]), answers(second[sid]["response"])
        rows = {r["value"]: r["text"] for r in first[sid]["meta"]["rows"]}
        for name, x in A.items():
            y = B.get(name)
            if not y or x["refusal"] or y["refusal"]:
                continue
            if name.startswith("b_") and x.get("choice") and y.get("choice"):
                b["n"] += 1
                cx, cy = str(x["choice"]), str(y["choice"])
                if cx != cy:
                    b["flips"] += 1
                    b["cases"].append(f"{ds['name']} {sid} {name[7:]}: {rows.get(cx, cx)} → {rows.get(cy, cy)}")
                px, py = x["probs"].get(cx), y["probs"].get(cx, 0.0)
                if isinstance(px, (int, float)) and isinstance(py, (int, float)):
                    b["abs_dp"].append(abs(px - py))
            elif name.startswith(("a_", "c_")) and prob(x) is not None and prob(y) is not None:
                pred["n"] += 1
                pred["cross_half"] += (prob(x) > .5) != (prob(y) > .5)
                pred["abs_dp"].append(abs(prob(x) - prob(y)))
            elif name.startswith("d_") and isinstance(x.get("score"), (int, float)) and isinstance(y.get("score"), (int, float)):
                score["n"] += 1
                score["abs_d"].append(abs(x["score"] - y["score"]))
    mean = lambda v: statistics.mean(v) if v else None
    return {"sets": len(common),
            "choice": {"n": b["n"], "flips": b["flips"], "flip_rate": rate(b["flips"], b["n"]), "mean_abs_dp": mean(b["abs_dp"]), "cases": b["cases"]},
            "predicate_rerun": {"n": pred["n"], "cross_half": pred["cross_half"], "cross_rate": rate(pred["cross_half"], pred["n"]),
                                "mean_abs_dp": mean(pred["abs_dp"])},
            "score_rerun": {"n": score["n"], "mean_abs_d": mean(score["abs_d"])}}


def calibrate(points):
    """우리 정답으로 정하는 (c) 임계값. points=[(P(true), 정답 true 여부)].
    hi 이상이면 '충족'(보정 세트의 실제 불일치가 하나도 넘지 않는 선), lo 이하면 '위반'(실제 일치가 하나도 내려가지 않는 선), 사이는 '확인 필요'."""
    pos = [p for p, t in points if t]
    neg = [p for p, t in points if not t]
    if not pos or not neg:
        return None
    hi, lo = min(1.0, max(neg) + 1e-6), max(0.0, min(pos) - 1e-6)
    if lo >= hi:  # 완전히 갈리면 가운데 한 선
        t = (max(neg) + min(pos)) / 2
        return {"lo": t, "hi": t, "separable": True}
    return {"lo": lo, "hi": hi, "separable": False}


# ------------------------------------------------------------------ 설계 비교
def model_status(it, signal, th):
    """(상태, 근거 박스 유무). 답이 없거나 거절이면 (None, False)."""
    if signal == "direct":
        p = prob(it["c"])
        if p is None or th is None:
            return None, False
        return ("pass" if p >= th["hi"] else "fail" if p <= th["lo"] else "review"), False
    res = line_rule(it["cond"], it["rows"], it["b"])
    return (None, False) if res is None else (res["status"], bool(res["evidence_box"]))


def design_eval(ds, items, design, signal=None, th=None):
    """설계 하나로 세트 판정을 다시 만들고 sim_check.evaluate로 채점한다(r4 판정 파일은 건드리지 않는다)."""
    by_set = {}
    for it in items:
        by_set.setdefault(it["set"], []).append(it)
    sets = [s for s in ds["sets"] if s["id"] in by_set]
    checks, stats = {}, {"model_decided": 0, "no_box": 0, "changed": 0, "flags": 0, "useful_flags": 0, "noise_flags": 0, "r4_wrong": 0,
                         "suggestions": 0, "suggestions_correct": 0, "sets_flagged": 0}
    for s in sets:
        status = {r["key"]: r["status"] for r in ds["checks"][s["id"]]["results"] if r["kind"] == "info"}
        expected = {r["key"]: r["expected"] for r in ds["checks"][s["id"]]["results"]}
        flagged = False
        for it in by_set[s["id"]]:
            r4s = it["r4"] or "review"
            new = r4s
            if design in ("model_first", "second_opinion", "parallel"):
                st, box = model_status(it, signal, th)
                if design == "model_first" or (design == "second_opinion" and r4s == "review"):
                    new = st or "review"
                    if new != "review":
                        stats["model_decided"] += 1
                        stats["no_box"] += not box
                elif design == "parallel":  # 판정은 r4 그대로, 모델 의견만 옆에 띄운다
                    r4_right = r4s != "review" and (r4s == "fail") == it["should_fail"]
                    stats["r4_wrong"] += r4s != "review" and not r4_right
                    if r4s != "review" and st in ("pass", "fail") and st != r4s:
                        stats["flags"] += 1
                        stats["useful_flags"] += not r4_right
                        stats["noise_flags"] += r4_right
                        flagged = True
                    if r4s == "review" and st in ("pass", "fail"):
                        stats["suggestions"] += 1
                        stats["suggestions_correct"] += (st == "fail") == it["should_fail"]
            elif design == "doc_gate":  # 기준 값 자체가 의심되면 '위반'을 보류(충족은 그대로)
                p = prob(it["a"])
                if p is not None and p < .5 and r4s == "fail":
                    new = "review"
            stats["changed"] += new != r4s
            status[it["field"]] = new
        stats["sets_flagged"] += flagged
        results = [{"key": k, "status": v, "expected": expected.get(k)} for k, v in status.items()]
        fails = any(v == "fail" for v in status.values())
        verdict = "fail" if fails else ("review" if any(v == "review" for v in status.values()) else "pass")
        checks[s["id"]] = {"predicted_mismatch": fails, "verdict": verdict, "results": results}
    ev = sc.evaluate(sets, checks)
    verdicts = [(s["label"], checks[s["id"]]["verdict"]) for s in sets]
    return {"design": design, "signal": signal, "thresholds": th, "n": ev["n"], "confusion": ev["confusion"], "accuracy": ev["accuracy"],
            "recall": ev["recall"], "precision": ev["precision"],
            "caught_field": sum(t["caught_field"] for t in ev["by_type"].values()),
            "review_sets": sum(v == "review" for _, v in verdicts), "match_review_sets": sum(l == "match" and v == "review" for l, v in verdicts),
            "missed_as_pass": sum(l == "mismatch" and v == "pass" for l, v in verdicts), **stats}


def designs_for(ds, items, thresholds):
    rows = [design_eval(ds, items, "r4"), design_eval(ds, items, "doc_gate")]
    for design in ("model_first", "second_opinion", "parallel"):
        for name, th in thresholds.items():
            if th is not None:
                rows.append({**design_eval(ds, items, design, "direct", th), "threshold_name": name})
        rows.append(design_eval(ds, items, design, "line"))
    return rows


# ------------------------------------------------------------------ 보고
def score(names=("v1", "v2"), out_dir=OUT):
    data = {n: load_dataset(n, out_dir) for n in names}
    items = {n: item_rows(data[n]) for n in names}
    drows = {n: doc_rows(data[n]) for n in names}
    counts = {n: {o: len(data[n]["responses"][o]) for o in ("ocr", "shuffled")} for n in names}
    report = {"rules": sc.RULES, "responses": counts}
    if not any(c["ocr"] for c in counts.values()):
        report["status"] = "no_responses"
        return report
    all_items = [it for n in names for it in items[n]]
    all_docs = [r for n in names for r in drows[n]]
    thresholds = {}
    for n in names:
        other = [(prob(it["c"]), it["truth_c"]) for m in names if m != n for it in items[m] if prob(it["c"]) is not None]
        thresholds[n] = {"fixed_0.5": {"lo": .5, "hi": .5, "separable": None}, "cross": calibrate(other)}
    report.update({
        "status": "ok",
        "miscorrection": {"all": miscorrection(all_items), **{n: miscorrection(items[n]) for n in names}},
        "doc_check": doc_check(all_docs),
        "line_check": {"all": line_check(all_items), **{n: line_check(items[n]) for n in names}},
        "cases": case_view(all_items),
        "auroc": {n: auroc_block(data[n], items[n], drows[n]) for n in names} | {"all": auroc_block(None, all_items, all_docs)},
        "brier_c": brier(all_items),
        "order": {n: order_check(data[n]) for n in names},
        "legibility": legibility(all_items),
        "thresholds": thresholds,
        "designs": {n: designs_for(data[n], items[n], thresholds[n]) for n in names if items[n]},
    })
    return report


def auroc_block(ds, items, drows):
    out = {"c_item": auroc([1 - prob(it["c"]) for it in items if not it["truth_c"] and prob(it["c"]) is not None],
                           [1 - prob(it["c"]) for it in items if it["truth_c"] and prob(it["c"]) is not None]),
           "a_item": auroc([1 - r["p"] for r in drows if not r["ok"] and r["p"] is not None], [1 - r["p"] for r in drows if r["ok"] and r["p"] is not None])}
    if ds is not None:
        per_set = {}
        for it in items:
            p = prob(it["c"])
            if p is not None:
                per_set.setdefault(it["set"], []).append(1 - p)
        labels = {s["id"]: s["label"] for s in ds["sets"]}
        out["c_set"] = auroc([max(v) for k, v in per_set.items() if labels[k] == "mismatch"], [max(v) for k, v in per_set.items() if labels[k] == "match"])
        r4 = {sid: {"fail": 1.0, "review": .5, "pass": 0.0}[c["verdict"]] for sid, c in ds["checks"].items() if sid in per_set}
        out["r4_set_ordinal"] = auroc([v for k, v in r4.items() if labels[k] == "mismatch"], [v for k, v in r4.items() if labels[k] == "match"])
    return out


def brier(items):
    pts = [(prob(it["c"]), it["truth_c"]) for it in items if prob(it["c"]) is not None]
    return statistics.mean((p - t) ** 2 for p, t in pts) if pts else None


def oracle(names=("v1", "v2")):
    """모델 호출 없이 정답을 모델 답 자리에 넣은 상한: (b)는 정답 줄(확률 1), (a)는 문서 OCR이 맞으면 1·틀리면 0.
    '줄 고르기 + r4 규칙'과 '기준 문서 확인' 설계가 모델이 다 맞혀도 낼 수 있는 최선을 본다."""
    from .decisions_shadow import prepare
    out = {}
    for n in names:
        ds = load_dataset(n)
        ds["responses"] = {"ocr": {}, "shuffled": {}}
        for s in ds["sets"]:
            if s["id"] not in ds["checks"]:
                continue
            _, meta = prepare(n, s["id"])
            perfect = []
            for f in meta["items"]:
                gold = gold_lines(f, s["poster"], meta["rows"])
                if gold:
                    pick = next(r["value"] for r in meta["rows"] if r["value"] in gold)
                    perfect.append({"type": "choice", "name": f"b_line_{f}", "choice": pick, "probabilities": [{"value": pick, "probability": 1.0}]})
            for q in meta["questions"]:
                if q["probe"] == "a":
                    ok = key(q["value"]) == key(doc_truth_raw(s["document"], q["item"]))
                    perfect.append({"type": "predicate", "name": q["name"], "probability": 1.0 if ok else 0.0})
            ds["responses"]["ocr"][s["id"]] = {"meta": meta, "response": {"answers": perfect}}
        items = item_rows(ds)
        changed = [f"{it['set']} {it['field']}: {STATUS_KO[it['r4']]} → {STATUS_KO[model_status(it, 'line', None)[0]]}"
                   for it in items if model_status(it, "line", None)[0] != it["r4"]]
        gated = [f"{it['set']} {it['field']}: {STATUS_KO[it['r4']]} → 확인 필요" for it in items if prob(it["a"]) == 0.0 and it["r4"] == "fail"]
        out[n] = {"designs": [design_eval(ds, items, "r4"), design_eval(ds, items, "model_first", "line"), design_eval(ds, items, "second_opinion", "line"),
                              design_eval(ds, items, "doc_gate")],
                  "changed_items": {"model_first": changed, "doc_gate": gated}}
    return out


def oracle_markdown(result):
    lines = ["# 상한(정답을 모델 답 자리에 넣은 경우, 모델 호출 없음)", "", "| 데이터셋 | 설계 | 신호 | 정확도 | 재현율 | 정밀도 | 확인 필요 세트 | r4와 달라진 항목 |",
             "|---|---|---|---:|---:|---:|---:|---|"]
    for n, r in result.items():
        for row in r["designs"]:
            sig = {"line": "정답 줄", None: "정답 문서 확인" if row["design"] == "doc_gate" else "—"}[row["signal"]]
            lines.append(f"| {n} | {DESIGNS[row['design']]} | {sig} | {pct(row['accuracy'])} | {pct(row['recall'])} | {pct(row['precision'])} | "
                         f"{row['review_sets']} | {'; '.join(r['changed_items'].get(row['design'], []))} |")
    return "\n".join(lines)


def pct(x):
    return "—" if x is None else f"{x:.1%}"


def num(x, d=3):
    return "—" if x is None else f"{x:.{d}f}"


def to_markdown(report):
    lines = [f"# Decisions API 섀도 실험 채점 결과", "", f"r4 규칙: `{report['rules']}` · 응답 수: " +
             ", ".join(f"{n} {c['ocr']}세트(순서 바꿈 {c['shuffled']})" for n, c in report["responses"].items()), ""]
    if report.get("status") != "ok":
        lines += ["모델 응답이 없습니다. `--call`로 호출한 뒤 다시 채점하세요.", ""]
        return "\n".join(lines)
    m = report["miscorrection"]["all"]
    lines += ["## 1. 잘못 고친 비율((c), 일부러 넣은 차이에 P(true)>0.5)", "", "| 유형 | 묶음 | 답한 수 | 잘못 고침 | 비율 |", "|---|---|---:|---:|---:|"]
    for t, v in sorted(m["by_type"].items()):
        lines.append(f"| {v['label']} | {v['group']} | {v['answered']} | {v['miscorrected']} | {pct(v['rate'])} |")
    lines += ["", "| 묶음 | 답한 수 | 잘못 고침 | 비율 |", "|---|---:|---:|---:|"]
    lines += [f"| {g} | {v['answered']} | {v['miscorrected']} | {pct(v['rate'])} |" for g, v in m["by_group"].items()]
    fa = m["false_alarm"]
    lines += ["", f"일치 항목에 P(true)≤0.5(헛경보): {fa['p_le_half']}/{fa['answered']} ({pct(fa['rate'])}). "
              f"문서 OCR 오독이 겹쳐 제외한 항목: {', '.join(m['excluded_doc_misread']) or '없음'}.", ""]
    d = report["doc_check"]
    lines += ["## 2. 기준 문서 확인((a))", "", f"문서 OCR 오독 {d['caught']}/{d['n']}건을 P(true)<0.5로 잡음 "
              f"(알려진 6건 목록과 일치: {'예' if d['matches_known_list'] else '아니오'}). 맞게 읽은 값에 대한 헛경보 "
              f"{d['false_alarm']['p_lt_half']}/{d['false_alarm']['answered']} ({pct(d['false_alarm']['rate'])}), AUROC {num(d['auroc'])}.", "",
              "| 세트 | 문서 OCR | 정답 | P(true) | 잡음 |", "|---|---|---|---:|---|"]
    lines += [f"| {c['dataset']} {c['set']} | {c['ocr']} | {c['truth']} | {num(c['p_true'])} | {'예' if c['caught'] else '아니오'} |" for c in d["misreads"]]
    lc = report["line_check"]["all"]
    lines += ["", "## 3. 줄 고르기((b))", "", f"정답 줄을 고른 비율 {lc['correct']}/{lc['n']} ({pct(lc['accuracy'])}), 선택 확률 평균 {num(lc['mean_top_probability'])} "
              f"→ 과신 {num(lc['overconfidence'])}(평균 확률 − 정확도).", "", "| 항목 | 맞춤 | 비율 |", "|---|---:|---:|"]
    lines += [f"| {sc.CONDITION_LABEL[f]} | {v['correct']}/{v['n']} | {pct(v['accuracy'])} |" for f, v in lc["by_field"].items()]
    lines += ["", "## 4. 알려진 r4 오답 사례", "", "| 사례 | r4 | (a) P | (b) 고른 줄 | (b)+규칙 | (c) P | (d) |", "|---|---|---:|---|---|---:|---:|"]
    for c in report["cases"]:
        lines.append(f"| {c['case']} — {c['note']} | {STATUS_KO.get(c['r4'])} | {num(c['a_p_true'])} | {c['b_choice']} ({num(c['b_p'], 2)}, "
                     f"{'정답' if c['b_correct'] else '오답' if c['b_correct'] is False else '—'}) | {STATUS_KO.get(c['b_rule'])} | {num(c['c_p_true'])} | {num(c['d_score'], 2)} |")
    lines += ["", "## 5. AUROC·보정", "", "| 범위 | (c) 항목 | (a) 항목 | (c) 세트 | r4 세트(순서형) |", "|---|---:|---:|---:|---:|"]
    for n, a in report["auroc"].items():
        lines.append(f"| {n} | {num(a['c_item'])} | {num(a['a_item'])} | {num(a.get('c_set'))} | {num(a.get('r4_set_ordinal'))} |")
    lines += ["", f"(c) Brier 점수 {num(report['brier_c'])}.", "", "## 6. 보기 순서 민감도", "",
              "| 데이터셋 | 세트 | 줄 고르기 답 바뀜 | 같은 보기 확률 변화 평균 | predicate 0.5 넘나듦(재실행) | score 변화 평균 |", "|---|---:|---:|---:|---:|---:|"]
    for n, o in report["order"].items():
        lines.append(f"| {n} | {o['sets']} | {o['choice']['flips']}/{o['choice']['n']} ({pct(o['choice']['flip_rate'])}) | {num(o['choice']['mean_abs_dp'])} | "
                     f"{o['predicate_rerun']['cross_half']}/{o['predicate_rerun']['n']} ({pct(o['predicate_rerun']['cross_rate'])}) | {num(o['score_rerun']['mean_abs_d'], 2)} |")
    lines += ["", "## 7. 세 설계 비교(세트 판정)", "",
              "(c) 임계값: `fixed_0.5`는 0.5 한 선, `cross`는 다른 데이터셋 정답으로 정한 선(v1으로 정해 v2에 적용, 반대도 같음).", ""]
    for n, rows in report["designs"].items():
        th = report["thresholds"][n]["cross"]
        lines += [f"### {n}" + (f" (cross 임계값: 위반 ≤ {th['lo']:.3f}, 충족 ≥ {th['hi']:.3f})" if th else ""), "",
                  "| 설계 | 신호 | 정확도 | 재현율 | 정밀도 | 확인 필요 세트 | 놓쳐서 충족 | 모델이 정한 항목 | 근거 박스 없음 | 플래그(유용/잡음) |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
        for r in rows:
            sig = SIGNALS.get(r["signal"], "—") + (f" `{r['threshold_name']}`" if r.get("threshold_name") else "")
            flags = f"{r['flags']} ({r['useful_flags']}/{r['noise_flags']}), 제안 {r['suggestions_correct']}/{r['suggestions']} 맞음" if r["design"] == "parallel" else ""
            lines.append(f"| {DESIGNS[r['design']]} | {sig} | {pct(r['accuracy'])} | {pct(r['recall'])} | {pct(r['precision'])} | {r['review_sets']} | "
                         f"{r['missed_as_pass']} | {r['model_decided']} | {r['no_box']} | {flags} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", nargs="+", choices=sorted(DATASETS), default=sorted(DATASETS))
    parser.add_argument("--out", type=Path, default=OUT, help="응답 폴더(decisions_shadow --out과 같게)")
    parser.add_argument("--json", type=Path, help="채점 결과 JSON(기본: <out>/score.json)")
    parser.add_argument("--md", type=Path, help="요약 마크다운을 이 파일에 쓴다")
    parser.add_argument("--oracle", action="store_true", help="모델 없이 정답을 넣은 상한((b) 정답 줄, (a) 정답 문서 확인)만 계산")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.oracle:
        result = oracle(tuple(args.dataset))
        print(oracle_markdown(result))
        return result
    report = score(tuple(args.dataset), args.out)
    path = args.json or args.out / "score.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    md = to_markdown(report)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(md + "\n", encoding="utf-8")
    print(md)
    return report


if __name__ == "__main__":
    main()
