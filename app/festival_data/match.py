"""Link 전국문화축제표준데이터 rows to TourAPI searchFestival2 rows: name + strict date overlap + region.

Ported from the 2026-10-19 coverage script. A name alone never links: same-named festivals in other cities
(여주·이천 도자기축제) and same-day events with generic names (동두천·강북구 어린이날) must agree on region too.
"""
from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher
import math
import re
import unicodedata

from .normalize import iso_date, text, title_key

SUFFIXES = ["페스티벌", "페스티발", "festival", "fiesta", "페스타", "festa", "대축제", "축제", "축전", "한마당", "fest", "문화제", "대잔치", "잔치"]
SIDO = {
    "서울": ["서울특별시", "서울시", "서울"], "부산": ["부산광역시", "부산시", "부산"], "대구": ["대구광역시", "대구시", "대구"],
    "인천": ["인천광역시", "인천시", "인천"], "광주": ["광주광역시", "광주특별시", "광주시", "광주"], "대전": ["대전광역시", "대전시", "대전"],
    "울산": ["울산광역시", "울산시", "울산"], "세종": ["세종특별자치시", "세종시", "세종"], "경기": ["경기도", "경기"],
    "강원": ["강원특별자치도", "강원도", "강원"], "충북": ["충청북도", "충북"], "충남": ["충청남도", "충남"],
    "전북": ["전북특별자치도", "전라북도", "전북"], "전남": ["전라남도", "전남", "전남광주통합특별시"],
    "경북": ["경상북도", "경북"], "경남": ["경상남도", "경남"], "제주": ["제주특별자치도", "제주도", "제주"],
}
SIDO_TOKENS = sorted({token for names in SIDO.values() for token in names}, key=len, reverse=True)
# Same-name rows whose dates miss each other by at most this many days are reported as date conflicts.
CONFLICT_DAYS = 31


def nfkc(value):
    return unicodedata.normalize("NFKC", text(value) or "").casefold()


def base_key(value, drop_paren=True):
    value = nfkc(value)
    if drop_paren:
        value = re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]*>|〈[^〉]*〉|《[^》]*》|「[^」]*」|『[^』]*』", " ", value)
    value = re.sub(r"제\s*\d+\s*회", " ", value)
    value = re.sub(r"\d+\s*(?:st|nd|rd|th)\b", " ", value)
    value = re.sub(r"(?:19|20)\d{2}\s*(?:년도|년)?", " ", value)
    value = re.sub(r"^\s*'?\d{2}\s+", " ", value)
    return re.sub(r"[^0-9a-z가-힣]", "", value)


def core_key(key):
    changed = True
    while changed and key:
        changed = False
        for suffix in SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix):
                key, changed = key[:-len(suffix)], True
                break
    return key


def region_tokens(*addresses):
    tokens = set()
    for address in addresses:
        for part in nfkc(address).split()[:3]:
            part = re.sub(r"[^가-힣]", "", part)
            if re.fullmatch(r"[가-힣]{1,6}(?:시|군|구)", part) and part not in SIDO_TOKENS:
                tokens.add(part)
                if len(part) >= 3:
                    tokens.add(part[:-1])
    return tokens


def sido_of(address):
    address = nfkc(address).strip()
    for key, names in SIDO.items():
        if any(address.startswith(name) for name in sorted(names, key=len, reverse=True)):
            return key
    return None


def strip_region(key, tokens):
    for token in sorted(tokens, key=len, reverse=True):
        if len(token) >= 2 and key.startswith(token) and len(key) > len(token) + 1:
            key = key[len(token):]
    for token in SIDO_TOKENS:
        if len(token) >= 2 and key.startswith(token) and len(key) > len(token) + 1:
            return key[len(token):]
    return key


def safe_date(value):
    try:
        return iso_date(value)
    except ValueError:
        return None


def coordinate(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number != 0 else None


def km(a, b):
    if None in (*a, *b):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def prepare(name, start, end, addresses, location, **extra):
    key = base_key(name)
    tokens = region_tokens(*addresses)
    start, end = safe_date(start), safe_date(end) or safe_date(start)
    return {
        "name": text(name) or "", "title_key": title_key(name), "start": start, "end": end,
        "first": date.fromisoformat(start) if start else None, "last": date.fromisoformat(end or start) if start else None,
        "key": key, "key_paren": base_key(name, drop_paren=False), "core": core_key(key), "rcore": core_key(strip_region(key, tokens)),
        "tokens": tokens, "sido": sido_of(next((address for address in addresses if text(address)), "")), "location": location, **extra,
    }


def prepare_standard(item, index):
    addresses = [item.get("rdnmadr") or "", item.get("lnmadr") or ""]
    return prepare(item.get("fstvlNm"), item.get("fstvlStartDate"), item.get("fstvlEndDate"), addresses + [item.get("insttNm") or ""],
                   (coordinate(item.get("latitude")), coordinate(item.get("longitude"))), index=index)


def prepare_tour(item):
    return prepare(item.get("title"), item.get("eventstartdate"), item.get("eventenddate"), [item.get("addr1") or ""],
                   (coordinate(item.get("mapy")), coordinate(item.get("mapx"))), content_id=text(item.get("contentid")))


def overlap(a, b, tolerance=0):
    if a["first"] is None or b["first"] is None:
        return False
    slack = timedelta(days=tolerance)
    return a["first"] <= b["last"] + slack and b["first"] <= a["last"] + slack


def region(a, b):
    """Return (agree, conflict, detail): coordinates first, then 시군구 names, then 시도."""
    distance = km(a["location"], b["location"])
    shared = bool(a["tokens"] & b["tokens"])
    if distance is not None:
        if distance <= 10:
            return True, False, f"{distance:.1f}km"
        if shared and distance <= 30:
            return True, False, f"{distance:.1f}km+sigungu"
        return False, distance > 60, f"{distance:.1f}km"
    if shared:
        return True, False, "sigungu"
    if a["sido"] and b["sido"] and a["sido"] != b["sido"]:
        return False, True, "sido-differs"
    return False, False, "unknown"


def contained(left, right, minimum):
    short, long = sorted([left, right], key=len)
    return len(short) >= minimum and short in long


def similarity(left, right):
    """SequenceMatcher ratio, skipped when its cheap upper bounds already fall below the lowest threshold (0.6)."""
    matcher = SequenceMatcher(None, left, right)
    return matcher.ratio() if matcher.real_quick_ratio() >= 0.6 and matcher.quick_ratio() >= 0.6 else 0.0


def name_score(a, b):
    if a["key"] and (a["key"] == b["key"] or a["key_paren"] == b["key_paren"] or a["title_key"] == b["title_key"]):
        return 4, "exact"
    if a["core"] and a["core"] == b["core"] and len(a["core"]) >= 2:
        return 3, "core"
    if a["rcore"] and a["rcore"] == b["rcore"] and len(a["rcore"]) >= 2:
        return 3, "core-noregion"
    if contained(a["rcore"], b["rcore"], 3) or contained(a["core"], b["core"], 3):
        return 2, "contains"
    if contained(core_key(a["key_paren"]), core_key(b["key_paren"]), 4):
        return 2, "contains-paren"
    ratio = max(similarity(a["rcore"], b["rcore"]), similarity(a["core"], b["core"]))
    if ratio >= 0.75:
        return 1, "similar"
    distance = km(a["location"], b["location"])
    if ratio >= 0.6 and a["start"] == b["start"] and a["end"] == b["end"] and distance is not None and distance <= 2:
        return 1, "samedates-similar"
    return 0, "different"


def match_festivals(standard_items, tour_items):
    """Return links keyed by the index in standard_items.

    pairs: strict links (name tier, overlapping dates, agreeing region; exact names only need no region conflict).
    date_conflicts: same-name rows within 31 days whose dates do not overlap. They are never linked automatically.
    """
    standard = [prepare_standard(item, index) for index, item in enumerate(standard_items)]
    tour = [prepare_tour(item) for item in tour_items if text(item.get("contentid"))]
    pairs, conflicts = {}, {}
    for a in standard:
        best = loose = None
        for b in tour:
            if not overlap(a, b, CONFLICT_DAYS):
                continue
            tier, how = name_score(a, b)
            if tier == 0:
                continue
            agree, conflict, detail = region(a, b)
            distance = km(a["location"], b["location"])
            if overlap(a, b) and (not conflict if tier == 4 else agree):
                score = (tier, int(agree), -(distance if distance is not None else 999))
                if best is None or score > best[0]:
                    best = (score, b, how, detail)
            elif tier >= 3 and (agree or tier == 4 and not conflict):
                score = (tier, int(agree))
                if loose is None or score > loose[0]:
                    loose = (score, b, how, detail)
        if best:
            pairs[a["index"]] = {"content_id": best[1]["content_id"], "how": best[2], "region": best[3]}
        elif loose:
            conflicts[a["index"]] = {"content_id": loose[1]["content_id"], "how": loose[2], "region": loose[3]}
    return {"pairs": pairs, "date_conflicts": conflicts}
