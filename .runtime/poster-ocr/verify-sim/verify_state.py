import json, os, glob
from pathlib import Path
R = Path('.')
V = R/'.runtime/poster-ocr/verify-sim'
S = json.load(open(V/'state.json', encoding='utf-8'))
sets = json.load(open(R/'data/synthetic/poster-check-v1/sets.json', encoding='utf-8'))['sets']
man = json.load(open(R/'data/synthetic/poster-check-v1/manifest.json', encoding='utf-8'))
runs = R/'data/runtime/poster-ocr/sim/runs/paddleocr'
checks = {Path(p).stem: json.load(open(p, encoding='utf-8')) for p in glob.glob(str(R/'data/runtime/poster-ocr/sim/checks/*.json'))}
print('checks', len(checks), 'sets', len(sets), 'manifest cases', len(man['cases']))
print('run files', sorted(os.listdir(runs))[:3], [f for f in os.listdir(runs) if f.endswith('.json')])
# verdict counts
from collections import Counter
vc = Counter(c['verdict'] for c in checks.values())
print('verdict counts from checks', vc)
items = {i['id']: i for i in S['items']}
print('verdict counts from state', Counter(i['verdict'] for i in S['items']))
print('stages', Counter(i['stage'] for i in S['items']))
# per item consistency
bad = []
truthmap = {s['id']: s for s in sets}
tp=fn=fp=tn=0; fh=0
for sid, c in sorted(checks.items()):
    it = items[sid]; s = truthmap[sid]
    if it['counts'] != c['counts']: bad.append((sid, 'counts', it['counts'], c['counts']))
    if it['verdict'] != c['verdict']: bad.append((sid,'verdict'))
    if it['predicted_mismatch'] != c['predicted_mismatch']: bad.append((sid,'pred'))
    cnt = Counter(r['status'] for r in c['results'])
    if dict(pass_=cnt['pass']) and (cnt['pass'],cnt['fail'],cnt['review']) != (c['counts']['pass'],c['counts']['fail'],c['counts']['review']): bad.append((sid,'counts vs results'))
    if c['predicted_mismatch'] != (c['counts']['fail']>0): bad.append((sid,'pred vs fail'))
    info_review = sum(r['status']=='review' and r['kind']=='info' for r in c['results'])
    exp_v = 'fail' if c['counts']['fail'] else ('review' if info_review else 'pass')
    if exp_v != c['verdict']: bad.append((sid,'verdict logic', exp_v, c['verdict']))
    if it['truth'] != s['label']: bad.append((sid,'truth'))
    truth = s['label']=='mismatch'; pred = c['predicted_mismatch']
    if truth and pred: tp+=1
    elif truth: fn+=1
    elif pred: fp+=1
    else: tn+=1
    hit = truth and s['mismatch']['field'] in [r['key'] for r in c['results'] if r['status']=='fail']
    fh += hit
    if bool(hit) != it['field_hit']: bad.append((sid,'field_hit'))
    if c.get('rules') != 'sim_check r2': bad.append((sid,'rules', c.get('rules')))
print('bad', bad)
print('recomputed cm', dict(tp=tp,fn=fn,fp=fp,tn=tn), 'field_hit', fh)
print('state cm', S['report']['confusion'], 'rows field_hit', sum(x['field_hit'] for x in S['report']['rows']))
# overview tabs
wrong = [i['id'] for i in S['items'] if i['stage']=='done' and (i['truth']=='mismatch') != i['predicted_mismatch']]
print('wrong', wrong)
print('progress', sum(i['stage']!='done' for i in S['items']))
# mismatch sets details
for sid in wrong:
    c = checks[sid]; s = truthmap[sid]
    print(sid, s['label'], s['mismatch'] and s['mismatch']['field'], c['counts'], [ (r['key'], r['status']) for r in c['results'] if r['status']!='pass'])
# mismatched (other field) - tp but not hit
for sid,it in sorted(items.items()):
    if it['truth']=='mismatch' and it['predicted_mismatch'] and not it['field_hit']:
        print('tp other-field', sid)
# mtime check: check newer than runs?
for sid in sorted(checks):
    ct = os.path.getmtime(R/f'data/runtime/poster-ocr/sim/checks/{sid}.json')
    dt = os.path.getmtime(runs/f'{sid}-doc'/'rgb_1x.json'); pt = os.path.getmtime(runs/f'{sid}-poster'/'rgb_1x.json')
    st = os.path.getmtime(R/'app/poster_ocr/sim_check.py')
    if ct < max(dt,pt,st): print('stale', sid)
# finished_at
for sid,it in items.items():
    pt = os.path.getmtime(runs/f'{sid}-poster'/'rgb_1x.json')
    if abs(it['finished_at']-pt)>0.01: print('finished_at mismatch', sid)
# log
print('log len', len(S['log']))
for l in S['log']: print(l['set'], l['text'], l['t'])
