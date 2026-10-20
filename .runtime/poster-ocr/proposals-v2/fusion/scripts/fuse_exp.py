"""Scratch fusion experimenter (does not touch app/). Builds fused rgb_2x docs from v1 predictions with a named policy.

Inputs are only the pipelines' own predictions and OCR records (no gold). Writes <pred_root>/<name>/<case>/rgb_2x.poster.json
and validates each doc with validate_poster against the union of OCR ids of every run it drew from.
usage: python fuse_exp.py <policy[+policy...]> <name> [--pred-root R] [--require pipe ...]
"""
import argparse
import copy
import json
import re
import unicodedata
from pathlib import Path

from app.festival_data.poster import LISTS, SCALARS, validate_poster
from app.festival_data.settings import CollectionError

ROOT = Path(".").resolve()
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
RUNS = ROOT / "data/runtime/poster-ocr/runs"
LINKED = {"schedule": ("schedule", "year"), "year": ("schedule", "year")}


def key(text):
    if text is None:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(text)).casefold() if ch.isalnum())


def slot_key(doc, slot):
    s = doc[slot]
    if s["status"] != "present":
        return None
    if slot == "title":
        return key(s["official"]) or None
    if slot == "venue":
        return key(s["raw"]) or None
    if slot == "schedule":
        st = s["start"]
        if st is None:
            return None
        en = s["end"] or st
        return (st["month"], st["day"], en["month"], en["day"])
    if slot == "edition":
        return s["number"]
    if slot == "year":
        return s["value"]
    raise KeyError(slot)


def take(doc, src, slot, used):
    for s in LINKED.get(slot, (slot,)):
        doc[s] = copy.deepcopy(src[s])
    used.add(slot)


def fallback(doc, docs, order, used):
    """fusion.py logic, over a priority list of secondaries."""
    for name in order:
        sec = docs.get(name)
        if sec is None:
            continue
        for slot in [*SCALARS, *LISTS]:
            if doc[slot]["status"] == "present" or sec[slot]["status"] != "present":
                continue
            group = LINKED.get(slot, (slot,))
            if any(doc[s]["status"] == "present" for s in group if s != slot):
                continue
            take(doc, sec, slot, used)


def vote(doc, docs, voters, weights, slots, used):
    """Weighted plurality over normalised slot values; the current (primary) value wins ties."""
    for slot in slots:
        tally, first = {}, {}
        for name in voters:
            d = docs.get(name)
            if d is None:
                continue
            k = slot_key(d, slot)
            if k is None:
                continue
            tally[k] = tally.get(k, 0) + weights.get(name, 1.0)
            first.setdefault(k, name)
        if not tally:
            continue
        pk = slot_key(doc, slot)
        best = max(tally, key=lambda k: (tally[k], k == pk))
        if best != pk and tally[best] > tally.get(pk, 0):
            take(doc, docs[first[best]], slot, used)


def override(doc, docs, src_name, slots, used, present_only=True):
    src = docs.get(src_name)
    if src is None:
        return
    for slot in slots:
        if present_only and src[slot]["status"] != "present":
            continue
        take(doc, src, slot, used)


def phone_digits(item):
    return re.sub(r"\D", "", item["value"] or item["raw"])


def union_contacts(doc, docs, src_name, used):
    src = docs.get(src_name)
    if src is None or src["contacts"]["status"] != "present":
        return
    have = {phone_digits(i) for i in doc["contacts"]["items"] if i["kind"] == "phone"}
    added = False
    for item in src["contacts"]["items"]:
        digits = phone_digits(item) if item["kind"] == "phone" else ""
        if digits and digits not in have:
            doc["contacts"]["items"].append(copy.deepcopy(item))
            have.add(digits)
            added = True
    if added:
        doc["contacts"]["status"] = "present"
        used.add("contacts")


CURRENT = {"case": None}
_CONF = {}


def evidence_conf(pipe, slot, doc):
    """Mean OCR confidence of the records a slot cites (same engine → comparable across variants)."""
    ev = doc[slot].get("evidence") if isinstance(doc[slot], dict) else None
    if not ev or not ev.get("ocr_ids"):
        return None
    cid = CURRENT["case"]
    if (pipe, cid) not in _CONF:
        name, var = pipe.split("/")
        payload = json.loads((RUNS / name / cid / f"{var}.json").read_text(encoding="utf-8"))
        _CONF[(pipe, cid)] = {r["id"]: r.get("confidence") for r in payload["records"]}
    vals = [_CONF[(pipe, cid)].get(i) for i in ev["ocr_ids"]]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def conf_pick(doc, docs, primary, other, slots, used, margin=0.0):
    """Both variants of one engine: keep the slot value whose cited lines the engine read more confidently."""
    if other not in docs:
        return
    for slot in slots:
        a, b = slot_key(doc, slot), slot_key(docs[other], slot)
        if a is None or b is None or a == b:
            continue
        ca, cb = evidence_conf(primary, slot, docs[primary]), evidence_conf(other, slot, docs[other])
        if ca is not None and cb is not None and cb > ca + margin:
            take(doc, docs[other], slot, used)


P, H2, H1, E2, E1, P1 ="paddleocr/rgb_2x", "hybrid/rgb_2x", "hybrid/rgb_1x", "easyocr/rgb_2x", "easyocr/rgb_1x", "paddleocr/rgb_1x"
CORE = ["title", "venue", "schedule"]
POLICIES = {
    # fallback chains (absent-only, = fusion.py)
    "fb_h": lambda d, docs, u: fallback(d, docs, [H2], u),
    "fb_e": lambda d, docs, u: fallback(d, docs, [E2], u),
    "fb_h_e": lambda d, docs, u: fallback(d, docs, [H2, E2], u),
    "fb_h_h1": lambda d, docs, u: fallback(d, docs, [H2, H1], u),
    "fb_p1": lambda d, docs, u: fallback(d, docs, [P1], u),
    "fb_p1_h": lambda d, docs, u: fallback(d, docs, [P1, H2], u),
    # per-field engine choice
    "pf_contacts_h": lambda d, docs, u: override(d, docs, H2, ["contacts"], u, present_only=False),
    "pf_contacts_h_present": lambda d, docs, u: override(d, docs, H2, ["contacts"], u),
    "pf_contacts_union_h": lambda d, docs, u: union_contacts(d, docs, H2, u),
    "pf_contacts_union_e": lambda d, docs, u: union_contacts(d, docs, E2, u),
    "pf_edition_h": lambda d, docs, u: override(d, docs, H2, ["edition"], u),
    "pf_orgs_h": lambda d, docs, u: override(d, docs, H2, ["organizations"], u),
    "pf_orgs_e": lambda d, docs, u: override(d, docs, E2, ["organizations"], u),
    "pf_venue_h": lambda d, docs, u: override(d, docs, H2, ["venue"], u),
    "pf_contacts_union_p1": lambda d, docs, u: union_contacts(d, docs, P1, u),
    # voting: paddle weight w, others 1 (paddle value kept on ties)
    "vote3_core": lambda d, docs, u: vote(d, docs, [P, H2, E2], {P: 1.5}, CORE, u),
    "vote5_core_w25": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 2.5}, CORE, u),
    "vote5_core_w35": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 3.5}, CORE, u),
    "vote3h_core": lambda d, docs, u: vote(d, docs, [P, H2, H1], {P: 1.5}, CORE, u),
    "vote5_title_w25": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 2.5}, ["title"], u),
    "vote5_venue_w25": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 2.5}, ["venue"], u),
    "vote5_sched_w25": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 2.5}, ["schedule"], u),
    "vote5_ed_w25": lambda d, docs, u: vote(d, docs, [P, H2, H1, E2, E1], {P: 2.5}, ["edition"], u),
    "vote3h_title": lambda d, docs, u: vote(d, docs, [P, H2, H1], {P: 1.5}, ["title"], u),
    "vote3h_venue": lambda d, docs, u: vote(d, docs, [P, H2, H1], {P: 1.5}, ["venue"], u),
    "vote3h_sched": lambda d, docs, u: vote(d, docs, [P, H2, H1], {P: 1.5}, ["schedule"], u),
    # paddle 2x + 1x (+ hybrid as third voter)
    "vote_pp1h_core": lambda d, docs, u: vote(d, docs, [P, P1, H2], {P: 1.5}, CORE, u),
    "vote_pp1h2h1_core": lambda d, docs, u: vote(d, docs, [P, P1, H2, H1], {P: 1.5}, CORE, u),
    "vote_pp1h_title": lambda d, docs, u: vote(d, docs, [P, P1, H2], {P: 1.5}, ["title"], u),
    "vote_pp1h_venue": lambda d, docs, u: vote(d, docs, [P, P1, H2], {P: 1.5}, ["venue"], u),
    "vote_pp1h_sched": lambda d, docs, u: vote(d, docs, [P, P1, H2], {P: 1.5}, ["schedule"], u),
    "conf_p1_core": lambda d, docs, u: conf_pick(d, docs, P, P1, CORE, u),
    "conf_p1_title": lambda d, docs, u: conf_pick(d, docs, P, P1, ["title"], u),
    "conf_p1_title_m01": lambda d, docs, u: conf_pick(d, docs, P, P1, ["title"], u, margin=0.01),
    "conf_p1_title_m05": lambda d, docs, u: conf_pick(d, docs, P, P1, ["title"], u, margin=0.05),
    "conf_p1_venue": lambda d, docs, u: conf_pick(d, docs, P, P1, ["venue"], u),
    "conf_p1_sched": lambda d, docs, u: conf_pick(d, docs, P, P1, ["schedule"], u),
    "pf_orgs_p1": lambda d, docs, u: override(d, docs, P1, ["organizations"], u, present_only=False),
    "pf_orgs_p1_present": lambda d, docs, u: override(d, docs, P1, ["organizations"], u),
    "pf_contacts_p1": lambda d, docs, u: override(d, docs, P1, ["contacts"], u, present_only=False),
    "pf_contacts_p1_present": lambda d, docs, u: override(d, docs, P1, ["contacts"], u),
    "pf_edition_p1": lambda d, docs, u: override(d, docs, P1, ["edition"], u),
    "conf_p1_all":lambda d, docs, u: conf_pick(d, docs, P, P1, ["title", "venue", "schedule", "edition"], u),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("policy")
    ap.add_argument("name")
    ap.add_argument("--pred-root", type=Path, default=ROOT / "data/runtime/poster-ocr/pred")
    ap.add_argument("--require", nargs="*", default=[], help="pipelines that must exist for a case to be fused (else skipped)")
    args = ap.parse_args()
    cases = [c["id"] for c in json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"] if c["split"] in ("dev", "test")]
    stats = {"documents": 0, "valid": 0, "taken": {}, "errors": [], "skipped": 0}
    for cid in cases:
        docs = {}
        for pipe in (P, P1, H2, H1, E2, E1):
            name, var = pipe.split("/")
            path = args.pred_root / name / cid / f"{var}.poster.json"
            if path.exists():
                docs[pipe] = json.loads(path.read_text(encoding="utf-8"))
        if P not in docs or any(r not in docs for r in args.require):
            stats["skipped"] += 1
            continue
        doc, used = copy.deepcopy(docs[P]), set()
        CURRENT["case"] = cid
        for part in args.policy.split("+"):
            POLICIES[part](doc, docs, used)
        doc["extraction"]["extractor"]["model"] = f"fusion:{args.policy}"
        doc["extraction"]["notes"] = doc["extraction"]["notes"] + [f"필드 단위 결합({args.policy}): {', '.join(sorted(used)) or '없음'}"]
        ids = set()
        for pipe in docs:
            name, var = pipe.split("/")
            p = RUNS / name / cid / f"{var}.json"
            if p.exists():
                ids |= {r["id"] for r in json.loads(p.read_text(encoding="utf-8"))["records"]}
        stats["documents"] += 1
        for s in used:
            stats["taken"][s] = stats["taken"].get(s, 0) + 1
        try:
            validate_poster(doc, ids)
            stats["valid"] += 1
        except CollectionError as e:
            stats["errors"].append(f"{cid}: {e}")
        target = args.pred_root / args.name / cid / "rgb_2x.poster.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(args.name, json.dumps({k: (v[:5] if k == "errors" else v) for k, v in stats.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
