import json, sys
s = json.load(sys.stdin)
if "items" not in s: print(s); raise SystemExit
print({k: s[k] for k in ["running","stalled","finished","started","current","eta_seconds","failed","cases_done","sets_done","match_review"]}, s["report"]["confusion"])
for i in s["items"]:
    if i["stage"] != "done" or i["id"] in sys.argv[1:]:
        print(" ", i["id"], i["stage"], i["verdict"], i["counts"], i["checked_at"])
