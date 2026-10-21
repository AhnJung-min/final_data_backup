"""가상 세트(poster-check-v1·v2) 글자 정답 상자: sim_data.py를 고치지 않고 다시 그리며 ImageDraw.text 호출을 기록한다.

다시 그린 PNG의 sha256이 manifest와 같은 이미지만 상자를 저장한다(같은 그림이라는 보장).
빛 번짐(RGBA 층) 글자·빈 문자열·문장부호뿐인 조각은 뺀다. 위쪽 y는 그린 좌표의 textbbox로 잰다(center()는 아래쪽만 돌려준다).
한 줄에 여러 항목을 이어 쓴 줄('주최 … | 주관 … | 문의 …', 프로그램 목록, 타이포 틀의 '일시  값')은 앞부분 글자 폭(textlength)을 재서 부분 상자로 나눈다.
저장된 가상 OCR(sim_check RUNS/VARIANT, rgb_1x)과 IoU ≥ 0.5로 맞춰 항목별 검출 재현율과 CER을 잰다(detection.json).
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.synth_boxes build --set v1 v2
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import re
import statistics
import sys

import PIL
from PIL import Image, ImageDraw, features

from . import sim_check as sc
from . import sim_data as S
from .evaluate import key, levenshtein, ocr_text, semiglobal, wilson

ROOT = Path(__file__).resolve().parents[2]
SYNTH = ROOT / "data/synthetic"
OUT = ROOT / "data/runtime/poster-ocr/synth-boxes"
FONT_FILES = ("NotoSansKR-VF.ttf", "NotoSerifKR-VF.ttf", "malgun.ttf", "malgunbd.ttf")
IOU, PIECE_IN, COVER = 0.5, 0.5, 0.7  # 짝 기준 IoU · 나눠 읽은 조각이 정답 상자 안에 든 비율(OCR 상자 위아래 여백 때문에 0.5) · 큰 OCR 상자가 정답 상자를 덮은 비율
LOOSE_OTHERS = 0.1  # 큰 OCR 상자에서 다른 정답 상자가 차지한 비율이 이보다 작으면 '여백 큰 한 상자', 크면 '합쳐 읽음'
SEP = re.compile(r"\s+[|·]\s+|\s{2,}|^·\s+")  # 이어 쓴 항목 사이: 두 칸 이상 공백, 앞뒤 공백 있는 | ·, 줄 앞 '· '
LABELS = {"일시", "시간", "장소", "주최", "주관", "문의"}
DOC_LABELS = {text for _, text in sc.DOC_LABELS} | {"항목", "표기 내용", "비고"}
POSTER_FIELDS = ["title", "edition_year", "schedule", "time", "venue", "host", "organizer", "contact", "slogan", "programs", "label", "decor_mark", "other"]
DOC_FIELDS = ["title", "schedule", "time", "venue", "host", "organizer", "contact", "slogan", "label", "boilerplate"]
CHECKED = ["title", "edition_year", "schedule", "time", "venue", "host", "organizer", "contact", "slogan"]  # sim_check가 대조하는 항목(라벨·장식·프로그램 제외)
STATUSES = ("match", "loose", "split", "merged", "missed")
STATUS_LABEL = {"match": "일치(IoU≥0.5)", "loose": "여백 큰 한 상자", "split": "나눠 읽음", "merged": "합쳐 읽음", "missed": "못 찾음"}
COLORS = {"match": (22, 163, 74), "loose": (13, 148, 136), "split": (217, 119, 6), "merged": (37, 99, 235), "missed": (220, 38, 38)}
_ORIG = ImageDraw.ImageDraw.text
NOTES = [
    "v1과 v2는 같은 디자인 틀 5개를 쓴다(template = s % 5). v2는 지역·주제 짝과 세트 순서만 섞었다.",
    "v1은 sim_check 규칙을 맞출 때 본 세트다. v2는 홀드아웃으로 남겨야 하므로 v1·v2 상자로 검출기나 규칙을 학습·조정하지 않는다. "
    "학습용 상자가 필요하면 새 틀 묶음(v3)으로 만든 세트에서 가져와야 한다(같은 틀을 외우면 홀드아웃 평가가 무의미해진다).",
    "제품은 기준 문서를 글자(텍스트)로 받으므로 문서 이미지의 OCR 상자는 제품과 관계없다. 문서 수치는 따로 적고 product_relevant=false로 표시한다.",
    "bbox는 Pillow textbbox(획 두께 포함, 그린 좌표에서 잰 실제 위·아래), ink는 같은 글자를 빈 마스크에 다시 그려 잰 잉크 경계다. 둘 다 캔버스 밖은 잘랐다(clipped).",
    "한 줄에 이어 쓴 항목은 앞부분 글자 폭으로 나눈 부분 상자다(split=true). line 번호가 같은 상자를 합치면 그린 줄 하나가 된다.",
    "다른 PC에서는 fonts의 sha256과 Pillow·FreeType 버전이 같아야 같은 PNG가 나온다. sha256이 다르면 그 이미지의 상자는 쓰지 않는다.",
]
DEFINITIONS = {
    "match": "정답 상자와 OCR 상자를 IoU 큰 순서로 1:1 짝지어 IoU ≥ 0.5",
    "split": "1:1 짝은 없지만 정답 상자 안에 OCR 상자 면적의 50% 이상이 든 조각 2개 이상을 합치면 IoU ≥ 0.5(낱말 단위로 나눠 읽음)",
    "loose": "1:1 짝은 없지만 OCR 상자 하나가 정답 상자 면적의 70% 이상을 덮고 다른 정답 상자는 거의 덮지 않음(그 상자 면적의 10% 미만). "
             "같은 항목을 여백을 넉넉히 잡아 찾은 것으로, PaddleOCR 상자 여백(약 7~9px) 때문에 두 글자 낱말에서 IoU가 0.5 아래로 떨어진다",
    "merged": "1:1 짝은 없지만 OCR 상자 하나가 정답 상자 면적의 70% 이상을 덮고 다른 정답 상자도 함께 덮음(옆 항목과 한 상자로 읽음)",
    "missed": "위 넷이 아님(iou는 가장 많이 겹친 OCR 상자의 IoU)",
    "recall": "match / n (Wilson 95% 구간)", "single": "(match + loose) / n — 항목 하나를 OCR 상자 하나로 찾음",
    "found": "(n - missed) / n — 어떤 모양으로든 OCR 상자가 그 자리를 덮음",
    "cer": "공백·문장부호를 뺀(evaluate.key) 글자 편집거리 / 정답 글자 수, 1에서 자름. split은 조각을 읽기 순서로 이어 붙이고, "
           "merged는 OCR 문구 안 가장 가까운 부분 문자열(준전역 편집거리)과 비교",
    "cer_matched": "match 상자의 평균 CER", "exact_matched": "match 상자 중 CER 0 비율", "read_exact": "match이면서 CER 0인 비율(분모 n)",
    "lines": "그린 줄 단위(같은 line 번호 부분 상자의 합집합, 글자는 공백으로 이음)로 같은 방법을 적용한 결과",
    "checked_fields": "sim_check가 대조하는 항목(title·edition_year·schedule·time·venue·host·organizer·contact·slogan)만 모은 결과. 문서 쪽은 edition_year 없이 같은 이름",
    "unexplained_ocr_boxes": "정답 상자와 겹친 면적이 OCR 상자 면적의 50% 미만이고, 어느 정답 상자도 절반 이상 덮지 않은 OCR 상자(장식·배경을 글자로 읽음)",
}


# ------------------------------------------------------------------ 기록
def spans(text):
    """이어 쓴 항목 조각 [(글자, 시작 위치)]. 조각 앞뒤 공백은 뺀다."""
    out, start = [], 0
    for m in [*SEP.finditer(text), None]:
        piece = text[start:m.start() if m else len(text)]
        if piece.strip():
            out.append((piece.strip(), start + len(piece) - len(piece.lstrip())))
        if m:
            start = m.end()
    return out


def clip(box, size):
    w, h = size
    c = [min(max(box[0], 0), w), min(max(box[1], 0), h), min(max(box[2], 0), w), min(max(box[3], 0), h)]
    return [round(float(v), 1) for v in c], any(a != b for a, b in zip(c, box))


def ink_of(xy, text, f, stroke, box):
    """같은 글자를 빈 마스크에 같은 소수 좌표로 다시 그려 잉크 경계를 잰다(정수만큼만 옮긴다)."""
    ox, oy = math.floor(box[0]) - 2, math.floor(box[1]) - 2
    mask = Image.new("L", (max(1, math.ceil(box[2]) - ox + 2), max(1, math.ceil(box[3]) - oy + 2)))
    _ORIG(ImageDraw.Draw(mask), (xy[0] - ox, xy[1] - oy), text, fill=255, font=f, stroke_width=stroke)
    b = mask.getbbox()
    return [b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy] if b else None


class Recorder:
    def __init__(self):
        self.parts, self.lines, self.counts, self.fonts = [], 0, Counter(), set()

    def add(self, draw, xy, text, f, stroke):
        size, line = draw.im.size, self.lines
        self.lines += 1
        self.fonts.add(Path(getattr(f, "path", "") or "").name)
        for piece, i in spans(text):
            if not key(piece):
                self.counts["punct"] += 1
                continue
            x = xy[0] + (draw.textlength(text[:i], font=f) if i else 0)
            box = draw.textbbox((x, xy[1]), piece, font=f, stroke_width=stroke)
            bbox, cut = clip(box, size)
            ink = ink_of((x, xy[1]), piece, f, stroke, box)
            self.parts.append({"text": piece, "bbox": bbox, "ink": clip(ink, size)[0] if ink else None, "field": None, "size": f.size,
                               "font": Path(getattr(f, "path", "") or "").name, "line": line, "split": piece != text, "stroke": stroke, "clipped": cut})


@contextmanager
def recording():
    """ImageDraw.text를 잠시 감싸 그린 글자를 기록한다(끝나면 원래 함수로 되돌린다)."""
    rec = Recorder()

    def hook(self, xy, text, fill=None, font=None, *args, **kw):
        if not isinstance(text, str) or not text.strip():
            rec.counts["empty"] += 1
        elif self.mode != "RGB":
            rec.counts["glow"] += 1  # 빛 번짐 층(RGBA)은 흐리게 겹친 장식이라 상자로 쓰지 않는다
        else:
            rec.add(self, xy, text, font, kw.get("stroke_width") or 0)
        return _ORIG(self, xy, text, fill, font, *args, **kw)

    ImageDraw.ImageDraw.text = hook
    try:
        yield rec
    finally:
        ImageDraw.ImageDraw.text = _ORIG


def png_sha(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)  # sim_data와 같은 저장 옵션
    return hashlib.sha256(buf.getvalue()).hexdigest()


def poster_field(t, p):
    if t in LABELS:
        return "label"
    exact = {p["schedule"]: "schedule", p["time"]: "time", p["time"].replace(" ~ ", " - "): "time", p["venue"]: "venue", p["slogan"]: "slogan",
             p["host"]: "host", p["organizer"]: "organizer", p["contact"]: "contact", S.edition_line(p): "edition_year"}
    if t in exact:
        return exact[t]
    if t in p["programs"]:
        return "programs"
    if t in {p["title"], p["short"], p["theme"], *p["theme"].split()}:
        return "title"
    if t in {str(p["edition"]), str(p["year"])[2:]}:
        return "decor_mark"  # 타이포 틀의 큰 배경 숫자
    return "other"


def doc_field(t, s):
    d = s["document"]
    exact = {d["official"]: "title", d["schedule"]: "schedule", d["time"]: "time", d["venue"]: "venue", d["host"]: "host",
             d["organizer"]: "organizer", d["contact"]: "contact", d["slogan"]: "slogan"}
    return exact.get(t) or ("label" if t in DOC_LABELS else "boilerplate")


def render(s, k, seed, kind):
    """(이미지, 기록) — 기록 중에만 ImageDraw.text를 감싼다."""
    with recording() as rec:
        img = S.poster_image(s["poster"], seed + k) if kind == "poster" else S.doc_image(s)
    for part in rec.parts:
        part["field"] = poster_field(part["text"], s["poster"]) if kind == "poster" else doc_field(part["text"], s)
    return img, rec


# ------------------------------------------------------------------ 검출 대조
def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a, b):
    i = inter(a, b)
    u = area(a) + area(b) - i
    return i / u if u > 0 else 0.0


def union_box(boxes):
    xs0, ys0, xs1, ys1 = zip(*boxes)
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def cer(truth, read):
    t = key(truth)
    return round(min(1.0, levenshtein(t, key(read)) / len(t)), 4) if t else None


def assign(gts, ocr):
    """정답 상자마다 (상태, IoU, 쓰인 OCR 기록, CER). OCR 기록은 bbox_xyxy·text를 쓴다."""
    pairs = sorted(((iou(g["bbox"], o["bbox_xyxy"]), i, j) for i, g in enumerate(gts) for j, o in enumerate(ocr)), reverse=True)
    paired, taken = {}, set()
    for v, i, j in pairs:
        if v < IOU:
            break
        if i not in paired and j not in taken:
            paired[i] = (j, v)
            taken.add(j)
    out = []
    for i, g in enumerate(gts):
        b = g["bbox"]
        if i in paired:
            j, v = paired[i]
            out.append(("match", v, [ocr[j]], cer(g["text"], ocr[j]["text"])))
            continue
        pieces = [o for j, o in enumerate(ocr) if j not in taken and area(o["bbox_xyxy"]) and inter(b, o["bbox_xyxy"]) / area(o["bbox_xyxy"]) >= PIECE_IN]
        v = iou(b, union_box([o["bbox_xyxy"] for o in pieces])) if len(pieces) >= 2 else 0.0
        if v >= IOU:
            out.append(("split", v, pieces, cer(g["text"], ocr_text(pieces))))
            continue
        cover = max(((inter(b, o["bbox_xyxy"]) / max(area(b), 1e-9), iou(b, o["bbox_xyxy"]), o) for o in ocr), key=lambda x: x[:2], default=None)
        if cover and cover[0] >= COVER:
            o = cover[2]["bbox_xyxy"]
            others = sum(inter(o, h["bbox"]) for k, h in enumerate(gts) if k != i) / max(area(o), 1e-9)
            if others < LOOSE_OTHERS:  # 다른 정답 상자를 거의 덮지 않음 → 같은 항목을 여백 넉넉히 잡은 상자(짧은 낱말에서 IoU가 떨어짐)
                out.append(("loose", cover[1], [cover[2]], cer(g["text"], cover[2]["text"])))
                continue
            t = key(g["text"])
            c = round(min(1.0, semiglobal(t, key(cover[2]["text"])) / len(t)), 4) if t else None
            out.append(("merged", cover[1], [cover[2]], c))
            continue
        out.append(("missed", max((iou(b, o["bbox_xyxy"]) for o in ocr), default=0.0), [], None))
    return out


def lines_of(parts):
    """같은 line 번호의 부분 상자를 합친 그린 줄."""
    groups = {}
    for p in parts:
        groups.setdefault(p["line"], []).append(p)
    return [{"line": n, "text": " ".join(p["text"] for p in ps), "bbox": [round(v, 1) for v in union_box([p["bbox"] for p in ps])],
             "field": "+".join(sorted({p["field"] for p in ps}))} for n, ps in groups.items()]


def r4(value):
    return None if value is None else round(value, 4)


def stats(rows):
    n = len(rows)
    c = Counter(r["status"] for r in rows)
    matched = [r["cer"] for r in rows if r["status"] == "match" and r["cer"] is not None]
    found = [r["cer"] for r in rows if r["status"] != "missed" and r["cer"] is not None]
    return {"n": n, **{s: c[s] for s in STATUSES}, "recall": r4(c["match"] / n) if n else None, "recall_ci95": wilson(c["match"], n),
            "single": r4((c["match"] + c["loose"]) / n) if n else None, "found": r4((n - c["missed"]) / n) if n else None,
            "cer_matched": r4(statistics.fmean(matched)) if matched else None,
            "exact_matched": r4(sum(x == 0 for x in matched) / len(matched)) if matched else None,
            "read_exact": r4(sum(x == 0 for x in matched) / n) if n else None, "cer_found": r4(statistics.fmean(found)) if found else None}


def grouped(rows, order, by="field"):
    names = [f for f in order if any(r[by] == f for r in rows)] + sorted({r[by] for r in rows} - set(order))
    return {name: stats([r for r in rows if r[by] == name]) for name in names}


def row_of(image, s, g, res):
    status, v, recs, c = res
    return {"image": image, "set": s["id"], "template": s["poster"]["template"], "line": g["line"], "field": g["field"], "text": g["text"],
            "bbox": g["bbox"], "status": status, "iou": round(v, 4), "ocr": [o["text"] for o in recs], "cer": c}


def load_ocr(path, size):
    payload = sc.load(path)
    nw, nh = payload.get("normalized_dimensions") or size
    sx, sy = size[0] / nw, size[1] / nh  # rgb_1x는 원본 좌표라 1.0
    return payload, [{**r, "bbox_xyxy": [r["bbox_xyxy"][0] * sx, r["bbox_xyxy"][1] * sy, r["bbox_xyxy"][2] * sx, r["bbox_xyxy"][3] * sy]}
                     for r in payload["records"]]


def detect(boxes, specs, runs, sha):
    """종류(포스터·문서)마다 부분 상자·그린 줄 단위 검출 지표와 상자별 행."""
    by_id = {s["id"]: s for s in specs}
    out = {}
    for kind, suffix, size, order in (("poster", "poster", S.POSTER_SIZE, POSTER_FIELDS), ("document", "doc", S.DOC_SIZE, DOC_FIELDS)):
        rows, line_rows, skipped, junk, n_ocr, images = [], [], [], Counter(), 0, 0
        for set_id, entry in boxes.items():
            if kind not in entry:
                continue
            case, s = f"{set_id}-{suffix}", by_id[set_id]
            path = runs / case / f"{sc.VARIANT}.json"
            if not path.exists():
                skipped.append({"image": case, "reason": "OCR 결과 파일 없음"})
                continue
            payload, ocr = load_ocr(path, size)
            if payload.get("source_sha256") and payload["source_sha256"] != sha[case]:
                skipped.append({"image": case, "reason": "OCR 입력 이미지 sha256이 manifest와 다름"})
                continue
            images += 1
            n_ocr += len(ocr)
            parts = entry[kind]
            rows += [row_of(case, s, g, res) for g, res in zip(parts, assign(parts, ocr))]
            lines = lines_of(parts)
            line_rows += [row_of(case, s, g, res) for g, res in zip(lines, assign(lines, ocr))]
            for o in ocr:
                b = o["bbox_xyxy"]
                if (area(b) and sum(inter(b, g["bbox"]) for g in parts) / area(b) < .5
                        and not any(inter(b, g["bbox"]) / max(area(g["bbox"]), 1e-9) >= .5 for g in parts)):
                    junk[o["text"]] += 1
        out[kind] = {"product_relevant": kind == "poster",
                     "note": "포스터 이미지 OCR(제품 대상)" if kind == "poster" else "기준 문서 이미지 OCR — 제품은 문서를 글자로 받으므로 제품과 관계없음(참고용)",
                     "images": images, "ocr_boxes": n_ocr, "unexplained_ocr_boxes": sum(junk.values()),
                     "unexplained_top": [[t, n] for t, n in junk.most_common(12)],
                     "overall": stats(rows), "checked_fields": stats([r for r in rows if r["field"] in CHECKED]), "by_field": grouped(rows, order),
                     "by_template": {str(t): v for t, v in grouped(rows, range(5), "template").items()},
                     "by_template_field": {str(t): grouped([r for r in rows if r["template"] == t], order) for t in sorted({r["template"] for r in rows})},
                     "lines": {"overall": stats(line_rows), "by_field": grouped(line_rows, [])},
                     "skipped": skipped, "rows": rows, "line_rows": line_rows}
    return out


# ------------------------------------------------------------------ 겹쳐 그리기(가상 포스터만)
def overlay(img, rows, ocr, path):
    w, h = img.size
    canvas = Image.new("RGB", (w, h + 56), (255, 255, 255))
    canvas.paste(img.convert("RGB"), (0, 0))
    d = ImageDraw.Draw(canvas)
    for o in ocr:
        d.rectangle(o["bbox_xyxy"], outline=(160, 160, 160), width=1)
    small = S.font("malgun", 13)
    for r in rows:
        color = COLORS[r["status"]]
        d.rectangle(r["bbox"], outline=color, width=2)
        at = (r["bbox"][0], max(0, r["bbox"][1] - 16))
        d.rectangle(d.textbbox(at, r["field"], font=small), fill=(255, 255, 255))
        d.text(at, r["field"], font=small, fill=color)
    x, legend = 16, S.font("malgunbd", 17)
    for status in STATUSES:
        d.rectangle([x, h + 18, x + 18, h + 36], outline=COLORS[status], width=3)
        d.text((x + 26, h + 15), STATUS_LABEL[status], font=legend, fill=(30, 30, 30))
        x += 40 + int(d.textlength(STATUS_LABEL[status], font=legend))
    d.rectangle([x, h + 18, x + 18, h + 36], outline=(160, 160, 160), width=1)
    d.text((x + 26, h + 15), "OCR 상자(rgb_1x)", font=legend, fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


# ------------------------------------------------------------------ 세트 하나
def rel(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def font_hashes(used):
    out = {}
    for name in sorted(set(FONT_FILES) | {u for u in used if u}):
        path = S.FONTS / name
        out[name] = {"sha256": S.sha256(path), "bytes": path.stat().st_size, "used": name in used} if path.exists() else None
    return out


def runs_for(name, runtime=None):
    """sim_check의 RUNS 규칙: v1은 sim/runs/paddleocr, 그 밖은 sim-<세트>/runs/paddleocr."""
    base = sc.RUNTIME if runtime is None else Path(runtime) / "sim"
    return (base if name == "v1" else base.with_name(f"sim-{name}")) / sc.RUNS.relative_to(sc.RUNTIME)


def headline(sec):
    o = sec["overall"]
    return {"images": sec["images"], "boxes": o["n"], "recall": o["recall"], "recall_ci95": o["recall_ci95"], "single": o["single"], "found": o["found"],
            "cer_matched": o["cer_matched"], "read_exact": o["read_exact"], "line_recall": sec["lines"]["overall"]["recall"],
            "checked_fields_recall": sec["checked_fields"]["recall"], "checked_fields_found": sec["checked_fields"]["found"],
            "product_relevant": sec["product_relevant"]}


def build_set(name, sim_dir, runs, out_dir, overlays=0, offset=0):
    sim_dir, out_dir = Path(sim_dir), Path(out_dir) / name
    meta = sc.load(sim_dir / "manifest.json")
    stored = sc.load(sim_dir / "sets.json")["sets"]
    seed, shuffle = meta["seed"], bool(meta.get("shuffle_pairs"))
    specs = S.build_specs(seed, shuffle)
    plain = lambda s: json.loads(json.dumps({k: v for k, v in s.items() if k != "files"}, ensure_ascii=False))  # 튜플→목록
    if len(specs) != len(stored) or any(plain(a) != plain(b) for a, b in zip(specs, stored)):
        raise SystemExit(f"{name}: sets.json이 생성기(build_specs) 결과와 다릅니다. 상자를 만들지 않습니다.")
    sha = {c["id"]: c["sha256"] for c in meta["cases"]}
    boxes, checked, ok, mismatched, counts, fonts, ink_iou = {}, Counter(), Counter(), [], Counter(), set(), []
    for k, s in enumerate(specs):
        entry = {}
        for kind, suffix in (("poster", "poster"), ("document", "doc")):
            case = f"{s['id']}-{suffix}"
            if case not in sha:
                continue
            img, rec = render(s, k, seed, kind)
            checked[kind] += 1
            if png_sha(img) != sha[case]:
                mismatched.append(case)
                continue
            ok[kind] += 1
            counts.update(rec.counts)
            fonts |= rec.fonts
            ink_iou += [iou(p["bbox"], p["ink"]) for p in rec.parts if p["ink"]]
            entry[kind] = rec.parts
        if entry:
            boxes[s["id"]] = entry
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "boxes.json").write_text(json.dumps(boxes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    det = detect(boxes, specs, Path(runs), sha)
    det = {"set": name, "engine": "paddleocr", "variant": sc.VARIANT, "runs": rel(runs), "iou_threshold": IOU, "definitions": DEFINITIONS, **det}
    (out_dir / "detection.json").write_text(json.dumps(det, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    written = []
    if overlays:
        by_id = {s["id"]: (k, s) for k, s in enumerate(specs)}
        rows = det["poster"]["rows"]
        for t in [(offset + i) % 5 for i in range(overlays)]:
            pick = next((sid for sid in boxes if "poster" in boxes[sid] and by_id[sid][1]["poster"]["template"] == t
                         and any(r["set"] == sid for r in rows) and sid not in {w.split("-")[0] for w in written}), None)
            if not pick:
                continue
            k, s = by_id[pick]
            path = out_dir / "overlays" / f"{pick}-poster-t{t}.png"
            overlay(S.poster_image(s["poster"], seed + k), [r for r in rows if r["set"] == pick],
                    load_ocr(Path(runs) / f"{pick}-poster" / f"{sc.VARIANT}.json", S.POSTER_SIZE)[1], path)
            written.append(f"{pick}-poster-t{t}.png")

    def box_counts(kind, order):
        per = [len(e[kind]) for e in boxes.values() if kind in e]
        parts = [p for e in boxes.values() for p in e.get(kind, [])]
        fields = Counter(p["field"] for p in parts)
        return {"images": len(per), "total": len(parts), "per_image_min_max": [min(per), max(per)] if per else None,
                "by_field": {f: fields[f] for f in [*order, *sorted(set(fields) - set(order))] if fields[f]},
                "split_parts": sum(p["split"] for p in parts), "clipped": sum(p["clipped"] for p in parts)}

    summary = {"set": name, "dataset_id": meta.get("dataset_id"), "seed": seed, "shuffle_pairs": shuffle, "sets": len(specs),
               "generator": "app/poster_ocr/sim_data.py를 고치지 않고 다시 그림(ImageDraw.text 기록)", "spec_identical": True,
               "sha256_check": {"poster": f"{ok['poster']}/{checked['poster']}", "document": f"{ok['document']}/{checked['document']}",
                                "mismatched": mismatched, "rule": "다시 그린 PNG(optimize=True)의 sha256이 manifest와 같은 이미지만 상자를 저장"},
               "boxes": {"poster": box_counts("poster", POSTER_FIELDS), "document": box_counts("document", DOC_FIELDS)},
               "excluded": {"glow_rgba_draws": counts["glow"], "empty_strings": counts["empty"], "punct_only_parts": counts["punct"]},
               "bbox_vs_ink_iou": {"mean": r4(statistics.fmean(ink_iou)) if ink_iou else None, "min": r4(min(ink_iou)) if ink_iou else None},
               "templates": {"count": 5, "rule": "template = s % 5 (v1·v2 공통)",
                             "sets_per_template": dict(sorted(Counter(str(s["poster"]["template"]) for s in specs).items()))},
               "environment": {"python": platform.python_version(), "pillow": PIL.__version__, "freetype": features.version("freetype2"),
                               "raqm": bool(features.check("raqm")), "fonts_dir": S.FONTS.as_posix()},
               "fonts": font_hashes(fonts),
               "detection": {"engine": "paddleocr", "variant": sc.VARIANT, "runs": rel(runs), "iou_threshold": IOU,
                             "poster": headline(det["poster"]), "document": headline(det["document"])},
               "files": {"boxes": "boxes.json", "detection": "detection.json", "overlays": [f"overlays/{w}" for w in written]},
               "notes": NOTES}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary, det


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="세트를 다시 그려 상자(boxes.json)·요약(summary.json)·검출 대조(detection.json)를 쓴다")
    b.add_argument("--set", nargs="+", default=["v1", "v2"], dest="sets", help="세트 이름(data/synthetic/poster-check-<이름>)")
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--synthetic", type=Path, default=SYNTH, help="poster-check-<이름> 폴더들이 있는 곳")
    b.add_argument("--runtime", type=Path, help="OCR 결과 루트(기본: sim_check.RUNTIME의 부모, data/runtime/poster-ocr)")
    b.add_argument("--overlays", type=int, default=3, help="세트마다 확인용으로 상자를 겹쳐 그린 가상 포스터 PNG 수(0이면 쓰지 않음)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    for i, name in enumerate(args.sets):
        runs = runs_for(name, args.runtime)
        summary, det = build_set(name, args.synthetic / f"poster-check-{name}", runs, args.out, args.overlays, offset=i * args.overlays)
        sha, bx, p, d = summary["sha256_check"], summary["boxes"], det["poster"], det["document"]
        print(f"[{name}] sha256 일치 포스터 {sha['poster']} · 문서 {sha['document']} → 상자 포스터 {bx['poster']['total']}개 · 문서 {bx['document']['total']}개 "
              f"(빛 번짐 제외 {summary['excluded']['glow_rgba_draws']}, 빈 문자열 제외 {summary['excluded']['empty_strings']})")
        for label, sec in (("포스터", p), ("문서(제품과 무관)", d)):
            o = sec["overall"]
            if o["n"]:
                print(f"  {label} 검출 {sec['images']}장: 재현율(IoU≥0.5) {o['recall']:.1%} · 한 상자로 찾음 {o['single']:.1%} · 찾음 {o['found']:.1%} "
                      f"· 일치 상자 CER {o['cer_matched']} · 줄 단위 재현율 {sec['lines']['overall']['recall']:.1%}")
        print("  포스터 항목별 재현율/한 상자: " + " · ".join(f"{f} {v['recall']:.0%}/{v['single']:.0%}" for f, v in p["by_field"].items()))
        print(f"  → {rel(args.out / name)}")


if __name__ == "__main__":
    main()
