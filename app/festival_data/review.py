"""Write review.html: a static page that hotlinks T1/T2 candidates and exports decisions.json.

Images are only displayed from their original URLs (공공누리 제3유형: no copies, no edits); ingest downloads
just the accepted ones. Keys: Enter accept, 1-9 pick a candidate, X no poster / wrong year, J/K or arrows move.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .schema import now
from .worklist import POSTER_RATIO, build_units

PUBLIC = ["url", "thumb", "name", "serial", "copyright", "endpoint", "hint", "auto", "width", "height"]

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>포스터 후보 검토</title>
<style>
:root { --bg: #f6f7f9; --card: #fff; --text: #1d2330; --muted: #5d6675; --line: #d8dde5; --accent: #1f6feb; --ok: #1a7f37; --no: #b42318; --t1: #e6f4ea; --t2: #fff4e5; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 system-ui, "Malgun Gothic", sans-serif; }
header { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 10px 16px; background: var(--card); border-bottom: 1px solid var(--line); }
header h1 { margin: 0; font-size: 16px; }
header .keys { color: var(--muted); font-size: 12px; }
button, select { font: inherit; padding: 4px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--card); color: var(--text); cursor: pointer; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
main { max-width: 1280px; margin: 0 auto; padding: 16px; }
.card { background: var(--card); border: 2px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; scroll-margin-top: 96px; }
.card.current { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(31,111,235,.15); }
.card.accept { border-left: 8px solid var(--ok); }
.card.reject { border-left: 8px solid var(--no); opacity: .7; }
.head { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
.head h2 { margin: 0; font-size: 16px; }
.tier { font-weight: 700; padding: 0 6px; border-radius: 4px; }
.tier.T1 { background: var(--t1); } .tier.T2 { background: var(--t2); }
.facts { color: var(--muted); margin: 2px 0 8px; }
.facts b { color: var(--text); }
.links a { margin-right: 10px; color: var(--accent); }
.state { font-weight: 700; } .state.accept { color: var(--ok); } .state.reject { color: var(--no); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
figure { position: relative; margin: 0; border: 2px solid var(--line); border-radius: 8px; padding: 4px; cursor: pointer; background: #fafbfc; }
figure.selected { border-color: var(--ok); background: var(--t1); }
figure img { display: block; width: 100%; height: 280px; object-fit: contain; background: #eef0f3; }
figure .n { position: absolute; top: 6px; left: 6px; min-width: 22px; padding: 0 4px; border-radius: 4px; background: rgba(0,0,0,.7); color: #fff; text-align: center; font-weight: 700; }
figcaption { font-size: 12px; color: var(--muted); word-break: break-all; }
figcaption .hint { color: var(--ok); font-weight: 700; }
.empty { color: var(--muted); }
@media (max-width: 600px) { main { padding: 8px; } .grid { grid-template-columns: repeat(2, 1fr); } figure img { height: 200px; } }
</style>
</head>
<body>
<header>
  <h1>포스터 후보 검토</h1>
  <span id="progress"></span>
  <select id="filter" aria-label="표시할 카드">
    <option value="all">전체</option><option value="T1">T1</option><option value="T2">T2</option><option value="open">미결정</option>
  </select>
  <button class="primary" id="export">decisions.json 내보내기 (E)</button>
  <span class="keys">Enter 채택 · 1–9 후보 선택 · X 포스터 없음/연도 불일치 · J/K·↑↓ 이동 · Backspace 결정 취소</span>
</header>
<main id="cards"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);
const STORE = "poster-review:" + DATA.run;
const cards = document.getElementById("cards");
let saved = {};
try { saved = JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch (error) { saved = {}; }
// Decisions remember the chosen image URL, not its position, so a regenerated page keeps them.
const state = {};
for (const unit of DATA.units) {
  const previous = saved[unit.wl_id];
  const preselected = unit.selected === null ? null : unit.candidates[unit.selected].url;
  state[unit.wl_id] = previous && typeof previous === "object" && (previous.decision !== "accept" || unit.candidates.some(c => c.url === previous.url))
    ? previous : preselected ? { decision: "accept", url: preselected } : { decision: null, url: null };
}
let visible = [];
let current = 0;

function persist() {
  try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (error) { /* private window: keep in memory only */ }
}
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "text") node.textContent = value; else if (key === "class") node.className = value; else node.setAttribute(key, value);
  }
  for (const child of children || []) node.append(child);
  return node;
}
function link(label, url) { return url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: label }) : ""; }
function sizeText(c) { return c.width + "×" + c.height + " · 비율 " + (c.height / c.width).toFixed(2); }
function chosenIndex(unit) { return unit.candidates.findIndex(c => c.url === state[unit.wl_id].url); }

function rerank(unit) {
  // Undecided cards: candidates without a poster name move toward the A-series ratio once sizes are known.
  if (state[unit.wl_id].decision) return;
  const keyed = unit.candidates.map((c, position) => {
    const ratio = c.width && c.height ? c.height / c.width : null;
    return { c, key: [!c.auto, !c.hint, ratio ? Math.abs(ratio - DATA.poster_ratio) : 9, c.endpoint !== "detailImage2", position] };
  });
  keyed.sort((a, b) => { for (let i = 0; i < a.key.length; i++) { if (a.key[i] !== b.key[i]) return a.key[i] < b.key[i] ? -1 : 1; } return 0; });
  if (keyed.every((entry, position) => entry.c === unit.candidates[position])) return;
  unit.candidates = keyed.map(entry => entry.c);
  update(unit);
}

function card(unit) {
  const decision = state[unit.wl_id];
  const chosen = chosenIndex(unit);
  const position = visible.indexOf(unit);
  const node = el("section", { class: "card" + (position === current ? " current" : "") + (decision.decision ? " " + decision.decision : ""), id: unit.wl_id });
  const label = decision.decision === "accept" ? "채택 " + (chosen + 1) + "번" : decision.decision === "reject" ? "포스터 없음" : "미결정";
  node.append(el("div", { class: "head" }, [
    el("span", { class: "tier " + unit.tier, text: unit.tier }), el("h2", { text: unit.title }),
    el("span", { class: "state " + (decision.decision || ""), text: label }), el("span", { class: "facts", text: unit.reason }),
  ]));
  const tour = unit.tour_title && unit.tour_title !== unit.title ? unit.tour_title + " " + unit.tour_dates : unit.tour_dates;
  const facts = [["기대 기간", unit.expected_dates], ["장소", unit.venue], ["기관", unit.institution], ["TourAPI", tour]];
  node.append(el("div", { class: "facts" }, facts.filter(([, value]) => value).flatMap(([name, value]) => [el("b", { text: name + " " }), value + "   "])));
  node.append(el("div", { class: "links" }, [link("구석구석(kfes)", unit.kfes_url), link("홈페이지", unit.homepage_url), link("검색", unit.search_url)]));
  const grid = el("div", { class: "grid" });
  let waiting = unit.candidates.length;
  const settle = () => { if (--waiting === 0) rerank(unit); };
  unit.candidates.forEach((c, index) => {
    const size = el("span", { text: c.width ? sizeText(c) : "" });
    const caption = el("figcaption", {}, [
      c.auto ? el("span", { class: "hint", text: "포스터명 " }) : c.hint ? el("span", { text: "포스터명(다른 연도) " }) : "",
      (c.name || (c.endpoint === "searchFestival2" ? "대표이미지" : "")) + " · ", size,
    ]);
    const image = el("img", { src: c.url, alt: c.name || unit.title, loading: "lazy", referrerpolicy: "no-referrer" });
    image.addEventListener("load", () => {
      if (!c.width) { c.width = image.naturalWidth; c.height = image.naturalHeight; }
      size.textContent = sizeText(c);
      settle();
    }, { once: true });
    image.addEventListener("error", settle, { once: true });
    const figure = el("figure", { class: index === chosen && decision.decision === "accept" ? "selected" : "" },
      [el("span", { class: "n", text: index < 9 ? String(index + 1) : "" }), image, caption]);
    figure.addEventListener("click", event => { event.stopPropagation(); focus(visible.indexOf(unit), false); choose(index); });
    grid.append(figure);
  });
  node.append(unit.candidates.length ? grid : el("p", { class: "empty", text: "후보 이미지 없음" }));
  node.addEventListener("click", () => focus(visible.indexOf(unit), false));
  return node;
}

function update(unit) {
  const node = document.getElementById(unit.wl_id);
  if (node) node.replaceWith(card(unit));
  progress();
}
function progress() {
  const decided = DATA.units.filter(unit => state[unit.wl_id].decision);
  document.getElementById("progress").textContent = "결정 " + decided.length + " / " + DATA.units.length
    + " · 채택 " + decided.filter(unit => state[unit.wl_id].decision === "accept").length;
}
function renderAll() {
  const filter = document.getElementById("filter").value;
  visible = DATA.units.filter(unit => filter === "all" || unit.tier === filter || (filter === "open" && !state[unit.wl_id].decision));
  current = 0;
  cards.replaceChildren(...visible.map(card));
  if (!visible.length) cards.append(el("p", { class: "empty", text: "표시할 카드가 없습니다." }));
  progress();
}
function focus(position, scroll) {
  if (!visible.length) return;
  const before = document.getElementById(visible[current].wl_id);
  if (before) before.classList.remove("current");
  current = Math.max(0, Math.min(position, visible.length - 1));
  const after = document.getElementById(visible[current].wl_id);
  if (after) { after.classList.add("current"); if (scroll !== false) after.scrollIntoView({ block: "start" }); }
}
function choose(index) {
  const unit = visible[current];
  if (!unit || index >= unit.candidates.length) return;
  state[unit.wl_id] = { decision: "accept", url: unit.candidates[index].url };
  persist(); update(unit);
}
function decide(decision) {
  const unit = visible[current];
  if (!unit) return;
  if (decision === "accept") {
    if (!unit.candidates.length) return;
    const index = chosenIndex(unit);
    state[unit.wl_id] = { decision: "accept", url: unit.candidates[index < 0 ? 0 : index].url };
  } else {
    state[unit.wl_id] = { decision, url: null };
  }
  persist(); update(unit); focus(current + 1);
}
function decisionsDocument() {
  const decisions = [];
  for (const unit of DATA.units) {
    const decision = state[unit.wl_id];
    if (!decision.decision) continue;
    const entry = { wl_id: unit.wl_id, tier: unit.tier, decision: decision.decision, content_id: unit.content_id };
    if (decision.decision === "accept") {
      const c = unit.candidates[chosenIndex(unit)];
      Object.assign(entry, { url: c.url, serial: c.serial, endpoint: c.endpoint, image_name: c.name });
    }
    decisions.push(entry);
  }
  return { format: "poster-review/1", run: DATA.run, exported_at: new Date().toISOString(), decisions };
}
function exportDecisions() {
  const text = JSON.stringify(decisionsDocument(), null, 2);
  const anchor = el("a", { href: URL.createObjectURL(new Blob([text], { type: "application/json" })), download: "decisions.json" });
  document.body.append(anchor); anchor.click(); anchor.remove();
}
document.addEventListener("keydown", event => {
  if (event.target.tagName === "SELECT" || event.ctrlKey || event.metaKey || event.altKey) return;
  const key = event.key;
  if (key === "Enter") decide("accept");
  else if (key === "x" || key === "X") decide("reject");
  else if (key === "Backspace") { const unit = visible[current]; if (unit) { state[unit.wl_id] = { decision: null, url: null }; persist(); update(unit); } }
  else if (/^[1-9]$/.test(key)) choose(Number(key) - 1);
  else if (key === "j" || key === "ArrowDown") focus(current + 1);
  else if (key === "k" || key === "ArrowUp") focus(current - 1);
  else if (key === "e" || key === "E") exportDecisions();
  else return;
  event.preventDefault();
});
document.getElementById("filter").addEventListener("change", renderAll);
document.getElementById("export").addEventListener("click", exportDecisions);
renderAll();
</script>
</body>
</html>
"""


def page_data(run_dir, units):
    reviewable = [unit for unit in units if unit["tier"] in {"T1", "T2"}]
    reviewable.sort(key=lambda unit: (unit["tier"], unit["start_date"] or "9999", unit["title"]))
    return {
        "run": Path(run_dir).resolve().name, "generated_at": now(), "poster_ratio": POSTER_RATIO,
        "units": [{
            **{key: unit[key] for key in ["wl_id", "tier", "reason", "title", "expected_dates", "venue", "institution",
                                         "content_id", "tour_title", "tour_dates", "kfes_url", "homepage_url", "search_url", "selected"]},
            "candidates": [{key: entry[key] for key in PUBLIC} for entry in unit["candidates"]],
        } for unit in reviewable],
    }


def make_review(run_dir, output, year=None):
    data = page_data(run_dir, build_units(run_dir, year))
    # JSON inside <script>: escape '<' and line separators so API text cannot close the element.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8", newline="\n")
    return {"output": str(output), "cards": len(data["units"]), "tiers": dict(Counter(unit["tier"] for unit in data["units"])),
            "candidates": sum(len(unit["candidates"]) for unit in data["units"])}
