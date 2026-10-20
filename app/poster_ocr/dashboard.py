"""포스터 OCR 실험 실시간 대시보드(로컬 전용, 127.0.0.1).

실행: .\\.venv\\Scripts\\python.exe -m app.poster_ocr.dashboard [--port 8765] [--journal <workflow journal.jsonl>]
파일만 읽는다(라벨·OCR·로그). 실험 프로세스를 시작·중단하지 않는다. 포스터 원본은 이 컴퓨터의 브라우저에만 보여 준다.
test 분할의 정답 대조와 지표는 규칙 고정 표시(data/runtime/poster-ocr/FREEZE)가 생기기 전까지 잠근다.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .evaluate import compare, gold_texts, key, ocr_text, semiglobal
from .extract import extract
from .report_style import CSS
from . import human_review
from .review_page import REVIEW_PAGE

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
GOLD = ROOT / "data/benchmarks/gold-v2"
BLIND = ROOT / "data/benchmarks/gold-v2-blind"
RUNS = ROOT / "data/runtime/poster-ocr/runs"
FREEZE = ROOT / "data/runtime/poster-ocr/FREEZE"
FREEZE_V2 = ROOT / "data/runtime/poster-ocr/FREEZE_V2"
LOGS = ROOT / ".runtime/logs"
PIPELINES = [("easyocr", "EasyOCR"), ("paddleocr", "PaddleOCR"), ("hybrid", "EasyOCR+Paddle 재인식")]
VARIANTS = ["rgb_2x", "rgb_1x"]
LOG_FILES = ["chain.log", "run-easyocr.log", "run-paddle-2x.log", "run-hybrid-rec.log", "run-paddle-1x.log", "chain-reserve.log",
             "run-reserve-easyocr.log", "run-reserve-paddle.log"]
METRIC_FIELDS = [("core_all", "핵심 4필드"), ("title", "축제명"), ("schedule_pair", "일정"), ("venue", "장소"), ("year", "연도")]


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_SUMMARY = {}


def summary(path, make):
    """파일 수정 시각이 같으면 이전 요약을 다시 쓴다(3초마다 수백 개 JSON을 다시 읽지 않도록)."""
    try:
        stamp = Path(path).stat().st_mtime
    except OSError:
        return None
    cached = _SUMMARY.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    payload = read_json(path)
    value = make(payload) if payload else None
    _SUMMARY[path] = (stamp, value)
    return value


class SystemMeter:
    """ctypes로 Windows CPU·메모리 사용률을 읽는다(추가 패키지 없음)."""
    class FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    def __init__(self):
        self.previous = self._times()

    def _times(self):
        if sys.platform != "win32":
            return None
        idle, kernel, user = self.FILETIME(), self.FILETIME(), self.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        value = lambda ft: (ft.high << 32) | ft.low
        return value(idle), value(kernel), value(user)

    def sample(self):
        if sys.platform != "win32":
            return {}
        now = self._times()
        idle, kernel, user = (b - a for a, b in zip(self.previous, now))
        self.previous = now
        busy = kernel + user
        memory = self.MEMORYSTATUSEX()
        memory.dwLength = ctypes.sizeof(memory)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
        return {"cpu_percent": round(100 * (busy - idle) / busy, 1) if busy else None,
                "mem_total_gb": round(memory.ullTotalPhys / 2**30, 1),
                "mem_used_gb": round((memory.ullTotalPhys - memory.ullAvailPhys) / 2**30, 1)}


class State:
    def __init__(self, journals):
        self.journals = [Path(j) for j in (journals or [])]
        self.meter = SystemMeter()
        self.cases = {}
        self.cache = {}  # (ocr path) -> (mtimes, row)
        self.status = {"updated_at": None}
        self.lock = threading.Lock()

    def load_manifest(self):
        manifest = read_json(MANIFEST) or {"cases": []}
        self.cases = {c["id"]: c for c in manifest["cases"] if c["split"] in ("dev", "test")}
        self.reserve = {c["id"]: c for c in manifest["cases"] if c["split"] == "reserve"}
        self.all = {**self.cases, **self.reserve}

    # ------------------------------------------------------------ labels
    def labels(self):
        files = sorted(GOLD.glob("*.poster.json"))
        counts = {"main": [0, 0], "reserve": [0, 0]}
        for path in files:
            version = summary(path, lambda doc: "" if any(doc[s]["status"] == "not_checked" for s in ("title", "schedule", "venue"))
                              else ((doc.get("extraction", {}).get("extractor") or {}).get("version") or "pass1"))
            if not version:
                continue  # 빈 문서(작성 중)는 세지 않는다.
            group = "reserve" if path.name.split(".")[0] in self.reserve else "main"
            counts[group][0] += 1
            counts[group][1] += version.endswith("pass2-reviewed")
        (pass1, reviewed), (r_pass1, r_reviewed) = counts["main"], counts["reserve"]
        recent = [p.name.split(".")[0] for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:6]]
        seen = {}
        for journal in [j for j in self.journals if j.exists()]:
            for line in journal.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "started" and event.get("label"):
                    seen[event["key"]] = {"label": event["label"], "phase": event.get("phase"), "status": "running"}
                elif event.get("type") == "result" and event.get("key") in seen:
                    seen[event["key"]]["status"] = "done"
        agents = list(seen.values())
        return {"pass1": pass1, "reviewed": reviewed, "blind": len(list(BLIND.glob("*.poster.json"))), "total": len(self.cases),
                "blind_total": 30, "recent": recent, "agents": agents, "reserve_pass1": r_pass1, "reserve_reviewed": r_reviewed,
                "reserve_total": len(self.reserve)}

    # ------------------------------------------------------------ OCR progress
    def ocr(self):
        now = time.time()
        rows, latest = [], None
        for group, ids in (("main", self.cases), ("reserve", self.reserve)):
          for engine, name in PIPELINES:
            for variant in VARIANTS:
                files = [p for p in (RUNS / engine).glob(f"*/{variant}.json") if p.parent.name in ids]
                done, failed, seconds, stamps = 0, 0, [], []
                for path in files:
                    payload = summary(path, lambda d: {"case_id": d["case_id"], "status": d.get("status"),
                                                       "inference_seconds": d.get("inference_seconds")})
                    if not payload:
                        continue
                    stamp = path.stat().st_mtime
                    stamps.append((stamp, payload["case_id"]))
                    if payload.get("status") == "success":
                        done += 1
                        if payload.get("inference_seconds") is not None:
                            seconds.append((stamp, payload["inference_seconds"]))
                    else:
                        failed += 1
                total = len(ids)
                stamps.sort()
                recent = [s for _, s in sorted(seconds)[-20:]]
                mean = sum(recent) / len(recent) if recent else None
                last = stamps[-1] if stamps else None
                running = bool(last and now - last[0] < 240 and done + failed < total)
                if engine == "hybrid":  # 혼합 경로는 crop을 한 번에 인식한 뒤 병합하므로 진행률 대신 완료 여부를 본다.
                    running = (LOGS / ("run-hybrid-rec.log" if group == "main" else "run-reserve-hybrid-rec.log")).exists() and done == 0
                state = "done" if done + failed >= total and total else ("running" if running else ("partial" if done else "queued"))
                gaps = [b[0] - a[0] for a, b in zip(stamps[-21:], stamps[-20:])]
                pace = sum(gaps) / len(gaps) if gaps else mean
                rows.append({"key": f"{group}:{engine}/{variant}", "group": group, "engine": name, "variant": variant, "done": done, "failed": failed, "total": total,
                             "mean_s": round(mean, 1) if mean else None, "state": state,
                             "eta_s": round(pace * (total - done - failed)) if state == "running" and pace else None,
                             "last_case": last[1] if last else None,
                             "last_at": datetime.fromtimestamp(last[0]).strftime("%H:%M:%S") if last else None})
                if last and (latest is None or last[0] > latest[0]):
                    latest = (last[0], engine, variant, last[1])
        return rows, (None if latest is None else {"pipeline": latest[1], "variant": latest[2], "case_id": latest[3],
                                                    "at": datetime.fromtimestamp(latest[0]).strftime("%H:%M:%S")})

    # ------------------------------------------------------------ live metrics
    def row(self, case, engine, variant):
        ocr_path = RUNS / engine / case["id"] / f"{variant}.json"
        gold_path = GOLD / f"{case['id']}.poster.json"
        if not ocr_path.exists() or not gold_path.exists():
            return None
        stamp = (ocr_path.stat().st_mtime, gold_path.stat().st_mtime)
        cached = self.cache.get(ocr_path)
        if cached and cached[0] == stamp:
            return cached[1]
        payload, gold = read_json(ocr_path), read_json(gold_path)
        version = ((gold or {}).get("extraction", {}).get("extractor") or {}).get("version") or ""
        if not payload or payload.get("status") != "success" or not version.endswith("pass2-reviewed"):
            self.cache[ocr_path] = (stamp, None)
            return None
        image = {"sha256": case["sha256"], "width": case["image_meta"]["width"], "height": case["image_meta"]["height"]}
        pred = extract(payload["records"], image, payload["engine"], extracted_at="live")
        fields, _, _ = compare(gold, pred)
        text = ocr_text(payload["records"])
        reads = [max(0.0, 1 - semiglobal(key(t), text) / len(key(t))) for _, t in gold_texts(gold)]
        value = {"fields": fields, "reads": reads}
        self.cache[ocr_path] = (stamp, value)
        return value

    def metrics(self):
        frozen, frozen_v2 = FREEZE.exists(), FREEZE_V2.exists()
        out = {"frozen": frozen, "frozen_v2": frozen_v2, "splits": {}}
        for split in ["dev"] + (["test"] if frozen else []) + (["reserve"] if frozen_v2 else []):
            table = {}
            for engine, name in PIPELINES:
                for variant in VARIANTS:
                    rows = [r for c in self.all.values() if c["split"] == split for r in [self.row(c, engine, variant)] if r]
                    if not rows:
                        continue
                    entry = {"engine": name, "variant": variant, "n": len(rows)}
                    for field, _ in METRIC_FIELDS:
                        values = [r["fields"][field] for r in rows if field in r["fields"]]
                        entry[field] = [sum(values), len(values)]
                    reads = [x for r in rows for x in r["reads"]]
                    entry["read"] = round(sum(reads) / len(reads), 4) if reads else None
                    table[f"{engine}/{variant}"] = entry
            out["splits"][split] = table
        return out

    def logs(self):
        out = {}
        for name in LOG_FILES:
            path = LOGS / name
            if path.exists():
                lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                keep = [l for l in lines if not re.search(r"UserWarning|warnings\.warn|super\(\).__init__|ccache", l)]
                out[name] = keep[-8:]
        return out

    def refresh(self):
        self.load_manifest()
        ocr, latest = self.ocr()
        status = {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dataset": self.dataset(), "labels": self.labels(),
                  "ocr": ocr, "latest": latest, "metrics": self.metrics(), "logs": self.logs(), "system": self.meter.sample()}
        with self.lock:
            self.status = status

    def dataset(self):
        manifest = read_json(MANIFEST) or {"cases": [], "funnel": {}}
        splits = {}
        for c in manifest["cases"]:
            splits[c["split"]] = splits.get(c["split"], 0) + 1
        return {"splits": splits, "funnel": manifest.get("funnel", {})}

    def loop(self):
        while True:
            try:
                self.refresh()
            except Exception as error:  # 대시보드는 실험을 방해하지 않고 다음 주기에 다시 읽는다.
                with self.lock:
                    self.status["error"] = f"{type(error).__name__}: {error}"
            time.sleep(3)

    # ------------------------------------------------------------ case view
    def case_view(self, case_id, engine, variant):
        case = self.all.get(case_id)
        if not case or engine not in dict(PIPELINES) or variant not in VARIANTS:
            return None
        payload = read_json(RUNS / engine / case_id / f"{variant}.json")
        view = {"case_id": case_id, "split": case["split"], "listing_title": case["listing"]["title"],
                "width": case["image_meta"]["width"], "height": case["image_meta"]["height"], "image": f"/img/{case_id}",
                "records": [], "fields": [], "frozen": FREEZE.exists()}
        if not payload or payload.get("status") != "success":
            view["missing"] = True
            return view
        view["records"] = [{"id": r["id"], "text": r["text"], "confidence": round(r["confidence"], 3), "box": r["bbox_xyxy"],
                            "replaced": r.get("recognition_engine") == "paddleocr", "baseline": r.get("baseline_text")} for r in payload["records"]]
        view["inference_seconds"] = payload.get("inference_seconds")
        image = {"sha256": case["sha256"], "width": case["image_meta"]["width"], "height": case["image_meta"]["height"]}
        pred = extract(payload["records"], image, payload["engine"], extracted_at="live")
        gold = read_json(GOLD / f"{case_id}.poster.json")
        reviewed = bool(gold) and ((gold["extraction"].get("extractor") or {}).get("version") or "").endswith("pass2-reviewed")
        show_gold = reviewed and (case["split"] == "dev" or (case["split"] == "test" and FREEZE.exists())
                                  or (case["split"] == "reserve" and FREEZE_V2.exists()))
        fields = compare(gold, pred)[0] if show_gold else {}
        def value(doc, slot):
            if doc is None or doc[slot]["status"] != "present":
                return None if doc is None else f"({doc[slot]['status']})"
            s = doc[slot]
            if slot == "title":
                return s["official"]
            if slot == "schedule":
                start, end = s["start"], s["end"]
                fmt = lambda p: f"{p['year'] or '?'}-{p['month']:02d}-{p['day']:02d}" + (f"({p['weekday']})" if p["weekday"] else "")
                return fmt(start) + (" ~ " + fmt(end) if end else "")
            if slot in ("year", "edition"):
                return s["raw"]
            return s["raw"]
        for slot, label, metric in (("title", "축제명", "title"), ("schedule", "일정", "schedule_pair"), ("venue", "장소", "venue"),
                                    ("year", "연도", "year"), ("edition", "회차", "edition")):
            view["fields"].append({"label": label, "pred": value(pred, slot), "gold": value(gold, slot) if show_gold else None,
                                   "correct": fields.get(metric) if show_gold else None})
        for slot, label in (("organizations", "기관"), ("contacts", "연락처"), ("links", "링크")):
            items = pred[slot]["items"]
            text = ", ".join((f"{i.get('role_raw')} " if i.get("role_raw") else "") + (i.get("name") or i.get("value") or i.get("raw") or "") for i in items)
            view["fields"].append({"label": label, "pred": text or None, "gold": None, "correct": None})
        view["gold_shown"] = show_gold
        return view


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>포스터 OCR 실험 현황</title><style>__CSS__
main{max-width:76rem;gap:2rem}.bar{height:10px;background:var(--paper-2);border-radius:5px;overflow:hidden}.bar>span{display:block;height:100%;background:var(--seq);border-radius:0 5px 5px 0}
.prow{display:grid;grid-template-columns:13rem minmax(0,1fr) 15rem;gap:.9rem;align-items:center;padding:.45rem 0;border-bottom:1px solid var(--rule);font-size:.9rem}
.prow:last-child{border-bottom:0}.state{font-size:.75rem;padding:.05rem .45rem;border-radius:99px;border:1px solid var(--rule);margin-left:.4rem;color:var(--ink-2)}
.state.running{border-color:var(--seq);color:var(--accent)}.state.done{background:var(--paper-2)}.muted{color:var(--muted)}
.viewer{display:grid;grid-template-columns:minmax(0,22rem) minmax(0,1fr);gap:1.5rem;align-items:start}.stage{position:relative;background:var(--paper-2);border-radius:6px;overflow:hidden}
.stage img{display:block;width:100%;height:auto}.stage svg{position:absolute;inset:0;width:100%;height:100%}
.stage rect{fill:none;stroke:var(--s1);stroke-width:2;vector-effect:non-scaling-stroke}.stage rect.rep{stroke:var(--s3)}.stage rect.hot{stroke:var(--s2);stroke-width:3}
.lines{max-height:24rem;overflow:auto;font-size:.82rem;border:1px solid var(--rule);border-radius:6px;background:var(--sheet)}
.lines div{padding:.25rem .6rem;border-bottom:1px solid var(--rule);display:flex;gap:.6rem;justify-content:space-between}.lines div:hover{background:var(--accent-wash)}
.ok{color:#1a7f37;font-weight:600}.no{color:#c4352f;font-weight:600}.controls{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin-bottom:.8rem}
select,input{font:inherit;font-size:.85rem;padding:.3rem .5rem;border:1px solid var(--rule);border-radius:6px;background:var(--sheet);color:var(--ink)}
.logs pre{max-height:11rem}.panel{background:var(--sheet);border:1px solid var(--rule);border-radius:8px;padding:1rem 1.1rem}
.chips{display:flex;flex-wrap:wrap;gap:.35rem}.chip{font-size:.75rem;padding:.1rem .5rem;border-radius:99px;background:var(--paper-2);color:var(--ink-2)}
.chip.running{background:var(--accent-wash);color:var(--accent)}@media (max-width:760px){.viewer{grid-template-columns:1fr}.prow{grid-template-columns:1fr}}
</style></head><body><main>
<header class="intro"><p class="eyebrow"><span class="reg"></span>posters-v2 · 실시간</p><h1>포스터 OCR 실험 현황</h1>
<p class="lead">라벨 작업, 엔진별 OCR 진행, dev 기준 실시간 정확도와 방금 처리한 포스터를 3초마다 갱신합니다. 이 화면은 파일을 읽기만 합니다.</p>
<p class="muted" id="updated">불러오는 중…</p><p style="font-size:.88rem">보고서: <a href="/reports/dataset">데이터 전처리</a> · <a href="/reports/experiment">OCR 실험</a> · <a href="/review"><strong>정답 사람 검토</strong></a></p></header>
<section><div class="tiles" id="tiles"></div></section>
<section><h2>정답 라벨</h2><div class="panel"><div id="labels"></div></div></section>
<section><h2>OCR 실행</h2><div class="panel" id="ocr"></div></section>
<section><h2>실시간 정확도</h2><p class="sub" id="metric-note"></p><div id="metrics"></div></section>
<section><h2>포스터 보기</h2><div class="controls"><label><input type="checkbox" id="follow" checked> 최신 처리 따라가기</label>
<select id="pipe"><option value="easyocr">EasyOCR</option><option value="paddleocr">PaddleOCR</option><option value="hybrid">EasyOCR+Paddle 재인식</option></select>
<select id="variant"><option>rgb_2x</option><option>rgb_1x</option></select><input id="case" size="8" placeholder="v2-001"><button id="go">열기</button></div>
<div class="viewer"><div><div class="stage" id="stage"></div><p class="muted" id="caption" style="font-size:.8rem;margin-top:.4rem"></p></div>
<div><div id="fields"></div><h3>OCR 줄</h3><div class="lines" id="lines"></div></div></div></section>
<section class="logs"><h2>로그</h2><div class="grid2" id="logs"></div></section>
<footer>포스터 원본: ⓒ한국관광공사 TourAPI, 공공누리 제3유형(출처표시·변경금지). 로컬 내부 확인용이며 박스는 화면에만 겹쳐 그립니다.</footer>
</main><script>
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let current=null, lastLatest=null;
function pct(k,n){return n?((k/n*100).toFixed(1)+'%'):'—'}
function eta(s){if(!s)return '';const m=Math.round(s/60);return m>=60?`약 ${Math.floor(m/60)}시간 ${m%60}분 남음`:`약 ${m}분 남음`}
function render(s){
  $('updated').textContent='갱신 '+s.updated_at+(s.system&&s.system.cpu_percent!=null?` · CPU ${s.system.cpu_percent}% · 메모리 ${s.system.mem_used_gb}/${s.system.mem_total_gb} GB`:'')+(s.error?' · 오류: '+s.error:'');
  const L=s.labels, ocrDone=s.ocr.reduce((a,r)=>a+r.done,0), ocrTotal=s.ocr.reduce((a,r)=>a+r.total,0);
  const tiles=[['데이터셋',(s.dataset.splits.dev||0)+(s.dataset.splits.test||0)+'장',`dev ${s.dataset.splits.dev||0} · test ${s.dataset.splits.test||0} · 예비 ${s.dataset.splits.reserve||0}`],
    ['1차 라벨',`${L.pass1}/${L.total}`,'AI 판독 초안'],['검토 완료',`${L.reviewed}/${L.total}`,'2차 검토 반영'],['블라인드',`${L.blind}/${L.blind_total}`,'일치도용 독립 라벨'],
    ['예비 라벨',`${L.reserve_reviewed}/${L.reserve_total}`,`1차 ${L.reserve_pass1} · 규칙 v2 확인용`],
    ['OCR 실행',`${ocrDone}/${ocrTotal}`,'본 200장 + 예비 52장'],['test 지표',s.metrics.frozen?'공개':'잠김',s.metrics.frozen?'규칙 v1 고정 후':'규칙 v1 고정 전']];
  $('tiles').innerHTML=tiles.map(t=>`<div class="tile"><div class="k">${t[0]}</div><div class="v">${t[1]}</div><div class="d">${t[2]}</div></div>`).join('');
  $('labels').innerHTML=`<div class="prow"><span>1차 작성</span><div class="bar"><span style="width:${L.pass1/L.total*100}%"></span></div><span>${L.pass1}/${L.total}</span></div>
   <div class="prow"><span>2차 검토</span><div class="bar"><span style="width:${L.reviewed/L.total*100}%"></span></div><span>${L.reviewed}/${L.total}</span></div>
   <div class="prow"><span>예비 52장 (1차 / 검토)</span><div class="bar"><span style="width:${L.reserve_reviewed/Math.max(1,L.reserve_total)*100}%"></span></div><span>${L.reserve_pass1} / ${L.reserve_reviewed} / ${L.reserve_total}</span></div>
   <div class="prow"><span>블라인드 라벨</span><div class="bar"><span style="width:${L.blind/L.blind_total*100}%"></span></div><span>${L.blind}/${L.blind_total}</span></div>
   <p class="muted" style="font-size:.82rem;margin-top:.6rem">최근 저장: ${L.recent.map(esc).join(', ')||'—'}</p>
   <div class="chips" style="margin-top:.4rem">${L.agents.map(a=>`<span class="chip ${a.status}">${esc(a.label)} ${a.status==='done'?'완료':'진행 중'}</span>`).join('')}</div>`;
  $('ocr').innerHTML=s.ocr.map(r=>`<div class="prow"><span>${r.group==='reserve'?'<span class="muted">예비 · </span>':''}${esc(r.engine)} <span class="muted">${r.variant}</span><span class="state ${r.state}">${{done:'완료',running:'실행 중',queued:'대기',partial:'일부'}[r.state]}</span></span>
    <div class="bar"><span style="width:${r.total?((r.done+r.failed)/r.total*100):0}%"></span></div>
    <span>${r.done}/${r.total}${r.failed?` · 실패 ${r.failed}`:''}${r.mean_s?` · 평균 ${r.mean_s}초`:''}<br><span class="muted">${r.last_case?('마지막 '+esc(r.last_case)+' '+r.last_at):''} ${eta(r.eta_s)}</span></span></div>`).join('');
  const M=s.metrics; $('metric-note').textContent=(M.frozen?'규칙 v1 고정 후: dev와 test를 표시합니다.':'규칙 v1 고정 전이라 dev 60장만 표시합니다.')+(M.frozen_v2?' 규칙 v2 고정 후: 예비 52장도 표시합니다.':' 예비 52장은 규칙 v2 고정 후 표시합니다.')+' 검토 완료 라벨이 있는 포스터만 계산합니다.';
  $('metrics').innerHTML=Object.entries(M.splits).map(([split,t])=>{const rows=Object.values(t);
    if(!rows.length) return `<p class="muted">${split}: 아직 계산할 수 있는 포스터가 없습니다.</p>`;
    return `<h3>${split}</h3><div class="scroll"><table><thead><tr><th>경로</th><th>전처리</th><th class="num">포스터</th><th class="num">핵심 4필드</th><th class="num">축제명</th><th class="num">일정</th><th class="num">장소</th><th class="num">연도</th><th class="num">판독률</th></tr></thead><tbody>`+
      rows.map(r=>`<tr><td>${esc(r.engine)}</td><td>${r.variant}</td><td class="num">${r.n}</td>`+['core_all','title','schedule_pair','venue','year'].map(f=>`<td class="num">${pct(r[f][0],r[f][1])} <span class="muted">(${r[f][0]}/${r[f][1]})</span></td>`).join('')+`<td class="num">${r.read!=null?(r.read*100).toFixed(1)+'%':'—'}</td></tr>`).join('')+'</tbody></table></div>'}).join('');
  $('logs').innerHTML=Object.entries(s.logs).map(([n,l])=>`<div><h3>${esc(n)}</h3><pre>${l.map(esc).join('\\n')}</pre></div>`).join('');
  if($('follow').checked&&s.latest){const k=s.latest.pipeline+'/'+s.latest.variant+'/'+s.latest.case_id; if(k!==lastLatest){lastLatest=k; $('pipe').value=s.latest.pipeline; $('variant').value=s.latest.variant; $('case').value=s.latest.case_id; openCase();}}
}
async function poll(){try{const r=await fetch('/api/status',{cache:'no-store'}); render(await r.json());}catch(e){$('updated').textContent='서버 연결 끊김: '+e;} setTimeout(poll,3000)}
async function openCase(){const id=$('case').value.trim(); if(!id)return; const r=await fetch(`/api/case?id=${encodeURIComponent(id)}&pipeline=${$('pipe').value}&variant=${$('variant').value}`); if(!r.ok){$('caption').textContent='사례를 찾을 수 없습니다.';return}
  const v=await r.json(); current=v;
  const boxes=v.records.map((x,i)=>`<rect data-i="${i}" class="${x.replaced?'rep':''}" x="${x.box[0]}" y="${x.box[1]}" width="${x.box[2]-x.box[0]}" height="${x.box[3]-x.box[1]}"><title>${esc(x.text)} (${x.confidence})</title></rect>`).join('');
  $('stage').innerHTML=`<img src="${v.image}" alt=""><svg viewBox="0 0 ${v.width} ${v.height}" preserveAspectRatio="none">${boxes}</svg>`;
  $('caption').textContent=`${v.case_id} · ${v.split} · ${v.listing_title} · ${v.missing?'아직 OCR 결과 없음':('줄 '+v.records.length+'개 · '+v.inference_seconds+'초')}`+(v.records.some(x=>x.replaced)?' · 초록 박스: Paddle로 다시 읽은 줄':'');
  $('lines').innerHTML=v.records.map((x,i)=>`<div data-i="${i}"><span>${esc(x.text)}${x.baseline&&x.baseline!==x.text?` <span class="muted">← ${esc(x.baseline)}</span>`:''}</span><span class="muted">${x.confidence}</span></div>`).join('')||'<div class="muted">—</div>';
  $('lines').querySelectorAll('div[data-i]').forEach(d=>{d.onmouseenter=()=>{const r=$('stage').querySelector(`rect[data-i="${d.dataset.i}"]`); r&&r.classList.add('hot')}; d.onmouseleave=()=>{const r=$('stage').querySelector(`rect[data-i="${d.dataset.i}"]`); r&&r.classList.remove('hot')}});
  $('fields').innerHTML=`<div class="scroll"><table><thead><tr><th>항목</th><th>추출값</th>${v.gold_shown?'<th>정답</th><th></th>':''}</tr></thead><tbody>`+
    v.fields.map(f=>`<tr><td>${f.label}</td><td>${esc(f.pred??'—')}</td>${v.gold_shown?`<td>${esc(f.gold??'—')}</td><td>${f.correct==null?'':(f.correct?'<span class="ok">✓</span>':'<span class="no">✗</span>')}</td>`:''}</tr>`).join('')+'</tbody></table></div>'+
    (!v.gold_shown?`<p class="muted" style="font-size:.8rem;margin-top:.4rem">${v.split==='test'?'test 정답 대조는 규칙 v1 고정 후 표시합니다.':(v.split==='reserve'?'예비분 정답 대조는 규칙 v2 고정 후 표시합니다.':'검토 완료 정답이 아직 없습니다.')}</p>`:'');
}
$('go').onclick=()=>{$('follow').checked=false; openCase()}; $('pipe').onchange=$('variant').onchange=()=>openCase();
poll();
</script></body></html>"""


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, kind):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                return self.send(200, PAGE.replace("__CSS__", CSS), "text/html; charset=utf-8")
            if url.path == "/review":
                return self.send(200, REVIEW_PAGE.replace("__CSS__", CSS), "text/html; charset=utf-8")
            if url.path == "/api/review/list":
                return self.send(200, json.dumps(human_review.cases(), ensure_ascii=False), "application/json; charset=utf-8")
            if url.path == "/api/status":
                with state.lock:
                    return self.send(200, json.dumps(state.status, ensure_ascii=False), "application/json; charset=utf-8")
            if url.path == "/api/case":
                query = parse_qs(url.query)
                view = state.case_view(query.get("id", [""])[0], query.get("pipeline", ["easyocr"])[0], query.get("variant", ["rgb_2x"])[0])
                return self.send(200, json.dumps(view, ensure_ascii=False), "application/json; charset=utf-8") if view else self.send(404, "{}", "application/json")
            reports = {"/reports/dataset": ROOT / "docs/reports/poster-dataset-v2/report.html",
                       "/reports/experiment": ROOT / "docs/reports/ocr-experiment-v2/report.html",
                       "/reports/preview-dataset": ROOT / ".runtime/poster-ocr/report-preview/dataset.html",
                       "/reports/preview-experiment": ROOT / ".runtime/poster-ocr/report-preview/experiment.html"}
            if url.path in reports:
                path = reports[url.path]
                return self.send(200, path.read_bytes(), "text/html; charset=utf-8") if path.exists() else self.send(404, "보고서가 아직 없습니다.", "text/plain; charset=utf-8")
            match = re.fullmatch(r"/img/((?:v2|r2)-\d{3})", url.path)
            if match and match.group(1) in state.all:
                path = ROOT / state.all[match.group(1)]["image"]
                kind = "image/png" if path.suffix == ".png" else "image/jpeg"
                return self.send(200, path.read_bytes(), kind)
            return self.send(404, "not found", "text/plain; charset=utf-8")
        def do_POST(self):
            url = urlsplit(self.path)
            origin = self.headers.get("Origin")
            if origin and not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+", origin):
                return self.send(403, json.dumps({"error": "다른 출처의 요청"}), "application/json; charset=utf-8")
            if url.path != "/api/review/save":
                return self.send(404, "{}", "application/json")
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 100_000)
                record = human_review.save(json.loads(self.rfile.read(length).decode("utf-8")))
                return self.send(200, json.dumps(record, ensure_ascii=False), "application/json; charset=utf-8")
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                return self.send(400, json.dumps({"error": str(error)}, ensure_ascii=False), "application/json; charset=utf-8")
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--journal", type=Path, nargs="*", help="라벨 워크플로 journal.jsonl(선택, 여러 개)")
    args = parser.parse_args()
    state = State(args.journal)
    state.refresh()
    threading.Thread(target=state.loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    print(f"http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
