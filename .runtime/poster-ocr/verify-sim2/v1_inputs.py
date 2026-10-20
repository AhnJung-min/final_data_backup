import hashlib, json, os, sys, time
from pathlib import Path
from PIL import Image, ImageOps, ImageChops
import numpy as np
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
man = json.loads((ROOT/"data/synthetic/poster-check-v1/manifest.json").read_text(encoding="utf-8"))
RUNS = ROOT/"data/runtime/poster-ocr/sim/runs/paddleocr"
INP = ROOT/".runtime/poster-ocr/inputs"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def norm(p):
    with Image.open(p) as im:
        im.load(); o = ImageOps.exif_transpose(im).convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", o.size, "white"), o).convert("RGB")
bad = []
print(len(man["cases"]))
for c in man["cases"]:
    cid = c["id"]; src = ROOT/c["image"]
    out = RUNS/cid/"rgb_1x.json"; inp = INP/cid/"rgb_1x.png"
    probs = []
    fsha = sha(src)
    if fsha != c["sha256"]: probs.append("file sha != manifest")
    d = json.loads(out.read_text(encoding="utf-8"))
    if d.get("status") != "success": probs.append("status "+str(d.get("status")))
    if "source_sha256" in d and d["source_sha256"] != c["sha256"]: probs.append("source_sha256 mismatch")
    if not inp.exists(): probs.append("no input cache")
    else:
        isha = sha(inp)
        if d.get("input_sha256") != isha: probs.append(f"input_sha256 in output != cache file sha")
        a = np.asarray(norm(src)); b = np.asarray(Image.open(inp).convert("RGB"))
        if a.shape != b.shape: probs.append(f"shape {a.shape} vs {b.shape}")
        elif not np.array_equal(a, b): probs.append(f"pixels differ: {(a!=b).any(axis=2).sum()} px")
    if list(d["normalized_dimensions"]) != [Image.open(src).width, Image.open(src).height]: probs.append("dims")
    mt_src, mt_out, mt_inp = os.path.getmtime(src), os.path.getmtime(out), os.path.getmtime(inp) if inp.exists() else 0
    if mt_src > mt_out: probs.append(f"PNG newer than OCR output ({time.ctime(mt_src)} > {time.ctime(mt_out)})")
    if mt_inp > mt_out + 5: probs.append(f"input cache newer than output ({time.ctime(mt_inp)} vs {time.ctime(mt_out)})")
    flag = "src" if "source_sha256" in d else "-"
    print(cid, flag, time.strftime("%H:%M:%S", time.localtime(mt_src)), time.strftime("%H:%M:%S", time.localtime(mt_inp)), time.strftime("%H:%M:%S", time.localtime(mt_out)), "OK" if not probs else probs)
    if probs: bad.append((cid, probs))
print("BAD", bad)
