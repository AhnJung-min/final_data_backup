"""Step 5: download <=60 detail images of survey festivals with no poster-named image; read dims of the 37 survey posters."""
import json
import sys
from pathlib import Path

from PIL import Image

REPO = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
OUT = Path(__file__).resolve().parent
IMG = OUT / "img"
sys.path.insert(0, str(REPO))
from app.festival_data.settings import Settings, CollectionError  # noqa: E402
from app.festival_data.client import ApiClient  # noqa: E402

SURVEY_DIR = REPO / "data/runtime/festivals/poster-survey-2026-10-14"
LIMIT = 60


def main():
    survey = json.loads((SURVEY_DIR / "survey.sample.json").read_text(encoding="utf-8"))
    nohint = [r for r in survey["records"] if r["detail_images"] and not any(d.get("poster_name_hint") for d in r["detail_images"])]
    # round-robin across festivals so every festival is represented
    queue, depth = [], 0
    while len(queue) < LIMIT and any(depth < len(r["detail_images"]) for r in nohint):
        for r in nohint:
            if depth < len(r["detail_images"]) and len(queue) < LIMIT:
                queue.append((r, depth, r["detail_images"][depth]))
        depth += 1
    client = ApiClient(Settings.load(REPO))
    rows = []
    for r, pos, d in queue:
        row = {"content_id": r["content_id"], "title": r["title"], "position": pos, "imgname": d.get("imgname"),
               "serialnum": d.get("serialnum"), "url": d.get("originimgurl")}
        try:
            info = client.download_image(d["originimgurl"], IMG)
            row.update(file=Path(info["file"]).name, width=info["width"], height=info["height"], format=info["format"],
                       ratio=round(info["height"] / info["width"], 3))
        except CollectionError as error:
            row["error"] = str(error)
        rows.append(row)
    posters = []
    for r in survey["records"]:
        p = r.get("poster")
        if not p or not p.get("image"):
            continue
        f = SURVEY_DIR / "images" / Path(p["image"]["file"]).name
        with Image.open(f) as im:
            w, h = im.size
        posters.append({"content_id": r["content_id"], "title": r["title"], "imgname": p.get("imgname"), "file": f.name,
                        "width": w, "height": h, "ratio": round(h / w, 3)})
    result = {"nohint_festivals": len(nohint), "nohint_images_total": sum(len(r["detail_images"]) for r in nohint),
              "downloaded": rows, "survey_posters": posters}
    (OUT / "images.manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [x for x in rows if "ratio" in x]
    print(json.dumps({"queued": len(queue), "ok": len(ok), "errors": len(rows) - len(ok),
                      "portrait_ge_1.25": sum(x["ratio"] >= 1.25 for x in ok),
                      "posters": len(posters), "posters_portrait_ge_1.25": sum(x["ratio"] >= 1.25 for x in posters),
                      "poster_ratios": sorted(x["ratio"] for x in posters)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
