"""가상 포스터 검수 실험 대시보드(크리에이티브체커 화면 형식). localhost 전용, GET만 받는다.

OCR 작업자(ocr_worker.py)가 쓰는 결과 파일을 보고 세트별 단계를 계산하고, 문서·포스터 OCR이 모두 끝난 세트는 바로 대조(sim_check)한다.
조회(GET) 중에 대조 결과 캐시(data/runtime/poster-ocr/sim/checks)를 새로 쓸 수 있다. 대조 규칙(sim_check.py)을 고치면 다시 대조한다.
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_dashboard --port 8766
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.parse import unquote, urlparse

from . import sim_check
from .sim_check import CHECKS, RUNS, SIM, VARIANT, load

PAGE = Path(__file__).with_name("static") / "sim_dashboard.html"
LOCK = threading.Lock()
CODE = {"mtime": None}
STALL_SECONDS = 600  # 이 시간 동안 새 결과가 없으면 작업자가 멈춘 것으로 본다


def mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def ocr_payload(case_id):
    path = RUNS / case_id / f"{VARIANT}.json"
    if not path.exists():
        return None, None
    try:
        return load(path), mtime(path)
    except ValueError:  # 쓰는 중이거나 깨진 파일은 이 사례만 '읽는 중'으로 본다
        return None, None


def manifest_sha():
    return {c["id"]: c["sha256"] for c in load(SIM / "manifest.json")["cases"]}


def ok_payload(case_id, sha=None):
    """OCR이 성공했고, 지금 원본(매니페스트 해시)을 읽은 결과인가."""
    payload, _ = ocr_payload(case_id)
    sha = sha or manifest_sha()
    return bool(payload) and payload.get("status") == "success" and payload.get("source_sha256") == sha.get(case_id)


def ensure_check(set_id, sha=None):
    """두 OCR이 성공했는데 대조 결과가 없거나 낡았으면 다시 대조한다."""
    doc_t, poster_t = mtime(RUNS / f"{set_id}-doc" / f"{VARIANT}.json"), mtime(RUNS / f"{set_id}-poster" / f"{VARIANT}.json")
    sha = sha or manifest_sha()
    if not (doc_t and poster_t and ok_payload(f"{set_id}-doc", sha) and ok_payload(f"{set_id}-poster", sha)):
        return None
    path = CHECKS / f"{set_id}.json"
    code_t = mtime(Path(sim_check.__file__))
    with LOCK:
        if CODE["mtime"] != code_t:  # 대조 규칙을 고치면 서버를 다시 띄우지 않아도 새 규칙으로 다시 대조한다
            if CODE["mtime"] is not None:
                importlib.reload(sim_check)
            CODE["mtime"] = code_t
        if not path.exists() or mtime(path) < max(doc_t, poster_t, code_t or 0) or load(path).get("rules") != sim_check.RULES:
            return sim_check.check_set(set_id)
    return load(path)


def history(report):
    """규칙 버전별 채점: 보관한 eval-<규칙>.json + 지금 규칙."""
    rows = []
    for path in sorted((RUNS.parents[1]).glob("eval-r*.json")):
        r = load(path)
        if r.get("rules") == sim_check.RULES:
            continue
        rows.append({"rules": r.get("rules", path.stem[5:]), "note": r.get("note", ""), **{k: r[k] for k in ("n", "confusion", "accuracy")},
                     "field_hit": sum(x["field_hit"] for x in r["rows"]), "wrong_field": sum(r["field_false_alarms"].values())})
    rows.append({"rules": sim_check.RULES, "note": "현재 규칙 · 같은 40세트를 보고 고침(낙관적)", "current": True,
                 **{k: report[k] for k in ("n", "confusion", "accuracy")},
                 "field_hit": sum(x["field_hit"] for x in report["rows"]), "wrong_field": sum(report["field_false_alarms"].values())})
    return rows


def state():
    data = load(SIM / "sets.json")
    sets, cases = data["sets"], load(SIM / "manifest.json")["cases"]
    sha = {c["id"]: c["sha256"] for c in cases}
    run_path, final_path = RUNS / "run.json", RUNS / f"run.{VARIANT}.json"
    run_info = load(run_path) if run_path.exists() else None
    payloads = {c["id"]: ocr_payload(c["id"])[0] for c in cases}
    done_cases = {c["id"]: mtime(RUNS / c["id"] / f"{VARIANT}.json") if payloads[c["id"]] else None for c in cases}
    failed_cases = {c["id"] for c in cases if payloads[c["id"]] and payloads[c["id"]].get("status") != "success"}
    stale_cases = {c["id"] for c in cases if payloads[c["id"]] and c["id"] not in failed_cases and payloads[c["id"]].get("source_sha256") != sha[c["id"]]}
    pending = [c["id"] for c in cases if not done_cases[c["id"]]]
    finished_run = bool(run_info) and (mtime(final_path) or 0) >= (mtime(run_path) or 0)
    last_activity = max([t for t in done_cases.values() if t] + [mtime(run_path) or 0])
    stalled = bool(run_info) and not finished_run and bool(pending) and time.time() - last_activity > STALL_SECONDS
    running = bool(run_info) and not finished_run and bool(pending) and not stalled
    current = pending[0] if running else None
    seconds = []
    for c in cases:
        if done_cases[c["id"]] and c["id"] not in failed_cases:
            payload = payloads[c["id"]]
            if payload.get("inference_seconds"):
                seconds.append((c["kind"], payload["inference_seconds"]))
    avg = {k: (sum(s for kk, s in seconds if kk == k) / max(1, sum(1 for kk, _ in seconds if kk == k))) or None for k in ("document", "poster")}
    eta = sum((avg["document"] if p.endswith("-doc") else avg["poster"]) or 45 for p in pending) if running else 0
    items, checks, events = [], {}, []
    for s in sets:
        sid = s["id"]
        doc_t, poster_t = done_cases[f"{sid}-doc"], done_cases[f"{sid}-poster"]
        check = ensure_check(sid, sha)
        if f"{sid}-doc" in failed_cases or f"{sid}-poster" in failed_cases:
            stage = "failed"
        elif f"{sid}-doc" in stale_cases or f"{sid}-poster" in stale_cases:
            stage = "stale"
        elif check:
            checks[sid] = check
            stage = "done"
        elif doc_t and current == f"{sid}-poster":
            stage = "poster_ocr"
        elif doc_t:
            stage = "criteria"
        elif current == f"{sid}-doc":
            stage = "doc_ocr"
        else:
            stage = "queued"
        if doc_t:
            events.append((doc_t, sid, "doc"))
        if poster_t:
            events.append((poster_t, sid, "poster"))
        fails = [r["key"] for r in check["results"] if r["status"] == "fail"] if check else []
        items.append({"id": sid, "title": s["document"]["official"], "region": s["document"]["region"], "stage": stage,
                      "poster": s["files"]["poster"], "poster_sha": sha[f"{sid}-poster"][:12], "counts": check["counts"] if check else None,
                      "verdict": check["verdict"] if check else None, "checked_at": check["checked_at"] if check else None,
                      "truth": s["label"], "mismatch": s["mismatch"], "predicted_mismatch": check["predicted_mismatch"] if check else None,
                      "field_hit": bool(check and s["mismatch"] and s["mismatch"]["field"] in fails), "finished_at": poster_t})
    log = []
    for t, sid, kind in sorted(events, reverse=True)[:14]:
        c = checks.get(sid)
        if kind == "poster" and c:
            log.append({"t": t, "set": sid, "text": f"항목 검수 완료 · 충족 {c['counts']['pass']} · 위반 {c['counts']['fail']} · 확인 필요 {c['counts']['review']}"})
        case = f"{sid}-{'poster' if kind == 'poster' else 'doc'}"
        what = "포스터" if kind == "poster" else "기준 문서"
        log.append({"t": t, "set": sid, "text": f"{what} 읽기 실패" if case in failed_cases else f"{what} 원본이 바뀌어 다시 읽어야 함" if case in stale_cases
                    else "포스터 문구와 위치 읽기 완료" if kind == "poster" else "기준 문서 읽기 완료 · 검수 기준 추출"})
    report = sim_check.evaluate([s for s in sets if s["id"] in checks], checks)
    match_review = sum(1 for s in sets if s["label"] == "match" and s["id"] in checks and not checks[s["id"]]["predicted_mismatch"]
                       and checks[s["id"]]["verdict"] == "review")
    return {"now": time.time(), "history": history(report), "rules": sim_check.RULES, "running": running, "stalled": stalled,
            "finished": finished_run and not pending and not failed_cases and not stale_cases, "finished_with_problems": finished_run and not pending and bool(failed_cases or stale_cases),
            "stale": sorted(stale_cases),
            "started": bool(run_info), "started_at": run_info.get("started_at") if run_info else None,
            "load_seconds": run_info.get("load_seconds") if run_info else None, "engine": (run_info or {}).get("identity", {}),
            "current": current, "eta_seconds": round(eta), "avg_seconds": avg, "failed": sorted(failed_cases),
            "cases_done": len(cases) - len(pending), "cases_total": len(cases), "sets_done": len(checks), "sets_total": len(sets),
            "items": items, "log": log[:14], "report": report, "match_review": match_review, "meta": {k: v for k, v in data.items() if k != "sets"}}


def detail(set_id):
    s = next((x for x in load(SIM / "sets.json")["sets"] if x["id"] == set_id), None)
    if not s:
        return None
    doc, _ = ocr_payload(f"{set_id}-doc")
    poster, _ = ocr_payload(f"{set_id}-poster")
    check = ensure_check(set_id)
    slim = lambda p: {"size": p["normalized_dimensions"], "seconds": p.get("inference_seconds"), "status": p.get("status"), "error": p.get("error"),
                      "records": [{"id": r["id"], "text": r["text"], "confidence": round(r["confidence"], 3), "bbox": r["bbox_xyxy"]} for r in p["records"]]} if p else None
    sha = {c["id"]: c["sha256"][:12] for c in load(SIM / "manifest.json")["cases"]}
    return {"set": s, "document_ocr": slim(doc), "poster_ocr": slim(poster), "check": check,
            "sha": {"poster": sha[f"{set_id}-poster"], "document": sha[f"{set_id}-doc"]}}


class Handler(BaseHTTPRequestHandler):
    allowed_hosts = set()

    def log_message(self, *args):
        pass

    def send(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "max-age=3600" if kind == "image/png" else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, body):
        return self.send(code, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.headers.get("Host", "") not in self.allowed_hosts:  # DNS 리바인딩 차단
            return self.send_json(403, {"error": "forbidden host"})
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send_json(200, state())
            if path.startswith("/api/set/"):
                body = detail(path.rsplit("/", 1)[1])
                return self.send_json(200 if body else 404, body or {"error": "not found"})
            if path.startswith("/files/") and "\0" not in path:
                target = (SIM.parents[2] / path[len("/files/"):]).resolve()
                if target.suffix == ".png" and SIM.resolve() in target.parents and target.exists():
                    return self.send(200, target.read_bytes(), "image/png")
            return self.send(404, b"not found", "text/plain; charset=utf-8")
        except Exception:  # 대시보드는 계속 떠 있어야 한다. 자세한 내용은 서버 로그에만 남긴다
            traceback.print_exc(file=sys.stderr)
            return self.send_json(500, {"error": "server error"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    Handler.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
