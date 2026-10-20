"""Build ablation variants of the merged working copy: each variant switches off one change (exact string edits)."""
import os
import sys
from pathlib import Path
import subprocess

M = Path(".runtime/poster-ocr/proposals/merged")
SRC = (M / os.environ.get("ABL_BASE", "work/merged.lf.py")).read_text(encoding="utf-8")
OUT = os.environ.get("ABL_OUT", "work/abl")
ORIG = (M / "work/orig.lf.py").read_text(encoding="utf-8")


def between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


ORIG_ORG = between(ORIG, "    orgs = []\n    for l in lines:\n        marks = list(ROLE.finditer", "    doc[\"organizations\"].update(")
NEW_ORG = between(SRC, "    orgs, org_ids = organization_items(records)\n", "    doc[\"organizations\"].update(")
ORIG_ADDR = between(ORIG, "    address = next(((l, m, \"road\")", "    else:\n        doc[\"address\"][\"status\"] = \"absent\"")
NEW_ADDR = between(SRC, "    address = None\n    for kind in (\"road\", \"jibun\"):", "    else:\n        doc[\"address\"][\"status\"] = \"absent\"")
ORIG_TIMES = between(ORIG, "        for m in re.finditer(r\"(오전|오후", "            if start_c:\n                times.append")
NEW_TIMES = between(SRC, "        # 시·분 구분은 ':'·'시'", "            if start_c:\n                times.append")
ORIG_YEAR = 'YEAR = re.compile(r"(?<!\\d)(20[2-3]\\d)(?!\\d)")\n'
NEW_YEAR = [l + "\n" for l in SRC.splitlines() if l.startswith("YEAR = re.compile")][0]
NEW_VW = [l for l in SRC.splitlines() if "시장$|시청|군청" in l][0]

A = {
    # ---- title
    "T1_column": [("""    for direction in (-1, 1):
        while len(chosen) < 3:
            ref = chosen[0] if direction < 0 else chosen[-1]
            other = column_neighbor(lines, ref, direction, chosen)
            if other is None:
                break
""", """    for direction in (-1, 1):
        index = best["index"]
        while len(chosen) < 3:
            index += direction
            if not 0 <= index < len(lines):
                break
            other = lines[index]
            ref = chosen[0] if direction < 0 else chosen[-1]
""")],
    "T2_latin_tail": [('drop_latin_tail(unicodedata.normalize("NFC", r["text"]).strip())', 'unicodedata.normalize("NFC", r["text"]).strip()')],
    "T3_suffix": [('score += 3.0 if suffix and hangul_or_latin(stripped[:suffix.start()]) >= 1 else 0', 'score += 3.0 if suffix else 0')],
    "T4_year": [(NEW_YEAR, ORIG_YEAR)],
    "T5_edition_word": [("    elif worded:\n", "    elif False and worded:\n")],
    # ---- schedule
    "S1a_comma": [('sep>월|[./-]|[.,]{1,2})', 'sep>월|[./-])'),
                  ('20[2-3]\\d)\\s*(?:년|[./,-])\\s*)?" + ("(?:"', '20[2-3]\\d)\\s*(?:년|[./-])\\s*)?" + ("(?:"'),
                  ('x>일|[.,])?', 'x>일|\\.)?'),
                  ('END_HAS_MONTH = r"(?:20[2-3]\\d\\s*(?:년|[./,-])\\s*)?\\d{1,2}\\s*(?:월|[./,-])\\s*\\d"',
                   'END_HAS_MONTH = r"(?:20[2-3]\\d\\s*(?:년|[./-])\\s*)?\\d{1,2}\\s*(?:월|[./-])\\s*\\d"')],
    "S1b_double_sep": [('RANGE_SEP = r"\\s*(?:~|-|=|부터|∼|to)(?:\\s*[~=\\-])*\\s*"', 'RANGE_SEP = r"\\s*(?:~|-|=|부터|∼|to)\\s*"')],
    "S1c_brackets": [('return r"\\s*(?:[(\\[]\\s*){0,2}(?P<"', 'return r"\\s*(?:[(\\[]\\s*)?(?P<"')],
    "S2a_weekday_en": [('WD_EN = r"(?i:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]{0,6}(?![a-z])|(?:mo|tu|we|th|fr|sa|su)(?![a-z]))"',
                        'WD_EN = r"(?i:mon|tue|wed|thu|fri|sat|sun)"')],
    "S2b_il_sunday": [('    if weekday is None and g.get(p + "x") == "일":\n', '    if False:\n')],
    "S3_spaced": [('sep>월|[./-]|[.,]{1,2})\\s*|(?P<" + p + r"sp>\\s+)" + SPACED_AHEAD + r")")', 'sep>월|[./-]|[.,]{1,2})\\s*)")')],
    "S4_nosep": [('if kind == "nosep" and not ((has_month and g.get("ew")) or e_ok or (has_month and s_ok and g.get("ex")) or slashed):',
                  'if kind == "nosep" and not (has_month and g.get("ew")):')],
    "S5_noise": [('|(?P<sn>[^\\s~=\\-A-Za-z])(?=" + RANGE_SEP + END_HAS_MONTH + r")))?"', '))?"')],
    "S6a_weak_single": [("""                        if rest is None or len(re.findall(r"[0-9A-Za-z가-힣]", rest)) > 1:
                            continue
                        weak = True
""", """                        continue
""")],
    "S6b_twoline_pair": [('    singles = [c for c in found if c["kind"] == "single"]\n',
                          '    singles = [c for c in found if c["kind"] == "single" and len(c["members"]) == 1]\n')],
    "S7_multi_period": [("    return extend_multi_period(best, candidates, lines, poster_year) if best else None\n", "    return best\n")],
    "S8_times": [(NEW_TIMES, ORIG_TIMES)],
    # ---- venue
    "V1_label_mid": [('m = VENUE_LABEL.match(l["norm"]) or VENUE_LABEL_MID.search(l["norm"])', 'm = VENUE_LABEL.match(l["norm"])')],
    "V2_same_row": [("            right = same_row_value(l, lines)\n", "            right = None\n")],
    "V3a_segment_sched": [('        rest = venue_segment(last["norm"])\n',
                           '        rest = remove_tokens(last["norm"], [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN, DATE_LABEL, '
                           're.compile(r"\\d+\\s*일\\s*간"), re.compile(r"주말\\s*집중\\s*운영")])\n')],
    "V3b_segment_kw": [('venue_segment(best["norm"]) or best["norm"].strip(), False', 'best["norm"].strip(), False')],
    "V4_clean": [('    text = clean_venue(strip_dates(VENUE_LABEL.sub(" ", text, count=1)))\n', '    text = strip_dates(VENUE_LABEL.sub(" ", text, count=1))\n'),
                 ('    return clean_venue(chosen)\n', '    return chosen\n')],
    "V5_date_guard": [('            if before[-1:].isdigit() or re.search(r"(?:로|길|번지|동|리)\\s*$", before):\n', '            if False:\n'),
                      ('            if not (1 <= int(g["sm"]) <= 12 and 1 <= int(g["sd"]) <= 31):\n                return m.group(0)\n', '')],
    "V6a_5digits": [('                   and not TITLE_SUFFIX.search(l["norm"]) and not re.search(r"\\d{5,}", l["norm"])]',
                     '                   and not TITLE_SUFFIX.search(l["norm"])]')],
    "V6b_date_anchor": [("        if anchor is None:  # 일정을", "        if False:  # 일정을")],
    "V7a_join_above": [("                     or (aligned and .75 <= ratio", "                     or (False and aligned and .75 <= ratio")],
    "V7b_join_multi": [("    while len(value_lines) < 3:\n", "    while len(value_lines) == 1:\n")],
    "V7c_neg_gap": [('-.35 * first["h"] <= gap', '0 <= gap'), ('-.35 * last["h"] <= gap', '0 <= gap')],
    "V8_words": [(NEW_VW, NEW_VW.replace("|시청|군청|구청|도청|(?<![지구영전])역$", ""))],
    "V9_addr_cut": [('    for sep in re.finditer(r"\\s+[|/]\\s+", text):\n', '    for sep in []:\n')],
    "V10_address": [(NEW_ADDR, ORIG_ADDR)],
    "V11a_title_fallback": [("    if (region is None or district is None) and fallback:\n", "    if False:\n")],
    "V11b_not_tail": [(" or NOT_DISTRICT_TAIL.search(name):", ":")],
    "V11c_sido_strip": [('    spaced = SIDO_CITY.sub(lambda m: " " * len(m.group(0)), text)\n', '    spaced = text\n')],
    "V11d_multi_district": [("                for start, stop, name in found:\n", "                for start, stop, name in found[:1]:\n")],
    "V11e_district_ilwon": [('(?=$|[^가-힣]|일원|일대)")', '(?![가-힣])")')],
    # ---- lists
    "L1_org_rows": [(NEW_ORG, ORIG_ORG)],
    "L2_role_mark": [('ROLE_MARK = re.compile(r"(?<![가-힣])(?:공\\s*동\\s*)?(주\\s*최\\s*(?:[·/,&\\-\\u119e]|및)?\\s*주\\s*관|',
                      'ROLE_MARK = re.compile(r"(주\\s*최\\s*[·∙・/,]?\\s*주\\s*관|')],
    "L3_org_clean": [('    segment = PHONE.sub(" ", segment)\n    segment = re.sub(r"\\(\\s*\\)|\\[\\s*\\]", " ", segment)\n', ''),
                     ('        part = CONTACT_TAIL.sub("", part).strip(ORG_STRIP)\n        if hangul_or_latin(part) < 2 or is_mostly_digits(part):\n',
                      '        part = part.strip(ORG_STRIP)\n        if hangul_or_latin(part) < 2 or is_mostly_digits(part) or PHONE.search(part):\n')],
    "L4_unlabeled": [('    orgs += unlabeled_institutions(records, image, used, line_of, {re.sub(r"\\s+", "", o["name"]) for o in orgs})\n', '')],
    "L5_phone_run": [('        found += [m for m in PHONE_RUN.finditer(l["norm"]) if not any(m.start() < f.end() and f.start() < m.end() for f in found)]\n', '')],
    "L6_clock_span": [("            elif clock_span(m.group(4), m.group(5)):\n", "            elif False:\n")],
    "L7_sns_handle": [('            if raw.startswith("@") and not sns_handle(raw):\n', '            if False:\n')],
    "L8_link_spaced": [('            found = list(LINK_SPACED.finditer(l["norm"]))\n', '            found = []\n')],
    "L9a_notice_frag": [('    if "※" in text and hangul_or_latin(text.replace("※", " ")) < 3:\n', '    if False:\n')],
    "L9b_notice_parking": [('    probe = PARKING_PLACE.sub(" ", text)\n', '    probe = text\n')],
    "L9c_notice_more": [('    return bool(NOTICE.search(probe) or NOTICE_MORE.search(probe))\n', '    return bool(NOTICE.search(probe))\n')],
    "L10_badge_more": [('[BADGE.search(l["norm"]) or BADGE_MORE.search(l["norm"])]', '[BADGE.search(l["norm"])]')],
    "L11a_budget_label": [('(?:(?:행사\\s*|축제\\s*)?예\\s*산|사\\s*업\\s*비)', '(?:행사\\s*|축제\\s*)?예\\s*산')],
    "L11b_krw_dot": [('    if match.group(2) is None and re.fullmatch(r"\\d{1,3}(?:[,.]\\d{3})+", match.group(1)):\n', '    if False:\n')],
}

ORIG_ORG_PLUS_UNLABELED = ORIG_ORG + ('    line_of = {r["id"]: l["index"] for l in lines for r in l["members"]}\n'
                                      '    orgs += unlabeled_institutions(records, image, used, line_of, {re.sub(r"\\s+", "", o["name"]) for o in orgs})\n')
A.update({
    "V7d_joiner_first": [(' or VENUE_JOINER.search(text))):\n            break\n', ')):\n            break\n')],
    "V7e_label_skip": [('        following = [o for o in lines[last["index"] + 1:last["index"] + 3] if all(o is not x for x in label_lines)]\n',
                        '        following = lines[last["index"] + 1:last["index"] + 2]\n')],
    "V8a_cheong": [(NEW_VW, NEW_VW.replace("|시청|군청|구청|도청", ""))],
    "V8b_station": [(NEW_VW, NEW_VW.replace("|(?<![지구영전])역$", ""))],
    "L1b_rows_only": [(NEW_ORG, ORIG_ORG_PLUS_UNLABELED)],
    "S1a1_year_comma": [('20[2-3]\\d)\\s*(?:년|[./,-])\\s*)?" + ("(?:"', '20[2-3]\\d)\\s*(?:년|[./-])\\s*)?" + ("(?:"')],
    "S1a2_month_comma": [('sep>월|[./-]|[.,]{1,2})', 'sep>월|[./-])')],
    "S1a3_day_comma": [('x>일|[.,])?', 'x>일|\\.)?')],
    "V7c_neg_gap_final": [('-.35 * first["h"] <= gap', '0 <= gap'), ('-.35 * last["h"] <= nxt["box"][1] - last["box"][3]', '0 <= nxt["box"][1] - last["box"][3]')],
    "V7d_joiner_first_final": [(' or VENUE_JOINER.search(text))):\n            value_lines, text = value_lines + [nxt]',
                                ')):\n            value_lines, text = value_lines + [nxt]')],
    "S1a4_endmonth_comma": [('END_HAS_MONTH = r"(?:20[2-3]\\d\\s*(?:년|[./,-])\\s*)?\\d{1,2}\\s*(?:월|[./,-])\\s*\\d"',
                             'END_HAS_MONTH = r"(?:20[2-3]\\d\\s*(?:년|[./-])\\s*)?\\d{1,2}\\s*(?:월|[./-])\\s*\\d"')],
})


def build(name, edits, base=SRC):
    text = base
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"{name}: pattern occurs {n} times: {old[:80]!r}")
        text = text.replace(old, new)
    compile(text, name, "exec")
    d = M / OUT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "src.lf.py").write_text(text, encoding="utf-8", newline="\n")
    subprocess.run([sys.executable, str(M / "tools/mkcand.py"), str(d / "src.lf.py"), str(d / "extract.py")], check=True, capture_output=True)
    return d


if __name__ == "__main__":
    names = sys.argv[1:] or list(A)
    for n in names:
        build(n, A[n])
    print("built", len(names))
