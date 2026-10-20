"""Step 1: fetch full standard-data list and TourAPI 2026 festival list (read-only GETs).
Never prints keys or URLs containing keys; ApiClient.get() redacts payloads."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from app.festival_data.settings import Settings, CollectionError  # noqa: E402
from app.festival_data.client import ApiClient  # noqa: E402
from app.festival_data.normalize import parse_items  # noqa: E402


def main():
    settings = Settings.load(REPO)
    client = ApiClient(settings)
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    calls = 0
    try:
        std_pages = client.festivals()
        calls += len(std_pages)
    except CollectionError as e:
        print("standard fetch error:", e)
        return 1
    std_items = [it for p in std_pages for it in parse_items(p)]
    (OUT / "standard.raw.json").write_text(json.dumps({"retrieved_at": stamp, "pages": std_pages}, ensure_ascii=False), encoding="utf-8")
    try:
        tour_pages = client.tourapi("2026-01-01", "2026-12-31")
        calls += len(tour_pages)
    except CollectionError as e:
        print("tourapi fetch error:", e)
        return 1
    tour_items = [it for p in tour_pages for it in parse_items(p)]
    (OUT / "tourapi.raw.json").write_text(json.dumps({"retrieved_at": stamp, "filters": {"eventStartDate": "20260101", "eventEndDate": "20261231"}, "pages": tour_pages}, ensure_ascii=False), encoding="utf-8")

    def total(pages):
        try:
            return pages[0]["response"]["body"].get("totalCount")
        except Exception:
            return None

    std2026 = [it for it in std_items if str(it.get("fstvlStartDate") or "").startswith("2026")]
    tour2026start = [it for it in tour_items if str(it.get("eventstartdate") or "").startswith("2026")]
    fields = sorted({k for it in std_items for k in it})
    tour_fields = sorted({k for it in tour_items for k in it})
    summary = {
        "retrieved_at": stamp,
        "api_calls": calls,
        "standard": {"pages": len(std_pages), "totalCount": total(std_pages), "items": len(std_items),
                      "start_2026": len(std2026), "fields": fields,
                      "start_year_histogram": {}},
        "tourapi": {"pages": len(tour_pages), "totalCount": total(tour_pages), "items": len(tour_items),
                     "start_2026": len(tour2026start), "fields": tour_fields,
                     "with_firstimage": sum(1 for it in tour_items if (it.get("firstimage") or "").strip()),
                     "with_firstimage_start_2026": sum(1 for it in tour2026start if (it.get("firstimage") or "").strip())},
    }
    hist = {}
    for it in std_items:
        y = str(it.get("fstvlStartDate") or "")[:4] or "none"
        hist[y] = hist.get(y, 0) + 1
    summary["standard"]["start_year_histogram"] = dict(sorted(hist.items()))
    (OUT / "fetch.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
