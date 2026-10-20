"""Step 4/5 statistics (offline): poster-name share with Wilson 95% CI; portrait-rule observations."""
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
SURVEY = REPO / "data/runtime/festivals/poster-survey-2026-10-14/survey.sample.json"


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "p": round(p, 3), "ci95": [round(c - h, 3), round(c + h, 3)]}


def has_hint(r):
    return any(d.get("poster_name_hint") for d in r["detail_images"])


def main():
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))["records"]
    new = json.loads((OUT / "detail.sample.json").read_text(encoding="utf-8"))
    pairs = json.loads((OUT / "match.pairs.json").read_text(encoding="utf-8"))
    match_summary = json.loads((OUT / "match.summary.json").read_text(encoding="utf-8"))
    matched = {p["cid"] for p in pairs}
    s_m = [r for r in survey if r["content_id"] in matched]
    s_u = [r for r in survey if r["content_id"] not in matched]
    nr = new["records"]
    k_sm, k_su, k_new = sum(map(has_hint, s_m)), sum(map(has_hint, s_u)), sum(map(has_hint, nr))
    matched_pool = match_summary["matched_distinct_tourapi_contentids"]
    tour_total = match_summary["tourapi_2026_start_rows"]
    p_m = (k_sm + k_new) / (len(s_m) + len(nr))
    p_u = k_su / len(s_u)
    # stratified estimate for the whole TourAPI 2026 list (matched vs unmatched strata)
    w_m = matched_pool / tour_total
    est = w_m * p_m + (1 - w_m) * p_u
    var = (w_m ** 2) * p_m * (1 - p_m) / (len(s_m) + len(nr)) + ((1 - w_m) ** 2) * p_u * (1 - p_u) / len(s_u)
    strat = {"p": round(est, 3), "ci95_normal": [round(est - 1.96 * math.sqrt(var), 3), round(est + 1.96 * math.sqrt(var), 3)],
             "weights": {"matched": matched_pool, "unmatched": tour_total - matched_pool}}

    images = json.loads((OUT / "images.manifest.json").read_text(encoding="utf-8"))
    # visual labels from manual inspection (Read tool) of files in img/; key = file prefix
    labels = {
        "4dc2bfbb7ccb": "photo", "6a54fcf291c2": "photo", "4bd77a5fb102": "poster", "7973ecf98f84": "photo",
        "5c060b255262": "info-card", "ab3cab6b201f": "photo", "4c0b478787a8": "photo", "06f2c038105e": "photo",
        "487f759f70ac": "info-card", "c444d2ad82f9": "poster", "70a51fcd9b3a": "info-card", "25c5b909bfef": "info-card",
        "30003980a6a0": "info-card", "1a69c22a5026": "info-card", "60f54de965ae": "info-card", "1f379377dfe1": "info-card",
        "bc13536c1878": "info-card", "e95d47c901a5": "info-card",
        "8d1476b980a5": "photo", "81a611b3f2d4": "photo", "6f5ea358eded": "photo", "b08c3fb66f66": "photo",
        "13cf0a38e5d6": "photo", "d5e3512ada2d": "photo", "c5ee33c8ac7f": "photo", "88b9163d3073": "photo",
        "38dffbefdae4": "photo", "417c404243a8": "photo", "ea38265f8214": "photo", "d35a8c57b7d5": "photo", "b0159a254b68": "photo",
    }
    poster_checked = ["330af1d271ea", "4bb14ef6cfac", "5b84437a2d07", "9343ba92765f", "790fe75bb432", "6caa3b9eb386"]
    rows = images["downloaded"]
    for x in rows:
        x["visual_label"] = labels.get(x["file"][:12])
    portrait = [x for x in rows if x["ratio"] >= 1.25]
    near45 = [x for x in rows if 1.2 <= x["ratio"] < 1.25]
    landscape = [x for x in rows if x["ratio"] < 1.0]
    posters_found = [x for x in rows if x.get("visual_label") == "poster"]
    nohint_fests = {x["content_id"] for x in rows}
    fests_recovered = {x["content_id"] for x in portrait if x.get("visual_label") == "poster"}
    fests_flagged = {x["content_id"] for x in portrait}
    sp = images["survey_posters"]
    result = {
        "poster_name_share": {
            "survey_all_64": wilson(sum(map(has_hint, survey)), len(survey)),
            "survey_matched_subset": wilson(k_sm, len(s_m)),
            "survey_unmatched_subset": wilson(k_su, len(s_u)),
            "new_matched_sample": wilson(k_new, len(nr)),
            "matched_pooled_survey_plus_new": wilson(k_sm + k_new, len(s_m) + len(nr)),
            "all_pooled_unweighted": wilson(k_sm + k_su + k_new, len(survey) + len(nr)),
            "tourapi_2026_stratified_estimate": strat,
            "new_sample_no_detail_images": sum(1 for r in nr if not r["detail_images"]),
            "new_sample_detail_images_total": sum(len(r["detail_images"]) for r in nr),
            "new_api_calls": new["cumulative_api_calls"], "new_call_failures": len(new.get("stops", [])) + (1 if new.get("stopped") else 0),
        },
        "portrait_rule": {
            "survey_poster_named_images": len(sp),
            "survey_poster_named_portrait_ge_1_25": sum(x["ratio"] >= 1.25 for x in sp),
            "survey_poster_named_ratio_min_max": [min(x["ratio"] for x in sp), max(x["ratio"] for x in sp)],
            "survey_poster_named_visually_checked": len(poster_checked), "survey_poster_named_visually_confirmed_posters": len(poster_checked),
            "nohint_festivals": images["nohint_festivals"], "nohint_images_total": images["nohint_images_total"],
            "nohint_images_downloaded": len(rows),
            "nohint_portrait_ge_1_25": len(portrait),
            "nohint_portrait_labels": {lab: sum(1 for x in portrait if x.get("visual_label") == lab) for lab in ("poster", "info-card", "photo")},
            "nohint_4x5_band_1_20_to_1_25": len(near45),
            "nohint_4x5_band_labels": {lab: sum(1 for x in near45 if x.get("visual_label") == lab) for lab in ("poster", "info-card", "photo")},
            "nohint_landscape": len(landscape), "nohint_landscape_viewed": sum(1 for x in landscape if x.get("visual_label")),
            "nohint_landscape_viewed_posters": sum(1 for x in landscape if x.get("visual_label") == "poster"),
            "posters_found_without_name_hint": [{"title": x["title"], "imgname": x["imgname"], "w": x["width"], "h": x["height"]} for x in posters_found],
            "nohint_festivals_flagged_by_portrait": len(fests_flagged),
            "nohint_festivals_recovered_poster": len(fests_recovered),
            "portrait_precision_in_nohint_images": wilson(sum(1 for x in portrait if x.get("visual_label") == "poster"), len(portrait)),
        },
        "visual_labels": [{"file": x["file"], "title": x["title"], "imgname": x["imgname"], "w": x["width"], "h": x["height"],
                           "ratio": x["ratio"], "label": x["visual_label"]} for x in rows if x.get("visual_label")],
    }
    (OUT / "stats.summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "visual_labels"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
