"""사람 검토 화면(HTML). dashboard 서버의 /review 에서 제공한다."""

REVIEW_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>정답 사람 검토</title><style>__CSS__
main{max-width:84rem;gap:1.2rem}.top{display:flex;flex-wrap:wrap;gap:.8rem 1.2rem;align-items:center}
.top input,.top select{font:inherit;font-size:.88rem;padding:.3rem .5rem;border:1px solid var(--rule);border-radius:6px;background:var(--sheet);color:var(--ink)}
.prog{font-size:.86rem;color:var(--ink-2)}.wrapv{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,30rem);gap:1.5rem;align-items:start}
.stage{background:var(--paper-2);border-radius:8px;overflow:auto;max-height:82vh;position:sticky;top:.5rem}.stage img{display:block;width:100%;height:auto;cursor:zoom-in}
.stage.zoom img{width:200%;cursor:zoom-out}.form{background:var(--sheet);border:1px solid var(--rule);border-radius:8px;padding:1rem}
.row{display:grid;grid-template-columns:5.6rem 6.4rem minmax(0,1fr);gap:.5rem;align-items:center;padding:.35rem 0;border-bottom:1px solid var(--rule)}
.row:last-of-type{border-bottom:0}.row label{font-size:.88rem;font-weight:600}.row select,.row input{font:inherit;font-size:.9rem;padding:.3rem .45rem;border:1px solid var(--rule);border-radius:6px;background:var(--paper);color:var(--ink);min-width:0;width:100%}
.row.changed{background:var(--accent-wash);border-radius:6px}.ai{grid-column:3;font-size:.75rem;color:var(--muted)}
.btns{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.9rem}.btns button{padding:.45rem .9rem}.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
textarea{font:inherit;font-size:.86rem;width:100%;min-height:3.2rem;border:1px solid var(--rule);border-radius:6px;background:var(--paper);color:var(--ink);padding:.4rem;box-sizing:border-box}
.msg{font-size:.85rem;margin-top:.5rem;min-height:1.2em}.done{color:#1a7f37}.err{color:#c4352f}.kbd{font-family:var(--font-mono);font-size:.75rem;background:var(--paper-2);padding:.05rem .3rem;border-radius:4px}
@media (max-width:900px){.wrapv{grid-template-columns:1fr}.stage{position:static;max-height:none}}
</style></head><body><main>
<header class="intro"><p class="eyebrow"><span class="reg"></span>gold-v2 · 사람 검토</p><h1>정답 사람 검토</h1>
<p class="lead">포스터를 보고 AI 정답의 핵심 필드가 맞는지 확인합니다. 맞으면 <span class="kbd">Enter</span>(모두 맞음·다음), 틀린 칸만 고친 뒤 <span class="kbd">Ctrl+Enter</span>(저장·다음). 인쇄된 그대로 적고, 포스터에 없으면 '없음', 글자는 있는데 읽을 수 없으면 '판독 불가'를 고릅니다.</p></header>
<section class="top"><label>검토자 <input id="reviewer" size="10" placeholder="이름"></label>
<label>분할 <select id="split"><option value="all">전체(test→예비→dev)</option><option value="test">test 140</option><option value="reserve">예비 52</option><option value="dev">dev 60</option></select></label>
<label><input type="checkbox" id="pending" checked> 미검토만</label><label>번호 <input id="jump" size="7" placeholder="v2-001"></label>
<span class="prog" id="prog"></span><a href="/" style="font-size:.86rem">대시보드</a></section>
<section class="wrapv"><div class="stage" id="stage"><img id="img" alt=""></div>
<div class="form"><h2 id="cid" style="margin-bottom:.2rem"></h2><p class="sub" id="meta" style="margin-bottom:.6rem"></p><div id="rows"></div>
<label style="font-size:.85rem;display:block;margin-top:.6rem">메모<textarea id="note" placeholder="애매한 점, 정답 규칙에 대한 의견"></textarea></label>
<div class="btns"><button class="primary" id="ok">모두 맞음 · 다음 (Enter)</button><button id="save">수정 저장 · 다음 (Ctrl+Enter)</button>
<button id="prev">← 이전</button><button id="next">다음 →</button></div><div class="msg" id="msg"></div>
<p class="sub" style="margin-top:.8rem;font-size:.8rem">날짜는 <code>2026-10-03</code>(연도 인쇄)이나 <code>10-03</code>(연도 없음). 하루 행사는 종료일 '없음'. 이미지를 누르면 2배로 커집니다.</p></div></section>
<footer>포스터 원본: ⓒ한국관광공사 TourAPI, 공공누리 제3유형. 이 화면은 이 컴퓨터에서만 열리며 검토 결과는 <code>data/benchmarks/human-review/</code>에 저장됩니다.</footer>
</main><script>
const $=id=>document.getElementById(id);
const FIELDS=[["title","축제명","text"],["year","연도","text"],["edition","회차","text"],["start","시작일","date"],["start_weekday","시작 요일","wd"],["end","종료일","date"],["end_weekday","종료 요일","wd"],["venue","장소","text"]];
const WD=[["mon","월"],["tue","화"],["wed","수"],["thu","목"],["fri","금"],["sat","토"],["sun","일"]];
let all=[], list=[], idx=0;
try{$('reviewer').value=localStorage.getItem('reviewer')||''}catch(e){}
$('reviewer').onchange=()=>{try{localStorage.setItem('reviewer',$('reviewer').value)}catch(e){}};
function st(s){return s==='present'||s==='absent'||s==='illegible'?s:'absent'}
function filtered(){return all.filter(c=>($('split').value==='all'||c.split===$('split').value)&&(!$('pending').checked||!c.review))}
function progress(){const by={};all.forEach(c=>{by[c.split]=by[c.split]||[0,0];by[c.split][0]++;if(c.review)by[c.split][1]++});
 $('prog').textContent=['test','reserve','dev'].filter(s=>by[s]).map(s=>`${s==='reserve'?'예비':s} ${by[s][1]}/${by[s][0]}`).join(' · ')}
function control(name,kind,f){const sel=`<select data-s="${name}"><option value="present">있음</option><option value="absent">없음</option><option value="illegible">판독 불가</option></select>`;
 let input; if(kind==='wd'){input=`<select data-v="${name}"><option value="">—</option>${WD.map(w=>`<option value="${w[0]}">${w[1]}</option>`).join('')}</select>`}
 else input=`<input data-v="${name}" ${kind==='date'?'placeholder="2026-10-03"':''}>`;
 return {sel,input}}
function render(){list=filtered(); progress(); if(!list.length){$('cid').textContent='검토할 포스터가 없습니다';$('rows').innerHTML='';$('img').removeAttribute('src');return}
 idx=Math.max(0,Math.min(idx,list.length-1)); const c=list[idx], cur=c.review?c.review.fields:null;
 $('cid').textContent=`${c.id} · ${c.split==='reserve'?'예비':c.split}`; $('meta').textContent=`${idx+1}/${list.length} · ${c.review?('검토됨 '+(c.review.reviewer||'')+' '+c.review.reviewed_at):'미검토'}`;
 $('img').src=c.image; $('note').value=c.review&&c.review.note||'';
 $('rows').innerHTML=FIELDS.map(([n,l,k])=>{const {sel,input}=control(n,k);return `<div class="row" id="row-${n}"><label>${l}</label>${sel}${input}<div class="ai" id="ai-${n}"></div></div>`}).join('');
 FIELDS.forEach(([n])=>{const a=c.ai[n], v=cur?cur[n]:{status:st(a.status),value:a.value};
   document.querySelector(`[data-s="${n}"]`).value=v.status; document.querySelector(`[data-v="${n}"]`).value=v.value||'';
   const shown=n.endsWith('weekday')?(Object.fromEntries(WD)[a.value]||a.value):a.value; $('ai-'+n).textContent=`AI: ${a.status==='present'?(shown||'—'):({absent:'없음',illegible:'판독 불가',not_checked:'없음'}[a.status])}`;});
 document.querySelectorAll('[data-s],[data-v]').forEach(e=>e.oninput=e.onchange=mark); mark();}
function values(){const out={};FIELDS.forEach(([n])=>{out[n]={status:document.querySelector(`[data-s="${n}"]`).value,value:document.querySelector(`[data-v="${n}"]`).value.trim()}});return out}
function mark(){const c=list[idx]; if(!c)return; const v=values(); FIELDS.forEach(([n])=>{const a=c.ai[n], same=st(a.status)===v[n].status&&(v[n].status!=='present'||String(a.value).trim()===v[n].value);
 $('row-'+n).classList.toggle('changed',!same); const inp=document.querySelector(`[data-v="${n}"]`); inp.disabled=v[n].status!=='present'})}
async function submit(useAI){const c=list[idx]; if(!c)return; if(!$('reviewer').value.trim()){$('msg').className='msg err';$('msg').textContent='검토자 이름을 먼저 입력하세요.';$('reviewer').focus();return}
 const fields=useAI?Object.fromEntries(FIELDS.map(([n])=>[n,{status:st(c.ai[n].status),value:c.ai[n].status==='present'?c.ai[n].value:''}])):values();
 const r=await fetch('/api/review/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:c.id,reviewer:$('reviewer').value,fields,note:$('note').value})});
 const body=await r.json(); if(!r.ok){$('msg').className='msg err';$('msg').textContent='저장 실패: '+(body.error||r.status);return}
 c.review=body; $('msg').className='msg done'; $('msg').textContent=`${c.id} 저장${body.changed.length?' (수정: '+body.changed.join(', ')+')':' (변경 없음)'}`;
 if($('pending').checked){render()}else{idx++;render()} window.scrollTo(0,0)}
$('ok').onclick=()=>submit(true); $('save').onclick=()=>submit(false); $('prev').onclick=()=>{idx--;render()}; $('next').onclick=()=>{idx++;render()};
$('split').onchange=$('pending').onchange=()=>{idx=0;render()};
$('jump').onkeydown=e=>{if(e.key==='Enter'){const id=$('jump').value.trim(); $('pending').checked=false; $('split').value='all'; list=filtered(); idx=Math.max(0,list.findIndex(c=>c.id===id)); render()}};
$('img').onclick=()=>$('stage').classList.toggle('zoom');
document.addEventListener('keydown',e=>{const t=e.target.tagName; if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();submit(false);return}
 if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return; if(e.key==='Enter'){e.preventDefault();submit(true)} else if(e.key==='ArrowRight'){idx++;render()} else if(e.key==='ArrowLeft'){idx--;render()}});
fetch('/api/review/list').then(r=>r.json()).then(d=>{all=d;render()});
</script></body></html>"""
