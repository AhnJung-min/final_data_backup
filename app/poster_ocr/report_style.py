"""보고서 HTML 공통 틀: 팀 스키마 보고서(포스터 표준 통합 데이터 스키마.html)와 같은 서체·색 토큰·레이아웃."""
from __future__ import annotations

import html
import json

CSS = """
:root{--paper:#f6f7f8;--sheet:#ffffff;--paper-2:#eceef1;--ink:#1f2328;--ink-2:#5b616a;--muted:#686e77;--rule:#d9dde2;
--grid:#e3e6ea;--axis:#b9bfc7;--accent:#1d5fb3;--accent-wash:rgba(29,95,179,.07);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
--seq:#2a78d6;--warn:#b26a00;--shadow:rgba(16,24,40,.12);
--font-display:"Black Han Sans","IBM Plex Sans KR","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
--font-body:"IBM Plex Sans KR","Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif;
--font-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Consolas,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#14171b;--sheet:#1b1f24;--paper-2:#1d2126;--ink:#e9ecef;
--ink-2:#a9b0b9;--muted:#8b929c;--rule:#2e343b;--grid:#262b31;--axis:#4a515a;--accent:#7fb0ee;--accent-wash:rgba(127,176,238,.09);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--seq:#3987e5;--warn:#e0a030;--shadow:rgba(0,0,0,.45);color-scheme:dark}}
:root[data-theme="dark"]{--paper:#14171b;--sheet:#1b1f24;--paper-2:#1d2126;--ink:#e9ecef;--ink-2:#a9b0b9;--muted:#8b929c;--rule:#2e343b;
--grid:#262b31;--axis:#4a515a;--accent:#7fb0ee;--accent-wash:rgba(127,176,238,.09);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--seq:#3987e5;
--warn:#e0a030;--shadow:rgba(0,0,0,.45);color-scheme:dark}
[hidden]{display:none!important}
body{margin:0;background:var(--paper);color:var(--ink);font:400 1rem/1.7 var(--font-body);padding:3rem 20px 4rem;word-break:keep-all;overflow-wrap:break-word}
main{display:flex;flex-direction:column;gap:3.25rem;max-width:64rem;margin-inline:auto}
.wrap{width:100%;max-width:46rem}.wide{width:100%}.wide>h2,.wide>.sub{max-width:46rem}
h1,h2,h3{text-wrap:balance;margin:0}h1{font-family:var(--font-display);font-weight:400;font-size:clamp(2rem,5.5vw,3.1rem);line-height:1.15}
h2{font-size:1.35rem;font-weight:600;line-height:1.35;margin-bottom:.6rem}h3{font-size:1.02rem;font-weight:600;margin:1.4rem 0 .4rem}
p{margin:0}section>p+p,section>p+ul,section>ul+p,section>p+.scroll,section>.scroll+p{margin-top:.9rem}
.sub{color:var(--ink-2);font-size:.95rem;margin-bottom:1.2rem}
code{font-family:var(--font-mono);font-size:.86em;background:var(--paper-2);border-radius:4px;padding:.08em .35em;overflow-wrap:anywhere;word-break:normal}
pre{margin:0;background:var(--paper-2);border-radius:6px;padding:1rem 1.1rem;overflow-x:auto;font-family:var(--font-mono);font-size:.8rem;line-height:1.6}
pre code{background:none;padding:0;font-size:inherit}a{color:var(--accent)}:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.intro{display:flex;flex-direction:column;gap:1rem}.eyebrow{display:flex;align-items:center;gap:.55rem;font-family:var(--font-mono);font-size:.8rem;letter-spacing:.05em;color:var(--accent)}
.reg{width:15px;height:15px;flex:none;border:1.5px solid currentColor;border-radius:50%;background:linear-gradient(currentColor,currentColor) center/1.5px 100% no-repeat,linear-gradient(currentColor,currentColor) center/100% 1.5px no-repeat}
.lead{font-size:1.06rem;max-width:42rem}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr));gap:.75rem 1.5rem;margin:.5rem 0 0;padding-top:1rem;border-top:1px solid var(--rule)}
.meta div{min-width:0}.meta dt{font-size:.78rem;color:var(--muted);letter-spacing:.03em}.meta dd{margin:.1rem 0 0;font-size:.92rem}
.points,.plain{margin:0;padding-left:1.15rem;display:flex;flex-direction:column;gap:.55rem}.points li::marker,.plain li::marker{color:var(--accent)}
.points strong,.plain strong{font-weight:600}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:6px;background:var(--sheet)}
table{width:100%;border-collapse:collapse;font-size:.86rem;line-height:1.5}
th,td{text-align:left;vertical-align:top;padding:.5rem .7rem;border-bottom:1px solid var(--rule)}
thead th{background:var(--paper-2);font-weight:600;font-size:.8rem;color:var(--ink-2);white-space:nowrap}tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.scroll code{white-space:nowrap;overflow-wrap:normal}
td.ci{color:var(--muted);font-size:.8rem;white-space:nowrap;font-variant-numeric:tabular-nums}
.chart{position:relative;margin:0}.chart-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.75rem 1.5rem;margin-bottom:1rem}
.legend{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;list-style:none;margin:0;padding:0;font-size:.88rem;color:var(--ink-2)}
.legend li{display:flex;align-items:center;gap:.4rem}.sw{display:inline-block;width:12px;height:12px;border-radius:50%;flex:none}
.c1{background:var(--s1)}.c2{background:var(--s2)}.c3{background:var(--s3)}
button{font:inherit;font-size:.85rem;color:var(--ink);background:var(--sheet);border:1px solid var(--rule);border-radius:6px;padding:.35rem .85rem;cursor:pointer}
button:hover{border-color:var(--axis)}
.dots{display:grid;gap:2px;--label-w:12rem}
.drow{display:grid;grid-template-columns:var(--label-w) minmax(0,1fr) 7.5rem;align-items:center;gap:.85rem;padding:4px 6px;border-radius:4px}
.drow:hover,.drow:focus-visible{background:var(--accent-wash)}.dlabel{font-size:.9rem;min-width:0}
.dtrack{position:relative;height:34px}.dtrack .g{position:absolute;top:0;bottom:0;width:1px;background:var(--grid)}
.dmark{position:absolute;height:2px;border-radius:1px}.ddot{position:absolute;width:10px;height:10px;border-radius:50%;box-shadow:0 0 0 2px var(--sheet);transform:translate(-50%,-50%)}
.dval{font-size:.8rem;color:var(--ink-2);font-variant-numeric:tabular-nums;line-height:1.25}
.daxis{display:grid;grid-template-columns:var(--label-w) minmax(0,1fr) 7.5rem;gap:.85rem;padding:.3rem 6px 0}
.ticks{position:relative;height:1.2rem;font-size:.75rem;color:var(--muted);font-variant-numeric:tabular-nums}.ticks span{position:absolute;transform:translateX(-50%)}
.ticks span:first-child{transform:none}.ticks span:last-child{transform:translateX(-100%)}
.bars{display:grid;gap:4px;--label-w:7rem}.brow{display:grid;grid-template-columns:var(--label-w) minmax(0,1fr) 3rem;align-items:center;gap:.85rem;padding:3px 6px;border-radius:4px}
.brow:hover{background:var(--accent-wash)}.btrack{position:relative;height:14px;border-left:1px solid var(--axis)}.bfill{position:absolute;inset:0 auto 0 0;background:var(--seq);border-radius:0 4px 4px 0}
.bval{font-size:.82rem;color:var(--ink-2);font-variant-numeric:tabular-nums}
.tip{position:absolute;z-index:3;pointer-events:none;min-width:11rem;max-width:18rem;background:var(--sheet);border:1px solid var(--rule);border-radius:6px;box-shadow:0 6px 20px var(--shadow);padding:.55rem .75rem;font-size:.82rem;line-height:1.45}
.tip strong{display:block;font-size:.92rem;font-weight:600}
figcaption{margin-top:1rem;font-size:.86rem;color:var(--ink-2);max-width:46rem}
.note{border-left:3px solid var(--accent);background:var(--accent-wash);padding:.7rem .9rem;border-radius:0 6px 6px 0;font-size:.92rem}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:1.5rem 2rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr));gap:.9rem}
.tile{background:var(--sheet);border:1px solid var(--rule);border-radius:8px;padding:.8rem .9rem}.tile .k{font-size:.78rem;color:var(--muted)}
.tile .v{font-size:1.5rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.3}.tile .d{font-size:.8rem;color:var(--ink-2)}
footer{font-size:.85rem;color:var(--ink-2);border-top:1px solid var(--rule);padding-top:1.25rem;display:flex;flex-direction:column;gap:.5rem}
@media (max-width:640px){.drow,.daxis{grid-template-columns:7.2rem minmax(0,1fr) 5.6rem;gap:.5rem}.dots{--label-w:7.2rem}body{padding-block:2rem 3rem}}
"""

JS = """
(function(){
  document.querySelectorAll('[data-toggle]').forEach(function(btn){
    btn.addEventListener('click',function(){
      var plot=document.getElementById(btn.dataset.plot), table=document.getElementById(btn.dataset.toggle);
      var asTable=btn.getAttribute('aria-pressed')!=='true';
      btn.setAttribute('aria-pressed',String(asTable)); btn.textContent=asTable?'차트로 보기':'표로 보기';
      plot.hidden=asTable; table.hidden=!asTable;
    });
  });
  document.querySelectorAll('.chart').forEach(function(chart){
    var tip=chart.querySelector('.tip'); if(!tip) return;
    chart.querySelectorAll('[data-tip]').forEach(function(row){
      function show(){ tip.replaceChildren(); var lines=JSON.parse(row.dataset.tip);
        lines.forEach(function(t,i){ var el=document.createElement(i===0?'strong':'div'); el.textContent=t; tip.appendChild(el); });
        tip.hidden=false; var box=chart.getBoundingClientRect(), r=row.getBoundingClientRect();
        tip.style.left=Math.max(0,Math.min(r.left-box.left+r.width*0.35,box.width-tip.offsetWidth))+'px';
        var top=r.top-box.top-tip.offsetHeight-6; tip.style.top=(top<0?r.bottom-box.top+6:top)+'px'; }
      function hide(){ tip.hidden=true; }
      row.addEventListener('pointerenter',show); row.addEventListener('pointerleave',hide);
      row.addEventListener('focus',show); row.addEventListener('blur',hide);
    });
  });
})();
"""

SERIES = [("easyocr", "EasyOCR", "c1", "--s1"), ("paddleocr", "PaddleOCR", "c2", "--s2"), ("hybrid", "EasyOCR+Paddle 재인식", "c3", "--s3")]


def esc(value):
    return html.escape(str(value))


def page(title, eyebrow, heading, lead, meta, body, footer):
    meta_html = "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in meta)
    return (f'<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(title)}</title>\n"
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Black+Han+Sans&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap">\n'
            f"<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
            f'<header class="intro wrap"><p class="eyebrow"><span class="reg" aria-hidden="true"></span>{esc(eyebrow)}</p><h1>{esc(heading)}</h1>'
            f'<p class="lead">{lead}</p><dl class="meta">{meta_html}</dl></header>\n{body}\n'
            f'<footer class="wrap">{footer}</footer>\n</main>\n<script>{JS}</script>\n</body>\n</html>\n')


def table(headers, rows, num_cols=(), ci_cols=()):
    head = "".join(f'<th class="num">{esc(h)}</th>' if i in num_cols else f"<th>{esc(h)}</th>" for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in num_cols else (' class="ci"' if i in ci_cols else "")
            cells.append(f"<td{cls}>{cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def pct(value, digits=1):
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def dot_chart(chart_id, rows, series=SERIES, domain=(0.0, 1.0), ticks=(0, .25, .5, .75, 1), fmt=pct, caption="", table_html=""):
    """행마다 경로별 점(값)과 2px 구간선(95% 신뢰구간). rows: [(label, {series_key: (value, lo, hi, n_text)})]."""
    lo, hi = domain
    pos = lambda v: 0 if v is None else max(0, min(100, (v - lo) / (hi - lo) * 100))
    legend = "".join(f'<li><i class="sw {cls}"></i>{esc(name)}</li>' for key, name, cls, _ in series)
    out = []
    for label, values in rows:
        marks, texts, tips = [], [], [label]
        for idx, (key, name, cls, var) in enumerate(series):
            if key not in values or values[key][0] is None:
                continue
            value, low, high, note = values[key]
            y = 8 + idx * 9
            if low is not None and high is not None:
                marks.append(f'<span class="dmark" style="left:{pos(low):.2f}%;width:{max(0.4, pos(high) - pos(low)):.2f}%;top:{y - 1}px;background:var({var})"></span>')
            marks.append(f'<span class="ddot" style="left:{pos(value):.2f}%;top:{y}px;background:var({var})"></span>')
            texts.append(fmt(value))
            tips.append(f"{name}: {fmt(value)}" + (f" (95% {fmt(low)}–{fmt(high)})" if low is not None else "") + (f" · {note}" if note else ""))
        grid = "".join(f'<span class="g" style="left:{pos(t):.2f}%"></span>' for t in ticks)
        out.append(f'<div class="drow" tabindex="0" data-tip="{esc(json.dumps(tips, ensure_ascii=False))}"><span class="dlabel">{esc(label)}</span>'
                   f'<span class="dtrack">{grid}{"".join(marks)}</span><span class="dval">{" · ".join(texts)}</span></div>')
    tick_html = "".join(f'<span style="left:{pos(t):.2f}%">{fmt(t, 0) if fmt is pct else fmt(t)}</span>' for t in ticks)
    return (f'<figure class="chart"><div class="chart-bar"><ul class="legend">{legend}</ul>'
            f'<button type="button" data-toggle="{chart_id}-table" data-plot="{chart_id}-plot" aria-pressed="false">표로 보기</button></div>'
            f'<div id="{chart_id}-plot"><div class="dots">{"".join(out)}</div><div class="daxis" aria-hidden="true"><span></span><span class="ticks">{tick_html}</span><span></span></div></div>'
            f'<div id="{chart_id}-table" hidden>{table_html}</div><div class="tip" hidden></div>'
            f'{"<figcaption>" + caption + "</figcaption>" if caption else ""}</figure>')


def bar_chart(chart_id, rows, caption="", unit="장", table_html=""):
    """단일 계열 가로 막대. rows: [(label, value, tip)]."""
    top = max((v for _, v, _ in rows), default=1) or 1
    items = "".join(f'<div class="brow" data-tip="{esc(json.dumps([label, tip or f"{value}{unit}"], ensure_ascii=False))}">'
                    f'<span class="dlabel">{esc(label)}</span><span class="btrack"><span class="bfill" style="width:{value / top * 100:.2f}%"></span></span>'
                    f'<span class="bval">{value}</span></div>' for label, value, tip in rows)
    return (f'<figure class="chart"><div class="chart-bar"><span class="sub" style="margin:0">{esc(unit)} 수</span>'
            f'<button type="button" data-toggle="{chart_id}-table" data-plot="{chart_id}-plot" aria-pressed="false">표로 보기</button></div>'
            f'<div id="{chart_id}-plot"><div class="bars">{items}</div></div><div id="{chart_id}-table" hidden>{table_html}</div>'
            f'<div class="tip" hidden></div>{"<figcaption>" + caption + "</figcaption>" if caption else ""}</figure>')
