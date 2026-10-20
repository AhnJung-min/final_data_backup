"""Turn an LF working copy (with the original ROOT line) into a runnable CRLF candidate with the PROPOSAL-SHIM ROOT line."""
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read().replace("\r\n", "\n")
orig = 'ROOT = Path(__file__).resolve().parents[2]\n'
shim = ('ROOT = next((p for p in Path(__file__).resolve().parents if (p / "data/festivals/poster.template.json").exists()), '
        'Path(__file__).resolve().parents[2])  # PROPOSAL-SHIM\n')
assert text.count(orig) == 1, "ROOT line not found exactly once"
text = text.replace(orig, shim)
open(dst, "w", encoding="utf-8", newline="\r\n").write(text)
print("wrote", dst)
