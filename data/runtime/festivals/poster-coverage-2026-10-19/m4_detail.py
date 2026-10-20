"""Step 4: at most 100 detailImage2 calls on a systematic sample of matched TourAPI festivals not in the survey.
retries=0 and stop on the first error (429 / quota / any API error) to protect the shared daily quota."""
import dataclasses
import json
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from app.festival_data.settings import Settings, CollectionError  # noqa: E402
from app.festival_data.client import ApiClient  # noqa: E402
from app.festival_data.normalize import parse_items  # noqa: E402

MAX_CALLS = 100
SURVEY = REPO / "data/runtime/festivals/poster-survey-2026-10-14/survey.sample.json"


def hint(name):
    name = name or ""
    return "포스터" in name or "poster" in name.casefold()


def main():
    pairs = json.loads((OUT / "match.pairs.json").read_text(encoding="utf-8"))
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))
    surveyed = {r["content_id"] for r in survey["records"]}
    pool = sorted({p["cid"] for p in pairs} - surveyed, key=int)
    n = min(MAX_CALLS, len(pool))
    sample = [pool[(i * len(pool)) // n] for i in range(n)]
    assert len(set(sample)) == len(sample)
    tour_items = {str(it["contentid"]): it for p in json.loads((OUT / "tourapi.raw.json").read_text(encoding="utf-8"))["pages"] for it in parse_items(p)}
    out_path = OUT / "detail.sample.json"
    done = {}
    prior_calls, prior_stops = 0, []
    if out_path.exists():
        prev = json.loads(out_path.read_text(encoding="utf-8"))
        done = {r["content_id"]: r for r in prev["records"]}
        prior_calls = prev.get("cumulative_api_calls", prev.get("new_api_calls", 0))
        prior_stops = prev.get("stops", []) + ([prev["stopped"]] if prev.get("stopped") else [])
        if any("429" in (x.get("error") or "") or "22" in (x.get("error") or "") for x in prior_stops):
            print("previous run hit a quota/429 error; not resuming")
            return
    settings = dataclasses.replace(Settings.load(REPO), retries=0, timeout=60)
    client = ApiClient(settings)
    calls, stopped = 0, None
    records = []
    for cid in sample:
        if cid in done:
            records.append(done[cid])
            continue
        if calls + prior_calls >= MAX_CALLS:
            break
        try:
            calls += 1
            pages = client.tourapi_images(cid)
        except CollectionError as error:
            stopped = {"content_id": cid, "error": str(error)}
            break
        items = [it for p in pages for it in parse_items(p)]
        it = tour_items.get(cid, {})
        records.append({
            "content_id": cid, "title": it.get("title"), "addr1": it.get("addr1"),
            "eventstartdate": it.get("eventstartdate"), "eventenddate": it.get("eventenddate"),
            "firstimage": it.get("firstimage"),
            "detail_images": [{"imgname": d.get("imgname"), "serialnum": d.get("serialnum"), "originimgurl": d.get("originimgurl"),
                               "cpyrhtDivCd": d.get("cpyrhtDivCd"), "poster_name_hint": hint(d.get("imgname"))} for d in items],
        })
        time.sleep(0.2)
    result = {"sample_rule": f"matched contentids not in survey, sorted numerically, index floor(i*{len(pool)}/{n}) for i<{n}",
              "pool": len(pool), "planned": n, "new_api_calls": calls, "cumulative_api_calls": calls + prior_calls,
              "stops": prior_stops, "stopped": stopped, "records": records}
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with_hint = sum(1 for r in records if any(d["poster_name_hint"] for d in r["detail_images"]))
    no_detail = sum(1 for r in records if not r["detail_images"])
    print(json.dumps({"pool": len(pool), "planned": n, "new_api_calls": calls, "cumulative_api_calls": calls + prior_calls, "records": len(records), "with_poster_name": with_hint,
                      "no_detail_images": no_detail, "stopped": stopped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
