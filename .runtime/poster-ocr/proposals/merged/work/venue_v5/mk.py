from pathlib import Path
M = Path(".runtime/poster-ocr/proposals/merged")
src = open(".runtime/poster-ocr/proposals/venue/extract.py", encoding="utf-8", newline="").read()
old1 = '            if before[-1:].isdigit() or re.search(r"(?:로|길|번지|동|리)\s*$", before):\r\n'
old2 = '            if not (1 <= int(g["sm"]) <= 12 and 1 <= int(g["sd"]) <= 31):\r\n                return m.group(0)\r\n'
assert src.count(old1) == 1 and src.count(old2) == 1
open(M / "work/venue_v5/extract.py", "w", encoding="utf-8", newline="").write(src.replace(old1, '            if False:\r\n').replace(old2, ''))
