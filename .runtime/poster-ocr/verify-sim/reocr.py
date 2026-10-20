"""Re-run PaddleOCR (same engine settings as ocr_worker.PaddleEngine) on the CURRENT poster PNGs whose cached inputs were stale."""
import json, shutil, sys, time
from pathlib import Path
REPO = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
OUT = REPO/".runtime/poster-ocr/verify-sim"
sys.path.insert(0, str(REPO/"app/poster_ocr"))
import ocr_worker
ocr_worker.ROOT = OUT  # paddlex cache home -> OUT/.runtime/paddlex (keep writes inside verify-sim)
engine = ocr_worker.PaddleEngine(REPO/".runtime/paddle-models")
print("loaded", engine.load_seconds, flush=True)
for sid in sys.argv[1:]:
    cid = f"{sid}-poster"
    src = REPO/f"data/synthetic/poster-check-v1/posters/{sid}.png"
    rgb = ocr_worker.normalized_rgb(src)
    image, scale = ocr_worker.prepare_variant(rgb, "rgb_1x")
    inp = OUT/"inputs"/cid/"rgb_1x.png"; inp.parent.mkdir(parents=True, exist_ok=True); image.save(inp)
    t = time.perf_counter()
    raw = engine.run(image, inp)
    recs = ocr_worker.to_records(raw, scale, rgb.width, rgb.height, "padd_rgb_1x")
    payload = {"case_id": cid, "engine": "paddleocr", "variant": "rgb_1x", "scale": scale, "normalized_dimensions": [rgb.width, rgb.height],
               "input_dimensions": [image.width, image.height], "input_sha256": ocr_worker.sha256_file(inp), "coordinate_space": "normalized_original_pixels",
               "status": "success", "records": recs, "inference_seconds": round(time.perf_counter() - t, 3)}
    d = OUT/"runs/paddleocr"/cid; d.mkdir(parents=True, exist_ok=True)
    (d/"rgb_1x.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    dd = OUT/"runs/paddleocr"/f"{sid}-doc"; dd.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO/f"data/runtime/poster-ocr/sim/runs/paddleocr/{sid}-doc/rgb_1x.json", dd/"rgb_1x.json")
    print(sid, "done", payload["inference_seconds"], len(recs), flush=True)
