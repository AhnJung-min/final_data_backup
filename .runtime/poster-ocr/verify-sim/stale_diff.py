import sys
from pathlib import Path
import numpy as np
from PIL import Image
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr.ocr_worker import normalized_rgb
OUT = ROOT/".runtime/poster-ocr/verify-sim"
for sid in ["S03", "S05", "S08"]:
    a = normalized_rgb(ROOT/f"data/synthetic/poster-check-v1/posters/{sid}.png")
    b = Image.open(ROOT/f".runtime/poster-ocr/inputs/{sid}-poster/rgb_1x.png").convert("RGB")
    A, B = np.asarray(a).astype(int), np.asarray(b).astype(int)
    mask = np.abs(A-B).sum(axis=2) > 0
    ys, xs = np.nonzero(mask)
    print(sid, "diff bbox x", xs.min(), xs.max(), "y", ys.min(), ys.max(), "rows with diff:", sorted(set((ys//20*20).tolist()))[:40])
    side = Image.new("RGB", (a.width*2+10, a.height), "white")
    side.paste(b, (0, 0)); side.paste(a, (a.width+10, 0))
    side.save(OUT/f"{sid}_stale_vs_current.png")
