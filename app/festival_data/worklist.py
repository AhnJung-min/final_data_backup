"""Plan poster work from a collect run: one unit per festival, tiers T1/T2/T4, and worklist.csv.

T1: exactly one detail image named 포스터/poster without another 20xx year (pre-accepted in review.html).
T2: detail images exist but need a human pick (no name hint, several poster names, or a same-name date conflict).
T4: no TourAPI festival or no detail image; a person searches the links in worklist.csv.
pending: linked to TourAPI but detailImage2 not collected yet (run collect --resume, then regenerate).
"""
from __future__ import annotations

import csv
from datetime import date
from hashlib import sha1
import io
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from .match import base_key, match_festivals, safe_date, sido_of
from .normalize import parse_items, text, title_key
from .schema import read_json
from .settings import CollectionError

KFES_URL = "https://korean.visitkorea.or.kr/kfes/detail/fstvlDetail.do?cmsCntntsId={}"
SEARCH_URL = "https://search.naver.com/search.naver?where=image&query={}"
WEEKDAYS = "월화수목금토일"
YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
POSTER_RATIO = 1.41  # A-series height/width; 1.5 is portrait photos, ~1.25 is 4:5 card news.
KTO_CODE = "B551011"  # 한국관광공사 rows duplicate the local government's row.
TIER_ORDER = {"T4": 0, "T2": 1, "pending": 2, "T1": 3}
COLUMNS = ["wl_id", "tier", "reason", "owner", "sido", "title", "expected_dates", "venue", "institution", "content_id",
           "kfes_url", "homepage_url", "search_url", "candidates", "status", "image_url", "page_url", "license", "license_evidence", "note"]
HUMAN_COLUMNS = ["owner", "status", "image_url", "page_url", "license", "license_evidence", "note"]
FORMULA_PREFIXES = ("=", "+", "-", "@")


def poster_hint(name, years=()):
    """T1 signal: the image name says poster and names no 20xx year other than the festival's own."""
    name = text(name)
    if not name or not ("포스터" in name or "poster" in name.casefold()):
        return False
    return all(found in years for found in YEAR.findall(name))


def festival_years(item):
    values = [text(item.get("eventstartdate")), text(item.get("eventenddate"))]
    return {value[:4] for value in values if value and value[:4].isdigit()}


def with_weekday(value):
    return f"{value}({WEEKDAYS[date.fromisoformat(value).weekday()]})" if value else ""


def expected_dates(start, end):
    if not start:
        return ""
    return with_weekday(start) if not end or end == start else f"{with_weekday(start)} ~ {with_weekday(end)}"


def search_url(name, start):
    # Notes such as "(날짜미정)" and a printed year would narrow the search to the wrong edition.
    name = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text(name) or "")
    name = re.sub(r"^\s*(?:19|20)\d{2}(?:년도|년)?\s*|\s*(?:19|20)\d{2}(?:년도|년)?\s*$", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return SEARCH_URL.format(quote(" ".join(part for part in [(start or "")[:4], name, "포스터"] if part)))


def http_url(value):
    value = text(value)
    return value if value and value.lower().startswith(("https://", "http://")) else None


def load_run(run_dir):
    run_dir = Path(run_dir)
    try:
        standard = read_json(run_dir / "data_go_kr.raw.json")
        tour = read_json(run_dir / "tourapi.raw.json")
    except FileNotFoundError:
        raise CollectionError("collect --tourapi로 만든 실행 폴더가 필요합니다 (data_go_kr.raw.json, tourapi.raw.json).") from None
    details = {}
    combined = run_dir / "tourapi.detail-images.raw.json"
    if combined.exists():
        details.update((str(record["content_id"]), record) for record in read_json(combined).get("records", []))
    cache = run_dir / "cache" / "tourapi-detail"
    if cache.is_dir():
        for path in sorted(cache.glob("*.json")):
            record = read_json(path)
            details[str(record["content_id"])] = record
    downloaded = {}
    images = run_dir / "tourapi.images.json"
    if images.exists():
        downloaded = {entry["source_url"]: entry["image"] for entry in read_json(images)["images"] if entry.get("image") and entry.get("source_url")}
    return standard, tour, details, downloaded


def candidate(item, endpoint, years, downloaded):
    url = http_url(item.get("originimgurl" if endpoint == "detailImage2" else "firstimage"))
    if url is None:
        return None
    name = text(item.get("imgname")) if endpoint == "detailImage2" else None
    image = downloaded.get(url) or {}
    return {
        "url": url, "thumb": http_url(item.get("smallimageurl" if endpoint == "detailImage2" else "firstimage2")),
        "name": name, "serial": text(item.get("serialnum")) if endpoint == "detailImage2" else None,
        "copyright": text(item.get("cpyrhtDivCd")), "endpoint": endpoint,
        "hint": bool(name and ("포스터" in name or "poster" in name.casefold())), "auto": poster_hint(name, years),
        "width": image.get("width"), "height": image.get("height"), "file": image.get("file"), "raw": item,
    }


def rank(candidates):
    """Name hints first, then height/width closest to the A-series ratio; unknown sizes keep API order."""
    def key(pair):
        position, entry = pair
        ratio = entry["height"] / entry["width"] if entry["width"] and entry["height"] else None
        return (not entry["auto"], not entry["hint"], abs(ratio - POSTER_RATIO) if ratio else 9,
                entry["endpoint"] != "detailImage2", position)
    return [entry for _, entry in sorted(enumerate(candidates), key=key)]


def make_unit(rows, tour_item, content_id, conflict, details, downloaded):
    if rows:
        first = rows[0]
        name, start = text(first.get("fstvlNm")), safe_date(first.get("fstvlStartDate"))
        end = safe_date(first.get("fstvlEndDate")) or start
        identity = [title_key(name), start or "", text(first.get("insttCode")) or ""]
        venue, address = text(first.get("opar")), text(first.get("rdnmadr")) or text(first.get("lnmadr"))
        institutions = list(dict.fromkeys(text(row.get("insttNm")) for row in rows if text(row.get("insttNm"))))
        homepage = next((http_url(row.get("homepageUrl")) for row in rows if http_url(row.get("homepageUrl"))), None)
    else:
        name, start = text(tour_item.get("title")), safe_date(tour_item.get("eventstartdate"))
        end = safe_date(tour_item.get("eventenddate")) or start
        identity = [title_key(name), start or "", "tourapi:" + content_id]
        venue, address, institutions, homepage = text(tour_item.get("addr2")), text(tour_item.get("addr1")), [], None
    years = festival_years(tour_item or {}) | {value[:4] for value in (start, end) if value}
    detail = details.get(content_id) if content_id else None
    found = []
    if detail is not None:
        found = [candidate(item, "detailImage2", years, downloaded) for item in parse_items(detail)]
    if tour_item:
        found.append(candidate(tour_item, "searchFestival2", years, downloaded))
    seen, candidates = set(), []
    for entry in found:
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            candidates.append(entry)
    candidates = rank(candidates)
    detail_images = [entry for entry in candidates if entry["endpoint"] == "detailImage2"]
    auto = [entry for entry in detail_images if entry["auto"]]
    if content_id is None:
        tier, reason = "T4", "no_tourapi_match"
    elif detail is None:
        tier, reason = "pending", "date_conflict" if conflict else "detail_not_collected"
    elif conflict:
        tier, reason = "T2", "date_conflict"
    elif len(auto) == 1:
        tier, reason = "T1", "poster_name"
    elif auto:
        tier, reason = "T2", "multiple_poster_names"
    elif detail_images:
        tier, reason = "T2", "poster_name_other_year" if any(entry["hint"] for entry in detail_images) else "no_poster_name"
    else:
        tier, reason = "T4", "no_detail_images"
    return {
        "wl_id": "wl-" + sha1("|".join(identity).encode("utf-8")).hexdigest()[:10],
        "tier": tier, "reason": reason, "title": name or "", "start_date": start, "end_date": end,
        "expected_dates": expected_dates(start, end), "venue": venue, "address": address,
        "sido": sido_of(address or "") or sido_of((tour_item or {}).get("addr1") or "") or "",
        "institution": ", ".join(institutions), "homepage_url": homepage, "content_id": content_id,
        "tour_title": text((tour_item or {}).get("title")),
        "tour_dates": expected_dates(safe_date((tour_item or {}).get("eventstartdate")), safe_date((tour_item or {}).get("eventenddate"))),
        "kfes_url": KFES_URL.format(content_id) if content_id else None, "search_url": search_url(name, start),
        "standard_rows": len(rows), "candidates": candidates,
        "selected": candidates.index(auto[0]) if tier == "T1" else None,
        "retrieved_at": detail.get("retrieved_at") if detail else None,
    }


def build_units(run_dir, year=None):
    """Merge 표준데이터 rows that share a TourAPI festival (or name+start+시도) into one unit each."""
    standard_snapshot, tour_snapshot, details, downloaded = load_run(run_dir)
    filters = standard_snapshot.get("filters") or {}
    year = year or filters.get("year")
    prefix = str(year) if year else ""
    # collect --name keeps the whole TourAPI list on disk; apply the same name filter it used.
    named = title_key(filters["name"]) if filters.get("name") else None
    standard = [item for item in parse_items(standard_snapshot) if str(item.get("fstvlStartDate") or "").startswith(prefix)]
    tour = [item for item in parse_items(tour_snapshot) if str(item.get("eventstartdate") or "").startswith(prefix) and text(item.get("contentid"))
            and (named is None or title_key(item.get("title")) == named)]
    by_id = {text(item.get("contentid")): item for item in tour}
    links = match_festivals(standard, tour)
    groups = {}
    for index, item in enumerate(standard):
        if index in links["pairs"]:
            key = ("tour", links["pairs"][index]["content_id"])
        else:
            address = text(item.get("rdnmadr")) or text(item.get("lnmadr")) or ""
            key = ("standard", title_key(item.get("fstvlNm")), safe_date(item.get("fstvlStartDate")) or "", sido_of(address) or "")
        groups.setdefault(key, []).append(index)
    claimed = {link["content_id"] for link in links["date_conflicts"].values()}
    units = []
    for key, indexes in groups.items():
        # Prefer the local government's row over 한국관광공사 duplicates for the shown name and wl_id.
        indexes = sorted(indexes, key=lambda index: (text(standard[index].get("insttCode")) == KTO_CODE, index))
        rows = [standard[index] for index in indexes]
        conflict = next((links["date_conflicts"][index] for index in indexes if index in links["date_conflicts"]), None)
        content_id = key[1] if key[0] == "tour" else conflict["content_id"] if conflict else None
        units.append(make_unit(rows, by_id.get(content_id), content_id, conflict is not None, details, downloaded))
    for content_id, item in by_id.items():
        if ("tour", content_id) not in groups and content_id not in claimed:
            units.append(make_unit([], item, content_id, False, details, downloaded))
    seen = {}
    for unit in units:
        count = seen[unit["wl_id"]] = seen.get(unit["wl_id"], 0) + 1
        if count > 1:
            unit["wl_id"] += f"-{count}"
        unit["list_retrieved_at"] = tour_snapshot.get("retrieved_at")
    return units


def assign_owners(units, owners):
    """Give whole 시도 to owners, largest open workload (everything but T1) first, so each person's rows share a region."""
    load = {}
    for unit in units:
        load[unit["sido"] or "미상"] = load.get(unit["sido"] or "미상", 0) + (unit["tier"] != "T1")
    totals, assignment = {owner: 0 for owner in owners}, {}
    for sido, count in sorted(load.items(), key=lambda pair: (-pair[1], pair[0])):
        owner = min(owners, key=lambda name: (totals[name], owners.index(name)))
        assignment[sido] = owner
        totals[owner] += count
    return assignment


def guard(value):
    """Keep public-data text from running as an Excel formula."""
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def unguard(value):
    value = (value or "").strip()
    return value[1:] if value.startswith("'") and value[1:].startswith(FORMULA_PREFIXES) else value


def read_worklist(path):
    raw = Path(path).read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp949")  # Excel's plain "CSV" save on Korean Windows.
    rows = list(csv.DictReader(io.StringIO(content)))
    if rows and "wl_id" not in rows[0]:
        raise CollectionError("worklist.csv에 wl_id 열이 없습니다.")
    return [{key: unguard(value) for key, value in row.items() if key} for row in rows]


def worklist_rows(units, owners=None, priority=None, existing=None):
    assignment = assign_owners(units, owners) if owners else {}
    priority = {base_key(name) for name in priority or [] if base_key(name)}
    existing = existing or {}

    def order(unit):
        group = 0 if base_key(unit["title"]) in priority or base_key(unit["tour_title"]) in priority else 1 if unit["homepage_url"] else 2
        return TIER_ORDER[unit["tier"]], group, unit["start_date"] or "9999", unit["title"]

    rows = []
    for unit in sorted(units, key=order):
        row = {
            "wl_id": unit["wl_id"], "tier": unit["tier"], "reason": unit["reason"],
            "owner": assignment.get(unit["sido"] or "미상", ""), "sido": unit["sido"], "title": unit["title"],
            "expected_dates": unit["expected_dates"], "venue": unit["venue"], "institution": unit["institution"],
            "content_id": unit["content_id"], "kfes_url": unit["kfes_url"], "homepage_url": unit["homepage_url"],
            "search_url": unit["search_url"], "candidates": len(unit["candidates"]),
            "status": "", "image_url": "", "page_url": "", "license": "unknown", "license_evidence": "", "note": "",
        }
        previous = existing.get(unit["wl_id"], {})
        row.update({column: previous[column] for column in HUMAN_COLUMNS if previous.get(column)})
        rows.append(row)
    return rows


def write_worklist(path, rows):
    """UTF-8 BOM so Excel shows Korean; replaced atomically so an open copy is never half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows({column: guard(row[column]) for column in COLUMNS} for row in rows)
        os.replace(temporary, path)
    except PermissionError:
        raise CollectionError(f"{path.name}을 쓸 수 없습니다. Excel에서 열려 있으면 닫고 다시 실행하세요.") from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_worklist(run_dir, output, year=None, owners=None, priority_file=None):
    units = build_units(run_dir, year)
    priority = []
    if priority_file:
        priority = [line.strip() for line in Path(priority_file).read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.startswith("#")]
    existing = {row["wl_id"]: row for row in read_worklist(output)} if Path(output).exists() else {}
    rows = worklist_rows(units, owners, priority, existing)
    write_worklist(output, rows)
    counts = {}
    for row in rows:
        counts[row["tier"]] = counts.get(row["tier"], 0) + 1
    return {"output": str(output), "rows": len(rows), "tiers": counts, "kept_human_columns": sum(1 for row in rows if row["wl_id"] in existing)}
