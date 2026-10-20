"""Step 2/3: match 2026 standard-data rows to TourAPI searchFestival2 rows (offline, no API calls)."""
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from app.festival_data.normalize import parse_items  # noqa: E402

SUFFIXES = ["페스티벌", "페스티발", "festival", "fiesta", "페스타", "festa", "대축제", "축제", "축전", "한마당", "fest", "문화제", "대잔치", "잔치"]

SIDO = {
    "서울": ["서울특별시", "서울시", "서울"], "부산": ["부산광역시", "부산시", "부산"], "대구": ["대구광역시", "대구시", "대구"],
    "인천": ["인천광역시", "인천시", "인천"], "광주": ["광주광역시", "광주특별시", "광주시", "광주"], "대전": ["대전광역시", "대전시", "대전"],
    "울산": ["울산광역시", "울산시", "울산"], "세종": ["세종특별자치시", "세종시", "세종"], "경기": ["경기도", "경기"],
    "강원": ["강원특별자치도", "강원도", "강원"], "충북": ["충청북도", "충북"], "충남": ["충청남도", "충남"],
    "전북": ["전북특별자치도", "전라북도", "전북"], "전남": ["전라남도", "전남", "전남광주통합특별시"],
    "경북": ["경상북도", "경북"], "경남": ["경상남도", "경남"], "제주": ["제주특별자치도", "제주도", "제주"],
}
ALL_SIDO_TOKENS = sorted({t for v in SIDO.values() for t in v}, key=len, reverse=True)


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "").casefold()


def base_key(s, drop_paren=True):
    s = nfkc(s)
    if drop_paren:
        s = re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]*>|〈[^〉]*〉|《[^》]*》|「[^」]*」|『[^』]*』", " ", s)
    s = re.sub(r"제\s*\d+\s*회", " ", s)
    s = re.sub(r"\d+\s*(?:st|nd|rd|th)\b", " ", s)
    s = re.sub(r"(?:19|20)\d{2}\s*(?:년도|년)?", " ", s)
    s = re.sub(r"^\s*'?\d{2}\s+", " ", s)
    s = re.sub(r"[^0-9a-z가-힣]", "", s)
    return s


def core_key(s):
    k = s
    changed = True
    while changed and k:
        changed = False
        for suf in SUFFIXES:
            if k.endswith(suf) and len(k) > len(suf):
                k = k[: -len(suf)]
                changed = True
                break
    return k


def region_tokens(*addresses):
    toks = set()
    for a in addresses:
        a = nfkc(a)
        parts = re.split(r"\s+", a.strip())
        for p in parts[:3]:
            p = re.sub(r"[^가-힣]", "", p)
            if re.fullmatch(r"[가-힣]{1,6}(?:시|군|구)", p) and p not in ALL_SIDO_TOKENS:
                toks.add(p)
                if len(p) >= 3:
                    toks.add(p[:-1])
    return toks


def sido_of(address):
    a = nfkc(address).strip()
    for key, names in SIDO.items():
        for n in sorted(names, key=len, reverse=True):
            if a.startswith(n):
                return key
    return None


def strip_region(key, tokens):
    out = key
    for t in sorted(tokens, key=len, reverse=True):
        if len(t) >= 2 and out.startswith(t) and len(out) > len(t) + 1:
            out = out[len(t):]
    for t in sorted(ALL_SIDO_TOKENS, key=len, reverse=True):
        if len(t) >= 2 and out.startswith(t) and len(out) > len(t) + 1:
            out = out[len(t):]
            break
    return out


def iso(d):
    d = (d or "").strip().replace(".", "-").replace("/", "-")
    if re.fullmatch(r"\d{8}", d):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else None


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) and v != 0 else None
    except (TypeError, ValueError):
        return None


def km(a, b):
    if None in (a[0], a[1], b[0], b[1]):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def prep_std(it, idx):
    name = it.get("fstvlNm") or ""
    addr = [it.get("rdnmadr") or "", it.get("lnmadr") or ""]
    toks = region_tokens(*addr, it.get("insttNm") or "")
    k = base_key(name)
    return {
        "idx": idx, "name": name, "start": iso(it.get("fstvlStartDate")), "end": iso(it.get("fstvlEndDate")) or iso(it.get("fstvlStartDate")),
        "key": k, "key_paren": base_key(name, drop_paren=False), "core": core_key(k), "rcore": core_key(strip_region(k, toks)),
        "toks": toks, "sido": sido_of(addr[0] or addr[1]), "ll": (fnum(it.get("latitude")), fnum(it.get("longitude"))),
        "addr": addr[0] or addr[1], "homepage": (it.get("homepageUrl") or "").strip(), "instt": it.get("insttNm") or "",
    }


def prep_tour(it):
    name = it.get("title") or ""
    toks = region_tokens(it.get("addr1") or "")
    k = base_key(name)
    return {
        "cid": str(it.get("contentid")), "name": name, "start": iso(it.get("eventstartdate")), "end": iso(it.get("eventenddate")) or iso(it.get("eventstartdate")),
        "key": k, "key_paren": base_key(name, drop_paren=False), "core": core_key(k), "rcore": core_key(strip_region(k, toks)),
        "toks": toks, "sido": sido_of(it.get("addr1") or ""), "ll": (fnum(it.get("mapy")), fnum(it.get("mapx"))),
        "addr": it.get("addr1") or "", "firstimage": (it.get("firstimage") or "").strip(),
    }


def overlap(a, b, tol_days=0):
    from datetime import date, timedelta
    if not (a["start"] and b["start"]):
        return False
    t = timedelta(days=tol_days)
    s1, e1 = date.fromisoformat(a["start"]), date.fromisoformat(a["end"] or a["start"])
    s2, e2 = date.fromisoformat(b["start"]), date.fromisoformat(b["end"] or b["start"])
    return s1 <= e2 + t and s2 <= e1 + t


def region(a, b):
    """Return (agree, conflict, detail)."""
    d = km(a["ll"], b["ll"])
    tok = bool(a["toks"] & b["toks"])
    if d is not None:
        if d <= 10:
            return True, False, f"{d:.1f}km"
        if tok and d <= 30:
            return True, False, f"{d:.1f}km+sigungu"
        return False, d > 60, f"{d:.1f}km"
    if tok:
        return True, False, "sigungu"
    if a["sido"] and b["sido"] and a["sido"] != b["sido"]:
        return False, True, "sido-differs"
    return False, False, "unknown"


def name_score(a, b):
    if a["key"] and (a["key"] == b["key"] or a["key_paren"] == b["key_paren"]):
        return 4, "exact"
    if a["core"] and a["core"] == b["core"] and len(a["core"]) >= 2:
        return 3, "core"
    if a["rcore"] and a["rcore"] == b["rcore"] and len(a["rcore"]) >= 2:
        return 3, "core-noregion"
    short, long_ = sorted([a["rcore"], b["rcore"]], key=len)
    if len(short) >= 3 and short in long_:
        return 2, "contains"
    short, long_ = sorted([a["core"], b["core"]], key=len)
    if len(short) >= 3 and short in long_:
        return 2, "contains"
    pa, pb = core_key(a["key_paren"]), core_key(b["key_paren"])
    short, long_ = sorted([pa, pb], key=len)
    if len(short) >= 4 and short in long_:
        return 2, "contains-paren"
    r = max(SequenceMatcher(None, a["rcore"], b["rcore"]).ratio(), SequenceMatcher(None, a["core"], b["core"]).ratio())
    if r >= 0.75:
        return 1, f"similar{r:.2f}"
    d = km(a["ll"], b["ll"])
    if r >= 0.6 and a["start"] == b["start"] and a["end"] == b["end"] and d is not None and d <= 2:
        return 1, f"samedates-similar{r:.2f}"
    return 0, f"ratio{r:.2f}"


def accept(ns, ra, rc, core_len=0):
    tier, _ = ns
    if tier == 4:
        return not rc
    if tier == 3:
        return ra
    if tier in (2, 1):
        return ra
    return False


def main():
    std_items = [it for p in json.loads((OUT / "standard.raw.json").read_text(encoding="utf-8"))["pages"] for it in parse_items(p)]
    tour_items = [it for p in json.loads((OUT / "tourapi.raw.json").read_text(encoding="utf-8"))["pages"] for it in parse_items(p)]
    std = [prep_std(it, i) for i, it in enumerate(std_items) if str(it.get("fstvlStartDate") or "").startswith("2026")]
    tour_all = [prep_tour(it) for it in tour_items]
    tour = [t for t in tour_all if (t["start"] or "").startswith("2026")]

    matches = {}
    loose = {}
    for a in std:
        best = None
        best_loose = None
        for b in tour:
            ns = name_score(a, b)
            if ns[0] == 0:
                continue
            ra, rc, rd = region(a, b)
            if overlap(a, b) and accept(ns, ra, rc, min(len(a["core"]), len(b["core"]))):
                score = (ns[0], 1 if ra else 0, -(km(a["ll"], b["ll"]) or 999))
                if best is None or score > best[0]:
                    best = (score, b, ns[1], rd)
            elif ns[0] >= 3 and overlap(a, b, tol_days=31) and (ra or ns[0] == 4 and not rc):
                score = (ns[0], 1 if ra else 0)
                if best_loose is None or score > best_loose[0]:
                    best_loose = (score, b, ns[1], rd)
        if best:
            matches[a["idx"]] = {"cid": best[1]["cid"], "how": best[2], "region": best[3]}
        elif best_loose:
            loose[a["idx"]] = {"cid": best_loose[1]["cid"], "how": best_loose[2], "region": best_loose[3]}

    by_cid = {t["cid"]: t for t in tour}
    matched_cids = Counter(m["cid"] for m in matches.values())
    std_only = [a for a in std if a["idx"] not in matches]
    tour_only = [t for t in tour if t["cid"] not in matched_cids]

    def nearest(row, pool, key_self="rcore"):
        best = None
        for b in pool:
            r = SequenceMatcher(None, row[key_self], b[key_self]).ratio()
            if best is None or r > best[0]:
                best = (r, b)
        return best

    def spaced(rows, n=15):
        rows = sorted(rows, key=lambda r: (r["start"] or "", r["name"]))
        if len(rows) <= n:
            return rows
        step = len(rows) / n
        return [rows[int(i * step)] for i in range(n)]

    std_only_ex = []
    for a in spaced(std_only):
        r, b = nearest(a, tour)
        std_only_ex.append({"fstvlNm": a["name"], "dates": f"{a['start']}~{a['end']}", "addr": a["addr"], "insttNm": a["instt"],
                            "closest_tourapi_title": b["name"], "closest_dates": f"{b['start']}~{b['end']}", "closest_ratio": round(r, 2),
                            "date_overlap": overlap(a, b), "region": region(a, b)[2],
                            "loose_match": by_cid[loose[a["idx"]]["cid"]]["name"] if a["idx"] in loose else None})
    tour_only_ex = []
    for t in spaced(tour_only):
        r, a = nearest(t, std)
        tour_only_ex.append({"title": t["name"], "dates": f"{t['start']}~{t['end']}", "addr1": t["addr"], "contentid": t["cid"],
                             "closest_standard_name": a["name"], "closest_dates": f"{a['start']}~{a['end']}", "closest_ratio": round(r, 2),
                             "date_overlap": overlap(a, t), "region": region(a, t)[2]})

    # one-to-one view (each TourAPI row consumed once, first by best tier)
    one_to_one = len(matched_cids)
    how = Counter(m["how"].rstrip("0123456789.") for m in matches.values())
    matched_std_dupes = sum(c - 1 for c in matched_cids.values() if c > 1)
    tour_with_fi = sum(1 for t in tour if t["firstimage"])
    matched_with_fi = sum(1 for cid in matched_cids if by_cid[cid]["firstimage"])
    std_matched_with_fi = sum(1 for m in matches.values() if by_cid[m["cid"]]["firstimage"])
    std_hp = sum(1 for a in std if a["homepage"])
    std_hp_matched = sum(1 for a in std if a["homepage"] and a["idx"] in matches)
    std_hp_unmatched = sum(1 for a in std if a["homepage"] and a["idx"] not in matches)
    instt = Counter(a["instt"] for a in std)
    kto_rows = [a for a in std if a["instt"] == "한국관광공사"]
    kto_matched = sum(1 for a in kto_rows if a["idx"] in matches)

    near = []
    for a in std_only:
        for b in tour:
            d = km(a["ll"], b["ll"])
            if d is not None and d <= 2 and overlap(a, b):
                near.append({"fstvlNm": a["name"], "std_dates": f"{a['start']}~{a['end']}", "tour_title": b["name"], "tour_dates": f"{b['start']}~{b['end']}", "km": round(d, 2), "tour_cid": b["cid"]})
    (OUT / "match.near_unmatched.json").write_text(json.dumps(near, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {
        "standard_only_rows_with_a_date_overlapping_tourapi_row_within_2km": len({n["fstvlNm"] + n["std_dates"] for n in near}),
        "standard_2026_rows": len(std),
        "standard_2026_distinct_name_start": len({(a["key"], a["start"]) for a in std}),
        "tourapi_rows_returned": len(tour_all),
        "tourapi_2026_start_rows": len(tour),
        "matched_standard_rows": len(matches),
        "matched_distinct_tourapi_contentids": one_to_one,
        "standard_rows_sharing_a_tourapi_row_with_another_standard_row": matched_std_dupes,
        "standard_only_rows": len(std_only),
        "tourapi_only_rows": len(tour_only),
        "match_tier_counts": dict(how),
        "standard_only_with_name_match_within_31d_tolerance": len(loose),
        "standard_rows_with_homepageUrl": std_hp,
        "standard_rows_with_homepageUrl_matched": std_hp_matched,
        "standard_rows_with_homepageUrl_unmatched": std_hp_unmatched,
        "standard_rows_from_insttNm_한국관광공사": len(kto_rows),
        "standard_rows_from_한국관광공사_matched": kto_matched,
        "top_insttNm": instt.most_common(10),
        "tourapi_2026_with_firstimage": tour_with_fi,
        "matched_tourapi_with_firstimage": matched_with_fi,
        "matched_standard_rows_whose_tourapi_row_has_firstimage": std_matched_with_fi,
        "standard_only_examples": std_only_ex,
        "tourapi_only_examples": tour_only_ex,
    }
    pairs = [{"std_idx": i, "fstvlNm": next(a["name"] for a in std if a["idx"] == i), **m, "tour_title": by_cid[m["cid"]]["name"],
              "firstimage": by_cid[m["cid"]]["firstimage"]} for i, m in matches.items()]
    (OUT / "match.pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "match.loose.json").write_text(json.dumps([{"std_idx": i, "fstvlNm": next(a["name"] for a in std if a["idx"] == i), **m, "tour_title": by_cid[m["cid"]]["name"]} for i, m in loose.items()], ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "match.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("examples")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
