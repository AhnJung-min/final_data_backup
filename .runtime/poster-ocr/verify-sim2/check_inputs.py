import json, hashlib, sys
from pathlib import Path
from PIL import Image, ImageOps, ImageChops
ROOT = Path(sys.argv[1])
man = json.loads((ROOT/"data/synthetic/poster-check-v1/manifest.json").read_text(encoding="utf-8"))
runs = ROOT/"data/runtime/poster-ocr/sim/runs/paddleocr"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def norm(p):
    with Image.open(p) as im:
        im.load(); o = ImageOps.exif_transpose(im).convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", o.size, "white"), o).convert("RGB")
bad = 0
for c in man["cases"]:
    pay = json.loads((runs/c["id"]/"rgb_1x.json").read_text(encoding="utf-8"))
    src = ROOT/c["image"]
    s_ok = sha(src) == c["sha256"]
    inp = ROOT/".runtime/poster-ocr/inputs"/c["id"]/"rgb_1x.png"
    inp_sha = sha(inp) if inp.exists() else None
    same_file = inp_sha == pay.get("input_sha256")
    pix_same = None
    if inp.exists():
        a = norm(src); b = Image.open(inp).convert("RGB")
        pix_same = a.size == b.size and ImageChops.difference(a, b).getbbox() is None
    flag = (not s_ok) or (not same_file) or (pix_same is False) or pay.get("normalized_dimensions") != list(norm(src).size)
    if flag: bad += 1
    if flag or "source_sha256" in pay:
        print(c["id"], "src_ok", s_ok, "payload_src_sha", (pay.get("source_sha256") or "-")[:12], "inp_file==payload", same_file, "pixels_match_source", pix_same, "status", pay["status"])
print("flagged", bad, "of", len(man["cases"]))
