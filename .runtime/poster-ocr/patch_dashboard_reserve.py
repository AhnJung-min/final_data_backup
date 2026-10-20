from pathlib import Path

p = Path("app/poster_ocr/dashboard.py")
t = p.read_text(encoding="utf-8")


def rep(old, new):
    global t
    assert t.count(old) == 1, old[:80]
    t = t.replace(old, new)


rep('FREEZE = ROOT / "data/runtime/poster-ocr/FREEZE"', 'FREEZE = ROOT / "data/runtime/poster-ocr/FREEZE"\nFREEZE_V2 = ROOT / "data/runtime/poster-ocr/FREEZE_V2"')
rep('LOG_FILES = ["chain.log", "run-easyocr.log", "run-paddle-2x.log", "run-hybrid-rec.log", "run-paddle-1x.log"]',
    'LOG_FILES = ["chain.log", "run-easyocr.log", "run-paddle-2x.log", "run-hybrid-rec.log", "run-paddle-1x.log", "chain-reserve.log",\n             "run-reserve-easyocr.log", "run-reserve-paddle.log"]')
rep('''    def __init__(self, journal):
        self.journal = Path(journal) if journal else None''', '''    def __init__(self, journals):
        self.journals = [Path(j) for j in (journals or [])]''')
rep('''        self.cases = {c["id"]: c for c in manifest["cases"] if c["split"] in ("dev", "test")}''',
    '''        self.cases = {c["id"]: c for c in manifest["cases"] if c["split"] in ("dev", "test")}
        self.reserve = {c["id"]: c for c in manifest["cases"] if c["split"] == "reserve"}
        self.all = {**self.cases, **self.reserve}''')
rep('''        files = sorted(GOLD.glob("*.poster.json"))
        reviewed, pass1, recent = 0, 0, []
        for path in files:
            version = summary(path, lambda doc: (doc.get("extraction", {}).get("extractor") or {}).get("version") or "")
            if version is None:
                continue
            reviewed += version.endswith("pass2-reviewed")
            pass1 += 1''', '''        files = sorted(GOLD.glob("*.poster.json"))
        counts = {"main": [0, 0], "reserve": [0, 0]}
        for path in files:
            version = summary(path, lambda doc: (doc.get("extraction", {}).get("extractor") or {}).get("version") or "")
            if version is None:
                continue
            group = "reserve" if path.name.split(".")[0] in self.reserve else "main"
            counts[group][0] += 1
            counts[group][1] += version.endswith("pass2-reviewed")
        (pass1, reviewed), (r_pass1, r_reviewed) = counts["main"], counts["reserve"]''')
rep('''        agents = []
        if self.journal and self.journal.exists():
            seen = {}
            for line in self.journal.read_text(encoding="utf-8").splitlines():''', '''        seen = {}
        for journal in [j for j in self.journals if j.exists()]:
            for line in journal.read_text(encoding="utf-8").splitlines():''')
rep('''                elif event.get("type") == "result" and event.get("key") in seen:
                    seen[event["key"]]["status"] = "done"
            agents = list(seen.values())
        return {"pass1": pass1, "reviewed": reviewed, "blind": len(list(BLIND.glob("*.poster.json"))), "total": len(self.cases),
                "blind_total": 30, "recent": recent, "agents": agents}''', '''                elif event.get("type") == "result" and event.get("key") in seen:
                    seen[event["key"]]["status"] = "done"
        agents = list(seen.values())
        return {"pass1": pass1, "reviewed": reviewed, "blind": len(list(BLIND.glob("*.poster.json"))), "total": len(self.cases),
                "blind_total": 30, "recent": recent, "agents": agents, "reserve_pass1": r_pass1, "reserve_reviewed": r_reviewed,
                "reserve_total": len(self.reserve)}''')
rep('''        for engine, name in PIPELINES:
            for variant in VARIANTS:
                files = list((RUNS / engine).glob(f"*/{variant}.json"))''', '''        for group, ids in (("main", self.cases), ("reserve", self.reserve)):
          for engine, name in PIPELINES:
            for variant in VARIANTS:
                files = [p for p in (RUNS / engine).glob(f"*/{variant}.json") if p.parent.name in ids]''')
rep('''                total = len(self.cases)
                stamps.sort()''', '''                total = len(ids)
                stamps.sort()''')
rep('''                    running = (LOGS / "run-hybrid-rec.log").exists() and done == 0''',
    '''                    running = (LOGS / ("run-hybrid-rec.log" if group == "main" else "run-reserve-hybrid-rec.log")).exists() and done == 0''')
rep('''                rows.append({"key": f"{engine}/{variant}", "engine": name, "variant": variant, "done": done,''',
    '''                rows.append({"key": f"{group}:{engine}/{variant}", "group": group, "engine": name, "variant": variant, "done": done,''')
rep('''    def metrics(self):
        frozen = FREEZE.exists()
        out = {"frozen": frozen, "splits": {}}
        for split in (["dev", "test"] if frozen else ["dev"]):''', '''    def metrics(self):
        frozen, frozen_v2 = FREEZE.exists(), FREEZE_V2.exists()
        out = {"frozen": frozen, "frozen_v2": frozen_v2, "splits": {}}
        for split in ["dev"] + (["test"] if frozen else []) + (["reserve"] if frozen_v2 else []):''')
rep('''                    rows = [r for c in self.cases.values() if c["split"] == split for r in [self.row(c, engine, variant)] if r]''',
    '''                    rows = [r for c in self.all.values() if c["split"] == split for r in [self.row(c, engine, variant)] if r]''')
rep('''        case = self.cases.get(case_id)''', '''        case = self.all.get(case_id)''')
rep('''        show_gold = reviewed and (case["split"] == "dev" or FREEZE.exists())''',
    '''        show_gold = reviewed and (case["split"] == "dev" or (case["split"] == "test" and FREEZE.exists())
                                  or (case["split"] == "reserve" and FREEZE_V2.exists()))''')
rep('''            match = re.fullmatch(r"/img/(v2-\\d{3})", url.path)
            if match and match.group(1) in state.cases:
                path = ROOT / state.cases[match.group(1)]["image"]''', '''            match = re.fullmatch(r"/img/((?:v2|r2)-\\d{3})", url.path)
            if match and match.group(1) in state.all:
                path = ROOT / state.all[match.group(1)]["image"]''')
rep('''    parser.add_argument("--journal", type=Path, help="라벨 워크플로 journal.jsonl(선택)")''',
    '''    parser.add_argument("--journal", type=Path, nargs="*", help="라벨 워크플로 journal.jsonl(선택, 여러 개)")''')
rep('''    ['OCR 실행',`${ocrDone}/${ocrTotal}`,'3경로 × 전처리 2종'],['test 지표',s.metrics.frozen?'공개':'잠김',s.metrics.frozen?'규칙 고정 후':'규칙 고정 전']];''',
    '''    ['예비 라벨',`${L.reserve_reviewed}/${L.reserve_total}`,`1차 ${L.reserve_pass1} · 규칙 v2 확인용`],
    ['OCR 실행',`${ocrDone}/${ocrTotal}`,'본 200장 + 예비 52장'],['test 지표',s.metrics.frozen?'공개':'잠김',s.metrics.frozen?'규칙 v1 고정 후':'규칙 v1 고정 전']];''')
rep('''   <div class="prow"><span>블라인드 라벨</span>''', '''   <div class="prow"><span>예비 52장 (1차 / 검토)</span><div class="bar"><span style="width:${L.reserve_reviewed/Math.max(1,L.reserve_total)*100}%"></span></div><span>${L.reserve_pass1} / ${L.reserve_reviewed} / ${L.reserve_total}</span></div>
   <div class="prow"><span>블라인드 라벨</span>''')
rep('''  $('ocr').innerHTML=s.ocr.map(r=>`<div class="prow"><span>${esc(r.engine)} <span class="muted">${r.variant}</span>''',
    '''  $('ocr').innerHTML=s.ocr.map(r=>`<div class="prow"><span>${r.group==='reserve'?'<span class="muted">예비 · </span>':''}${esc(r.engine)} <span class="muted">${r.variant}</span>''')
rep('''$('metric-note').textContent=M.frozen?'규칙 고정 후: dev와 test를 모두 표시합니다.':'규칙 고정 전이라 dev 60장만 표시합니다. 검토 완료 라벨이 있는 포스터만 계산합니다.';''',
    '''$('metric-note').textContent=(M.frozen?'규칙 v1 고정 후: dev와 test를 표시합니다.':'규칙 v1 고정 전이라 dev 60장만 표시합니다.')+(M.frozen_v2?' 규칙 v2 고정 후: 예비 52장도 표시합니다.':' 예비 52장은 규칙 v2 고정 후 표시합니다.')+' 검토 완료 라벨이 있는 포스터만 계산합니다.';''')
rep('''(!v.gold_shown?`<p class="muted" style="font-size:.8rem;margin-top:.4rem">${v.split==='test'&&!v.frozen?'test 정답 대조는 규칙 고정 후 표시합니다.':'검토 완료 정답이 아직 없습니다.'}</p>`:'');''',
    '''(!v.gold_shown?`<p class="muted" style="font-size:.8rem;margin-top:.4rem">${v.split==='test'?'test 정답 대조는 규칙 v1 고정 후 표시합니다.':(v.split==='reserve'?'예비분 정답 대조는 규칙 v2 고정 후 표시합니다.':'검토 완료 정답이 아직 없습니다.')}</p>`:'');''')
p.write_text(t, encoding="utf-8")
print("ok")
