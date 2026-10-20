import json, sys, time, hashlib
from pathlib import Path
import numpy as np
from PIL import Image
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr.ocr_worker import normalized_rgb
m = json.loads((ROOT/"data/synthetic/poster-check-v1/manifest.json").read_text(encoding="utf-8"))
stale = []
for c in m["cases"]:
    src = ROOT/c["image"]
    inp = ROOT/".runtime/poster-ocr/inputs"/c["id"]/"rgb_1x.png"
    ocr = ROOT/"data/runtime/poster-ocr/sim/runs/paddleocr"/c["id"]/"rgb_1x.json"
    a = np.asarray(normalized_rgb(src)); b = np.asarray(Image.open(inp).convert("RGB"))
    same = a.shape == b.shape and np.array_equal(a, b)
    o = json.loads(ocr.read_text(encoding="utf-8"))
    hash_ok = hashlib.sha256(inp.read_bytes()).hexdigest() == o["input_sha256"]
    diffpx = int((np.abs(a.astype(int)-b.astype(int)).sum(axis=2) > 0).sum()) if a.shape == b.shape else -1
    if not same or not hash_ok:
        stale.append(c["id"])
    print(c["id"], "pixels_equal" if same else f"DIFF px={diffpx}", "ocr_hash_ok" if hash_ok else "OCR_HASH_MISMATCH",
          "input", time.strftime("%H:%M:%S", time.localtime(inp.stat().st_mtime)), "src", time.strftime("%H:%M:%S", time.localtime(src.stat().st_mtime)),
          "ocr", time.strftime("%H:%M:%S", time.localtime(ocr.stat().st_mtime)))
print("STALE:", stale, len(stale))
