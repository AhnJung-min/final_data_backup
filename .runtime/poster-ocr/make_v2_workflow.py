import sys

src, dst = sys.argv[1], sys.argv[2]
t = open(src, encoding="utf-8").read()


def rep(a, b):
    global t
    assert a in t, a[:70]
    t = t.replace(a, b)


rep("name: 'rules-v1-dev-tuning'", "name: 'rules-v2-tuning'")
rep("description: 'Field specialists improve the poster rule extractor on the dev split only, then merge and adversarially review before freezing v1'",
    "description: 'Rules v2: field specialists, engine fusion and schema-convention review on dev+test (never reserve), merge, adversarial review'")
rep("const PROP = `${REPO}\\\\.runtime\\\\poster-ocr\\\\proposals`", "const PROP = `${REPO}\\\\.runtime\\\\poster-ocr\\\\proposals-v2`")
rep('''STRICT DATA RULE: you may look ONLY at the dev split (60 cases; list them from data/benchmarks/posters-v2.json where split=="dev"). Never open gold, OCR output or predictions for test (split "test") or reserve (ids r2-*) cases, and never run evaluate.py on test/reserve. The only evaluation tool you use is:
  ${PY} -m app.poster_ocr.rule_dev --extract <path to candidate extract.py> --out <scratch dir>
which evaluates dev only for easyocr/rgb_2x, easyocr/rgb_1x, paddleocr/rgb_2x and hybrid/rgb_2x (OCR output at data/runtime/poster-ocr/runs/<engine>/<case>/<variant>.json; per-case rows are in <out>/dev-summary.json).''',
    '''CONTEXT: rules v1 (app/poster_ocr/extract_v1.py, same logic as app/poster_ocr/extract.py) were tuned on dev (60) and then evaluated ONCE on test (140). They lost a lot on test (PaddleOCR rgb_2x: title 38%->25%, venue 66%->45%, core 33%->5%) while OCR read rates were the same, i.e. the v1 rules overfit dev. Rules v2 must GENERALISE.
DATA RULE: you may now use dev (60) AND test (140) cases (split "dev"/"test" in data/benchmarks/posters-v2.json). NEVER open gold, OCR output or predictions of reserve cases (ids r2-*): they are the final confirmation set for v2. Evaluation tool:
  ${PY} -m app.poster_ocr.rule_dev --extract <candidate extract.py> --splits dev test --out <scratch dir>
which prints dev and test separately for easyocr/rgb_2x, easyocr/rgb_1x, paddleocr/rgb_2x and hybrid/rgb_2x (OCR output at data/runtime/poster-ocr/runs/<engine>/<case>/<variant>.json; per-case rows in <out>/dev-summary.json, keyed by pipeline, each row has its split).
ACCEPTANCE RULE (anti-overfitting): keep a change only if it improves dev AND test separately on the PaddleOCR pipeline (the likely product default) and does not regress either split on the other pipelines by more than it helps. Prefer one general rule that fixes many cases over case-by-case patches. Report per-split numbers.''')
rep("1) Copy app/poster_ocr/extract.py to", "1) Copy app/poster_ocr/extract_v1.py to")
rep("2) Run rule_dev on the ORIGINAL (--extract app/poster_ocr/extract.py --out ${PROP}\\\\${g.key}\\\\before) and inspect dev errors for your fields: for each dev case compare gold vs prediction and check whether the gold text appears in the OCR lines (if the OCR never read it, no rule can fix it — note it and move on).",
    "2) Run rule_dev on the ORIGINAL (--extract app/poster_ocr/extract_v1.py --splits dev test --out ${PROP}\\\\${g.key}\\\\before) and inspect errors for your fields on dev+test: compare gold vs prediction and check whether the gold text appears in the OCR lines (if the OCR never read it, no rule can fix it). Focus on rule errors (OCR read the gold text but the rule chose wrongly) and group them into general failure patterns.")
rep("3) Implement general rule improvements for your fields in the copy. Re-run rule_dev on the copy (--out ${PROP}\\\\${g.key}\\\\after) until it helps. Check other fields did not regress.",
    "3) Implement general rule improvements for your fields in the copy. Re-run rule_dev on the copy (--splits dev test --out ${PROP}\\\\${g.key}\\\\after). Apply the ACCEPTANCE RULE. Check other fields did not regress.")
rep("4) Write a unified diff to ${PROP}\\\\${g.key}\\\\change.diff (e.g. git diff --no-index app/poster_ocr/extract.py ${PROP}\\\\${g.key}\\\\extract.py > ...).",
    "4) Write a unified diff to ${PROP}\\\\${g.key}\\\\change.diff (e.g. git diff --no-index app/poster_ocr/extract_v1.py ${PROP}\\\\${g.key}\\\\extract.py > ...).")
rep("  { key: 'title', focus: 'title (title.raw/official selection among large lines, multi-line titles, slogans vs title, English variants), edition, year' },",
    "  { key: 'title', focus: 'title.raw/official selection. Known v1 failure patterns on test: (a) a smaller region/place prefix printed above or left of the main title is not joined (gold 포천 한탄강 가든페스타, pred 한탄강 가든페스타; about 12 cases); (b) a slogan line below is appended although the title already ends with a festival suffix; (c) a big slogan or an English/brand variant wins over the Korean title; (d) titles ending in a non-suffix noun. Also edition and year' },")
rep("  { key: 'venue', focus: 'venue raw/items (label-based and keyword-based selection, multi-line venues, venue on the same line as dates), address, admin_area' },",
    "  { key: 'venue', focus: 'venue raw/items, address, admin_area. Known v1 failure patterns on test: (a) trailing junk appended (문의 phone, address, times, unrelated next lines); (b) a region prefix at the start is missing (gold 서천군 홍원항 일원, pred 일원); (c) a second venue item on the next line is not joined; (d) English-only venues (PARK/Hall/Center/Square) are not found; (e) wrong line chosen when several venue-word lines exist' },")
extra = '''const EXTRA = [
  { key: 'fusion', prompt: `${COMMON}

YOUR TASK: evaluate ENGINE/VARIANT FUSION at field level with the v1 rules (no rule edits). Predictions exist under data/runtime/poster-ocr/pred/<pipeline>/<case>/<variant>.poster.json for easyocr, paddleocr and hybrid (dev+test; paddleocr rgb_1x may be partial because it is still running). Tool: ${PY} -m app.poster_ocr.fusion --primary paddleocr --secondary easyocr --name fusion_pe [--secondary-variant rgb_1x] writes data/runtime/poster-ocr/pred/<name>/...; then evaluate with ${PY} -m app.poster_ocr.evaluate --splits dev test --pipeline fusion_pe=paddleocr --out ${PROP}\\\\fusion\\\\<name>.json (it re-evaluates the base pipelines too; never pass reserve). Try at least paddleocr+easyocr, paddleocr+hybrid, paddleocr rgb_2x + paddleocr rgb_1x (cases where 1x exists), and a per-field engine choice (e.g. edition or organisations from another engine). Report per-split core_all, title, title_any, schedule_pair, venue, year, edition, organisations F1, contacts F1, and the extra cost (both engines must run). Recommend whether v2 should be a fused pipeline and which. Do not modify app/ files except app/poster_ocr/fusion.py if a bug blocks you (describe the fix).`,
    schema: { type: 'object', properties: { recommendation: { type: 'string' }, results: { type: 'string' }, files: { type: 'array', items: { type: 'string' } } }, required: ['recommendation', 'results'] } },
  { key: 'schema', prompt: `${COMMON}

YOUR TASK: schema and labelling-convention review (no extractor edits). Read docs/festival-poster-standard.md, data/festivals/poster.schema.json and the gold files of dev+test. Find conventions that are ambiguous or inconsistently applied across the 200 gold labels and that distort the metrics: admin_area (labellers disagreed whether a district inside a compound word like 예산황새축제 counts), title.official (does a region/place prefix belong to it; English-only posters), venue.raw (prefix/suffix scope), organisations without role labels, schedule for multi-period events. For each: count affected gold files (dev+test only), propose a precise rule (standard 1.1), say whether it can be applied deterministically from other gold fields, and list the case ids that would change. Do NOT edit gold files. Write the proposal to ${PROP}\\\\schema\\\\proposal.md.`,
    schema: { type: 'object', properties: { proposals: { type: 'array', items: { type: 'object', properties: { field: { type: 'string' }, problem: { type: 'string' }, rule: { type: 'string' }, deterministic: { type: 'boolean' }, affected_cases: { type: 'array', items: { type: 'string' } } }, required: ['field', 'problem', 'rule'] } }, summary: { type: 'string' } }, required: ['proposals', 'summary'] } },
]

const GROUPS = ['''
rep("const GROUPS = [", extra)
rep("const proposals = await parallel(GROUPS.map(g => () => agent(",
    "const extraRuns = parallel(EXTRA.map(e => () => agent(e.prompt, { label: `analyse:${e.key}`, phase: 'Propose', schema: e.schema }).then(r => ({ key: e.key, ...r }))))\nconst proposals = await parallel(GROUPS.map(g => () => agent(")
rep("TASK: build ONE merged candidate at ${PROP}\\\\merged\\\\extract.py starting from the original app/poster_ocr/extract.py",
    "TASK: build ONE merged candidate at ${PROP}\\\\merged\\\\extract.py starting from the original app/poster_ocr/extract_v1.py")
rep("Run rule_dev on the merged copy (--out ${PROP}\\\\merged\\\\after) and on the original (--out ${PROP}\\\\merged\\\\before)",
    "Run rule_dev on the merged copy (--splits dev test --out ${PROP}\\\\merged\\\\after) and on the original (--splits dev test --out ${PROP}\\\\merged\\\\before); apply the ACCEPTANCE RULE per change (ablation)")
rep("Write ${PROP}\\\\merged\\\\change.diff (vs the original).",
    "Write ${PROP}\\\\merged\\\\change.diff (vs app/poster_ocr/extract_v1.py).")
rep("for OVERFITTING to the 60 dev posters", "for OVERFITTING to the 200 dev+test posters")
rep("Open dev OCR outputs if needed to check how often each new rule fires (dev only).",
    "Open dev/test OCR outputs if needed to check how often each new rule fires (never reserve r2-*). Also check that each kept change helps dev and test separately.")
rep("return { proposals: valid, merged, reviews }", "const extras = await extraRuns\nreturn { proposals: valid, merged, reviews, extras }")
open(dst, "w", encoding="utf-8").write(t)
print("ok")
