"""가상 검수 세트 생성: 가상 축제 포스터 40장 + 기준 문서(디자인 용역 표준계약서 전문·첨부) 40장.

세트 20개는 포스터가 문서와 일치하고, 20개는 포스터 한 칸이 문서와 다르다(오류 유형별로 나눔).
문서와 포스터는 모두 이미지(PNG)이며 PaddleOCR로 읽어 검수한다. 실제 지명·기관·전화번호를 쓰지 않는다(국번 000).
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_data
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/synthetic/poster-check-v1"
SEED = 20261020
FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
POSTER_SIZE = (886, 1254)   # TourAPI 포스터(약 443×627)의 2배와 같은 크기
DOC_SIZE = (1240, 1754)     # A4 150dpi
WEEKDAY = "월화수목금토일"

REGIONS = [("한빛시", "한빛", "031"), ("솔내군", "솔내", "033"), ("가람시", "가람", "041"), ("누리군", "누리", "043"), ("바다울시", "바다울", "051"),
           ("새봄군", "새봄", "054"), ("은하시", "은하", "055"), ("다온군", "다온", "061"), ("미르시", "미르", "063"), ("하람군", "하람", "064")]
# (주제, 슬로건, 프로그램, 팔레트: 배경1, 배경2, 강조, 글자, 띠)
THEMES = [
    ("별빛정원 축제", "빛으로 물드는 가을밤", ["빛 터널 산책", "야간 정원 투어", "별자리 체험"], ("#0d1b3d", "#2b2f77", "#ffd166", "#ffffff", "#0a1430")),
    ("국화향 축제", "가을 향기 가득한 하루", ["국화 분재 전시", "꽃차 시음", "가을 음악회"], ("#fff3d6", "#ffd27a", "#c2410c", "#3b2412", "#7c2d12")),
    ("강변 재즈 페스티벌", "강바람에 실려 오는 선율", ["재즈 빅밴드 공연", "강변 피크닉존", "버스킹 무대"], ("#08324a", "#0f6b8f", "#ffb703", "#ffffff", "#062433")),
    ("전통시장 먹거리 대축제", "맛있는 골목, 즐거운 장터", ["향토 음식 경연", "장터 노래자랑", "할인 장보기"], ("#fff1e6", "#ffb38a", "#d62828", "#2b1b12", "#9a031e")),
    ("단풍 걷기 축제", "붉게 물든 길을 함께 걸어요", ["단풍길 걷기", "숲 해설 투어", "가족 사진 공모"], ("#fde8d7", "#f4a261", "#9c2c13", "#3a1c0f", "#6b2412")),
    ("청년 버스킹 페스티벌", "거리 위의 작은 무대", ["청년 밴드 공연", "댄스 경연", "플리마켓"], ("#1b1b1f", "#3d2c8d", "#ff4d8d", "#ffffff", "#111114")),
    ("한지 공예 문화제", "손끝에서 피어나는 전통", ["한지 등 만들기", "공예 장인 시연", "전통 놀이 마당"], ("#f6efe2", "#e3d3b5", "#8a5a2b", "#2f2418", "#5b3a1e")),
    ("해맞이 불꽃 축제", "밤하늘을 수놓는 불꽃", ["불꽃 쇼", "소원 등 날리기", "해변 콘서트"], ("#120a2e", "#4a1c6b", "#ff9f1c", "#ffffff", "#0b061d")),
    ("가을 책 축제", "책장을 넘기는 가을", ["작가와의 만남", "헌책 장터", "어린이 낭독회"], ("#eef6f0", "#b9e0c6", "#2d6a4f", "#1b3326", "#1b4332")),
    ("갯벌 생태 체험축제", "바다가 준 선물", ["갯벌 생물 관찰", "조개 캐기 체험", "생태 해설"], ("#e6f4f8", "#9ed6e6", "#00708a", "#0b2b33", "#04495a")),
]
ORGANIZERS = ["{s}문화재단", "{s}관광재단", "{s}축제위원회", "{s}문화원"]
VENUES = ["{s}강변공원 일원", "{s} 문화예술회관 야외광장", "{s}{hall} 앞 광장", "{s} 전통시장 일대", "{s}생태공원", "{s}종합운동장"]
TIMES = [("10:00", "18:00"), ("10:00", "21:00"), ("11:00", "20:00"), ("13:00", "22:00"), ("18:00", "22:00")]
STUDIOS = ["스튜디오 하늘결", "디자인랩 솔가지", "그래픽공방 너울", "스튜디오 온새미", "디자인하우스 가온길"]
PEOPLE = ["김가온", "이하람", "박누리", "최다솜", "정한결", "윤새벽", "강마루", "조은솔"]
MISMATCH_TYPES = (["date_shift"] * 2 + ["date_shift_stale_weekday"] * 2 + ["edition_swap"] * 3 + ["venue_diff"] * 3 + ["title_typo"] * 3
                  + ["organizer_diff"] * 3 + ["contact_diff"] * 2 + ["time_diff"] * 2)
TYPE_LABEL = {"date_shift": "행사 기간 하루 밀림(요일도 다시 계산)", "date_shift_stale_weekday": "행사 기간 하루 밀림(요일은 그대로 → 달력과 어긋남)",
              "edition_swap": "회차 숫자 뒤바뀜", "venue_diff": "행사 장소 다름", "title_typo": "행사명 한 글자 오타",
              "organizer_diff": "주관 기관 다름", "contact_diff": "문의 전화번호 한 자리 다름", "time_diff": "운영 시간 다름"}
TYPE_FIELD = {"date_shift": "schedule", "date_shift_stale_weekday": "schedule", "edition_swap": "edition", "venue_diff": "venue",
              "title_typo": "title", "organizer_diff": "organizer", "contact_diff": "contact", "time_diff": "time"}


@lru_cache(maxsize=None)
def font(face, size, weight=None):
    files = {"sans": "NotoSansKR-VF.ttf", "serif": "NotoSerifKR-VF.ttf", "malgun": "malgun.ttf", "malgunbd": "malgunbd.ttf"}
    f = ImageFont.truetype(str(FONTS / files[face]), size)
    if weight:
        f.set_variation_by_name(weight)
    return f


def hexrgb(value, alpha=None):
    value = value.lstrip("#")
    rgb = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + (alpha,) if alpha is not None else rgb


def text_w(draw, text, f, stroke=0):
    box = draw.textbbox((0, 0), text, font=f, stroke_width=stroke)
    return box[2] - box[0]


def fit_font(draw, text, face, weight, start, max_w, minimum=24):
    size = start
    while size > minimum and text_w(draw, text, font(face, size, weight)) > max_w:
        size -= 2
    return font(face, size, weight)


def center(draw, y, text, f, fill, width, stroke=0, stroke_fill=None):
    w = text_w(draw, text, f, stroke)
    draw.text(((width - w) / 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    box = draw.textbbox(((width - w) / 2, y), text, font=f, stroke_width=stroke)
    return box[3]


# ------------------------------------------------------------------ 값 만들기
def weekday(d):
    return WEEKDAY[d.weekday()]


def date_text(start, end, style, weekdays=None):
    """weekdays: (시작 요일, 종료 요일)을 직접 줄 때(달력과 다르게 찍힌 포스터)."""
    ws, we = weekdays or (weekday(start), weekday(end))
    if style == "doc" or style == "dot":
        head = f"{start.year}. {start.month}. {start.day}.({ws})"
        if start == end:
            return head
        return f"{head} ~ {end.month}. {end.day}.({we})"
    if style == "compact":
        head = f"{start.year}.{start.month:02d}.{start.day:02d}({ws})"
        return head if start == end else f"{head} - {end.month:02d}.{end.day:02d}({we})"
    if style == "korean":
        head = f"{start.month}월 {start.day}일({ws})"
        if start == end:
            return head
        tail = f"{end.day}일({we})" if end.month == start.month else f"{end.month}월 {end.day}일({we})"
        return f"{head} ~ {tail}"
    raise ValueError(style)


def typo(word, rng):
    """한 음절의 받침을 바꿔 그럴듯한 오타를 만든다(예: 빛→빗)."""
    idx = [i for i, ch in enumerate(word) if "가" <= ch <= "힣"]
    i = idx[1] if len(idx) > 1 else idx[0]
    code = ord(word[i]) - 0xAC00
    cho, jung, jong = code // 588, (code % 588) // 28, code % 28
    options = [j for j in (0, 4, 8, 16, 19, 21) if j != jong]
    new = chr(0xAC00 + cho * 588 + jung * 28 + rng.choice(options))
    return word[:i] + new + word[i + 1:]


def build_specs(seed=SEED, shuffle_pairs=False):
    rng = random.Random(seed)
    pairs, seen = [], set()
    for s in range(40):
        r, t = s % 10, (s * 3 + s // 10) % 10
        assert (r, t) not in seen
        seen.add((r, t))
        pairs.append((r, t))
    if shuffle_pairs:  # 지역·주제 짝과 세트 순서(→ 디자인 틀·날짜 표기)도 섞는다. 지역·주제·틀별 개수는 그대로, 값 난수(rng)는 건드리지 않는다
        mix = random.Random(f"pairs-{seed}")
        rmap, tmap = mix.sample(range(10), 10), mix.sample(range(10), 10)
        pairs = [(rmap[r], tmap[t]) for r, t in pairs]
        mix.shuffle(pairs)
    mismatched = sorted(rng.sample(range(40), 20))
    types = MISMATCH_TYPES[:]
    rng.shuffle(types)
    kind = {i: types[k] for k, i in enumerate(mismatched)}
    sets = []
    for s, (r, t) in enumerate(pairs):
        region, short, area = REGIONS[r]
        theme, slogan, programs, palette = THEMES[t]
        mtype = kind.get(s)
        hall = "시청" if region.endswith("시") else "군청"
        if mtype == "edition_swap":
            while True:
                edition = rng.randint(12, 89)
                if edition // 10 != edition % 10 and edition % 10:
                    break
        else:
            edition = rng.randint(2, 35) if rng.random() < .7 else None
        start = date(2026, 9, 5) + timedelta(days=rng.randint(0, 98))
        end = start + timedelta(days=rng.choice([0, 1, 2, 2, 3]))
        t_open, t_close = rng.choice(TIMES)
        doc = {
            "region": region, "short": short, "theme": theme, "edition": edition, "year": start.year,
            "title": f"{short} {theme}", "official": (f"제{edition}회 " if edition else "") + f"{short} {theme}",
            "start": start.isoformat(), "end": end.isoformat(), "schedule": date_text(start, end, "doc"),
            "time": f"{t_open} ~ {t_close}", "venue": rng.choice(VENUES).format(s=short, hall=hall),
            "host": region, "organizer": rng.choice(ORGANIZERS).format(s=short),
            "contact": f"{area}-000-{rng.randint(1000, 9999)}", "slogan": slogan,
        }
        poster = {k: v for k, v in doc.items()}
        poster.update(programs=programs, palette=palette, template=s % 5, date_style=["dot", "compact", "korean", "dot", "compact"][s % 5])
        start_p, end_p, weekdays = start, end, None
        change = None
        if mtype in ("date_shift", "date_shift_stale_weekday"):
            start_p, end_p = start + timedelta(days=1), end + timedelta(days=1)
            if mtype == "date_shift_stale_weekday":
                weekdays = (weekday(start), weekday(end))
            change = (doc["schedule"], date_text(start_p, end_p, "doc", weekdays))
        elif mtype == "edition_swap":
            poster["edition"] = int(str(edition)[::-1])
            change = (f"제{edition}회", f"제{poster['edition']}회")
        elif mtype == "venue_diff":
            poster["venue"] = rng.choice([v.format(s=short, hall=hall) for v in VENUES if v.format(s=short, hall=hall) != doc["venue"]])
            change = (doc["venue"], poster["venue"])
        elif mtype == "title_typo":
            words = theme.split()
            poster["theme"] = " ".join([typo(words[0], rng), *words[1:]])
            poster["title"] = f"{short} {poster['theme']}"
            change = (doc["title"], poster["title"])
        elif mtype == "organizer_diff":
            poster["organizer"] = rng.choice([o.format(s=short) for o in ORGANIZERS if o.format(s=short) != doc["organizer"]])
            change = (doc["organizer"], poster["organizer"])
        elif mtype == "contact_diff":
            last = doc["contact"][-4:]
            digit = str((int(last[2]) + rng.randint(1, 8)) % 10)
            poster["contact"] = doc["contact"][:-2] + digit + last[3]
            change = (doc["contact"], poster["contact"])
        elif mtype == "time_diff":
            o, c = doc["time"].split(" ~ ")
            c2 = f"{(int(c[:2]) + 1) % 24:02d}:00" if int(c[:2]) < 23 else "22:30"
            poster["time"] = f"{o} ~ {c2}"
            change = (doc["time"], poster["time"])
        poster["official"] = (f"제{poster['edition']}회 " if poster["edition"] else "") + poster["title"]  # 바뀐 회차·행사명 반영
        poster["schedule"] = date_text(start_p, end_p, poster["date_style"], weekdays)
        poster["start"], poster["end"] = start_p.isoformat(), end_p.isoformat()
        sets.append({
            "id": f"S{s + 1:02d}", "label": "mismatch" if mtype else "match",
            "mismatch": {"type": mtype, "field": TYPE_FIELD[mtype], "description": TYPE_LABEL[mtype], "document": change[0], "poster": change[1]} if mtype else None,
            "document": doc, "poster": poster,
            "contract": {
                "studio": rng.choice(STUDIOS), "client": f"{region} 문화관광과", "client_rep": rng.choice(PEOPLE), "studio_rep": rng.choice(PEOPLE),
                "amount": rng.choice([3_500_000, 4_800_000, 6_000_000, 7_200_000, 9_500_000]), "drafts": rng.choice([2, 3]),
                "signed": (start - timedelta(days=rng.randint(45, 70))).isoformat(),
                "period_start": (start - timedelta(days=rng.randint(40, 44))).isoformat(), "period_end": (start - timedelta(days=rng.randint(14, 20))).isoformat(),
            },
        })
    return sets


# ------------------------------------------------------------------ 포스터
def gradient(size, c1, c2):
    w, h = size
    top, bottom = hexrgb(c1), hexrgb(c2)
    base = Image.new("RGB", size)
    px = ImageDraw.Draw(base)
    for y in range(h):
        t = y / (h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return base


def overlay_shapes(img, rng, color, kind, count):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = img.size
    for _ in range(count):
        x, y, r = rng.randint(-80, w), rng.randint(-80, int(h * .7)), rng.randint(20, 150)
        alpha = rng.randint(28, 70)
        if kind == "circle":
            d.ellipse([x, y, x + r, y + r], fill=hexrgb(color, alpha))
        elif kind == "star":
            s = rng.randint(2, 4)
            d.ellipse([x, y, x + s, y + s], fill=(255, 255, 255, rng.randint(90, 200)))
        elif kind == "triangle":
            d.polygon([(x, y), (x + r, y + r // 2), (x, y + r)], fill=hexrgb(color, alpha))
    if kind == "circle":
        layer = layer.filter(ImageFilter.GaussianBlur(2))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def info_lines(p):
    return [("일시", p["schedule"]), ("시간", p["time"].replace(" ~ ", " - ") if p["template"] in (1, 4) else p["time"]), ("장소", p["venue"])]


def org_band(draw, p, y, width, fill, color):
    draw.rectangle([0, y, width, POSTER_SIZE[1]], fill=fill)
    text = f"주최  {p['host']}   |   주관  {p['organizer']}   |   문의  {p['contact']}"
    f = fit_font(draw, text, "sans", "Medium", 22, width - 60, 16)
    center(draw, y + (POSTER_SIZE[1] - y - f.size) / 2 - 4, text, f, color, width)


def title_lines(draw, p, max_w, start, face="sans", weight="Black"):
    whole = p["title"]
    f = font(face, start, weight)
    if text_w(draw, whole, f) <= max_w:
        return [whole], f
    lines = [p["short"], p["theme"]]
    f = fit_font(draw, max(lines, key=lambda s: text_w(draw, s, font(face, start, weight))), face, weight, start, max_w, 40)
    return lines, f


def edition_line(p):
    return f"{p['year']} 제{p['edition']}회" if p["edition"] else f"{p['year']}"


def poster_image(p, seed):
    rng = random.Random(seed)
    w, h = POSTER_SIZE
    bg1, bg2, accent, ink, band = p["palette"]
    t = p["template"]
    if t == 0:  # 그라데이션 중앙
        img = overlay_shapes(gradient((w, h), bg1, bg2), rng, accent, "circle", 18)
        d = ImageDraw.Draw(img)
        y = center(d, 90, edition_line(p), font("sans", 34, "Bold"), accent, w) + 30
        lines, f = title_lines(d, p, w - 120, 112)
        for line in lines:
            y = center(d, y, line, f, ink, w, stroke=2, stroke_fill=band) + 14
        y = center(d, y + 18, p["slogan"], font("serif", 34, "SemiBold"), ink, w) + 50
        box = [70, y, w - 70, y + 230]
        d.rounded_rectangle(box, 24, fill=(255, 255, 255))
        yy = y + 32
        for label, value in info_lines(p):
            d.text((110, yy), label, font=font("sans", 28, "Bold"), fill=hexrgb(band))
            d.text((210, yy), value, font=fit_font(d, value, "sans", "Medium", 30, w - 320, 20), fill=(30, 30, 30))
            yy += 62
        yy = y + 270
        for prog in p["programs"]:
            tw = text_w(d, prog, font("sans", 24, "Medium"))
            d.rounded_rectangle([(w - tw) / 2 - 22, yy, (w + tw) / 2 + 22, yy + 46], 23, outline=hexrgb(ink), width=2)
            center(d, yy + 7, prog, font("sans", 24, "Medium"), ink, w)
            yy += 58
        org_band(d, p, h - 96, w, hexrgb(band), (255, 255, 255))
    elif t == 1:  # 왼쪽 색 띠
        img = Image.new("RGB", (w, h), hexrgb(bg2))
        img = overlay_shapes(img, rng, bg1, "triangle", 14)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 40, h], fill=hexrgb(accent))
        d.text((90, 110), edition_line(p), font=font("sans", 36, "Bold"), fill=hexrgb(band))
        lines, f = title_lines(d, p, w - 160, 104)
        y = 170
        for line in lines:
            d.text((86, y), line, font=f, fill=hexrgb(ink))
            y = d.textbbox((86, y), line, font=f)[3] + 16
        d.text((90, y + 20), p["slogan"], font=font("sans", 32, "Medium"), fill=hexrgb(band))
        y += 120
        d.line([(90, y), (w - 90, y)], fill=hexrgb(band), width=3)
        y += 30
        for label, value in info_lines(p):
            d.rectangle([90, y + 4, 182, y + 44], fill=hexrgb(band))
            d.text((106, y + 6), label, font=font("sans", 26, "Bold"), fill=(255, 255, 255))
            d.text((200, y + 3), value, font=fit_font(d, value, "sans", "Bold", 32, w - 290, 20), fill=hexrgb(ink))
            y += 70
        y += 30
        for prog in p["programs"]:
            d.text((96, y), "· " + prog, font=font("sans", 26, "Regular"), fill=hexrgb(ink))
            y += 44
        org_band(d, p, h - 90, w, (255, 255, 255), hexrgb(band))
    elif t == 2:  # 밤하늘
        img = gradient((w, h), "#0b1026", bg2)
        img = overlay_shapes(img, rng, accent, "star", 110)
        d = ImageDraw.Draw(img)
        d.ellipse([w - 300, 70, w - 120, 250], fill=hexrgb(accent))
        d.ellipse([w - 270, 60, w - 100, 230], fill=hexrgb("#0b1026"))
        y = center(d, 300, edition_line(p), font("serif", 34, "Bold"), accent, w) + 24
        lines, f = title_lines(d, p, w - 120, 116, "serif", "Black")
        for line in lines:
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).text(((w - text_w(d, line, f)) / 2, y), line, font=f, fill=hexrgb(accent, 160))
            img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(9))).convert("RGB")
            d = ImageDraw.Draw(img)
            y = center(d, y, line, f, (255, 255, 255), w) + 16
        y = center(d, y + 20, p["slogan"], font("sans", 32, "Light"), (230, 230, 255), w) + 70
        y = center(d, y, p["schedule"], fit_font(d, p["schedule"], "sans", "Bold", 40, w - 120, 24), accent, w) + 22
        y = center(d, y, p["time"], font("sans", 32, "Medium"), (255, 255, 255), w) + 22
        y = center(d, y, p["venue"], fit_font(d, p["venue"], "sans", "Medium", 34, w - 120, 22), (255, 255, 255), w) + 50
        center(d, y, "  ·  ".join(p["programs"]), fit_font(d, "  ·  ".join(p["programs"]), "sans", "Regular", 24, w - 120, 16), (200, 200, 235), w)
        org_band(d, p, h - 90, w, hexrgb("#070a1a"), (220, 220, 240))
    elif t == 3:  # 위 그림 + 아래 정보
        img = gradient((w, int(h * .62)), bg1, bg2)
        canvas = Image.new("RGB", (w, h), (255, 255, 255))
        canvas.paste(img, (0, 0))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        base = int(h * .62)
        for k in range(4):
            pts = [(0, base)] + [(x, base - 60 - k * 45 - int(50 * ((x * (k + 3)) % 97) / 97)) for x in range(0, w + 60, 60)] + [(w, base)]
            dl.polygon(pts, fill=hexrgb(accent, 50 + k * 30))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")
        d = ImageDraw.Draw(canvas)
        y = center(d, 70, edition_line(p), font("sans", 34, "Bold"), ink, w) + 20
        lines, f = title_lines(d, p, w - 110, 110)
        for line in lines:
            y = center(d, y, line, f, ink, w) + 12
        center(d, y + 16, p["slogan"], font("serif", 32, "Bold"), band, w)
        y = base + 40
        for label, value in info_lines(p):
            d.text((90, y), label, font=font("sans", 28, "Black"), fill=hexrgb(accent))
            d.text((200, y), value, font=fit_font(d, value, "sans", "Medium", 31, w - 290, 20), fill=(25, 25, 25))
            d.line([(90, y + 52), (w - 90, y + 52)], fill=(220, 220, 220), width=2)
            y += 74
        center(d, y + 14, "  |  ".join(p["programs"]), fit_font(d, "  |  ".join(p["programs"]), "sans", "Regular", 24, w - 120, 16), (70, 70, 70), w)
        org_band(d, p, h - 84, w, hexrgb(band), (255, 255, 255))
        img = canvas
    else:  # 타이포
        img = Image.new("RGB", (w, h), hexrgb(bg1))
        d = ImageDraw.Draw(img)
        mark = f"{p['edition']}" if p["edition"] else f"{p['year']}"[2:]
        d.text((w - 40 - text_w(d, mark, font("sans", 360, "Black")), 20), mark, font=font("sans", 360, "Black"), fill=hexrgb(bg2))
        d.text((70, 120), edition_line(p), font=font("sans", 36, "Bold"), fill=hexrgb(accent))
        words = [p["short"], *p["theme"].split()]
        info_y = h - 86 - 44 - 3 * 52          # 정보 3줄은 아래 띠 위에 고정
        slogan_y = info_y - 74
        f = fit_font(d, max(words, key=len), "sans", "Black", 120, w - 140, 50)
        while f.size > 50 and 300 + len(words) * (f.size * 1.28) > slogan_y - 16:  # 제목이 슬로건·정보와 겹치지 않게 줄인다
            f = font("sans", f.size - 4, "Black")
        y = 300
        for word in words:
            d.text((66, y), word, font=f, fill=hexrgb(ink))
            y = d.textbbox((66, y), word, font=f)[3] + 6
        d.text((72, slogan_y), p["slogan"], font=font("sans", 30, "Medium"), fill=hexrgb(accent))
        y = info_y
        for label, value in info_lines(p):
            line = f"{label}  {value}"
            fl = fit_font(d, line, "sans", "Bold", 30, w - 150, 20)
            d.text((w - 72 - text_w(d, line, fl), y), line, font=fl, fill=hexrgb(ink))
            y += 52
        org_band(d, p, h - 86, w, hexrgb(band), (255, 255, 255))
    return img


# ------------------------------------------------------------------ 기준 문서
def doc_image(s):
    w, h = DOC_SIZE
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    doc, c = s["document"], s["contract"]
    L, R = 80, w - 80
    reg, bold, small = font("malgun", 23), font("malgunbd", 23), font("malgun", 19)
    d.text((L, 60), "[별표]", font=small, fill=(60, 60, 60))
    d.text((R - text_w(d, "산업통상부 고시 제2025-033호 서식", small), 60), "산업통상부 고시 제2025-033호 서식", font=small, fill=(60, 60, 60))
    center(d, 104, "( 시각디자인 ) 디자인 용역 표준계약서(전문)", font("malgunbd", 36), (0, 0, 0), w)

    def table(y, rows, cols, row_h=58, header=None):
        x0 = L
        widths = [int((R - L) * c) for c in cols]
        if header:
            d.rectangle([L, y, R, y + row_h], fill=(238, 241, 245))
            x = x0
            for text, cw in zip(header, widths):
                d.text((x + 16, y + (row_h - 28) / 2), text, font=bold, fill=(20, 20, 20))
                x += cw
            d.rectangle([L, y, R, y + row_h], outline=(0, 0, 0), width=2)
            y += row_h
        for row in rows:
            x = x0
            d.rectangle([L, y, R, y + row_h], outline=(0, 0, 0), width=1)
            for k, (text, cw) in enumerate(zip(row, widths)):
                if k == 0:
                    d.rectangle([x, y, x + cw, y + row_h], fill=(247, 248, 250), outline=(0, 0, 0), width=1)
                f = bold if k == 0 else (small if k == 2 else reg)
                text_fit = text
                while text_w(d, text_fit, f) > cw - 28 and len(text_fit) > 4:
                    text_fit = text_fit[:-1]
                d.text((x + 16, y + (row_h - f.size - 6) / 2), text_fit, font=f, fill=(20, 20, 20))
                if k < len(widths) - 1:
                    d.line([(x + cw, y), (x + cw, y + row_h)], fill=(0, 0, 0), width=1)
                x += cw
            y += row_h
        d.rectangle([L, y - row_h * len(rows), R, y], outline=(0, 0, 0), width=2)
        return y

    ps, pe = date.fromisoformat(c["period_start"]), date.fromisoformat(c["period_end"])
    y = table(170, [
        ("계약명", f"{doc['official']} 홍보 포스터 디자인 용역"),
        ("계약기간", f"{ps.year}년 {ps.month}월 {ps.day}일부터 {pe.year}년 {pe.month}월 {pe.day}일까지"),
        ("계약금액", f"금 {c['amount']:,}원정 (￦{c['amount']:,}, 부가가치세 별도)"),
        ("디자인 분야", "■ 시각디자인   □ 제품디자인   □ 멀티미디어디자인   □ 포장디자인"),
        ("용역 대상", f"{doc['official']} 홍보 포스터 (A2 세로형) 1종"),
        ("디자인 제작", f"■ 시안제작 ({c['drafts']})종   ■ 세부디자인   □ 3D 렌더링"),
    ], [.22, .78])
    d.text((L, y + 34), "첨부 1. 개발 세부내역 - 포스터 필수 표기 사항", font=font("malgunbd", 26), fill=(0, 0, 0))
    y = table(y + 84, [
        ("행사명", doc["official"], "정식 명칭 그대로"),
        ("행사 기간", doc["schedule"], "요일 포함 표기"),
        ("운영 시간", doc["time"], ""),
        ("행사 장소", doc["venue"], ""),
        ("주최", doc["host"], ""),
        ("주관", doc["organizer"], ""),
        ("문의처", doc["contact"], "대표 번호"),
        ("필수 문구", doc["slogan"], "슬로건"),
        ("기관 로고", "주최·주관 기관 로고 하단 배치", "시각 확인"),
    ], [.2, .56, .24], header=("항목", "표기 내용", "비고"))
    d.text((L, y + 26), "※ 위 표기 사항과 다른 결과물은 제7조에 따라 수정·보완을 요구할 수 있다.", font=small, fill=(50, 50, 50))
    signed = date.fromisoformat(c["signed"])
    center(d, y + 92, f"{signed.year}년 {signed.month}월 {signed.day}일", reg, (0, 0, 0), w)
    y = table(y + 150, [
        ("디자인 수요자", f"상호 또는 명칭: {c['client']}    대표자: {c['client_rep']} (인)"),
        ("디자인 공급자", f"상호 또는 명칭: {c['studio']}    대표자: {c['studio_rep']} (인)"),
    ], [.22, .78])
    center(d, h - 70, "- 1 -", small, (90, 90, 90), w)
    return img


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shuffle-pairs", action="store_true", help="지역·주제·디자인 틀 조합도 시드로 섞는다(기본: v1과 같은 조합)")
    args = parser.parse_args()
    args.out = args.out.resolve()  # 상대 경로도 받는다(파일 경로는 저장소 기준으로 기록)
    sets = build_specs(args.seed, args.shuffle_pairs)
    (args.out / "posters").mkdir(parents=True, exist_ok=True)
    (args.out / "documents").mkdir(parents=True, exist_ok=True)
    cases = []
    for k, s in enumerate(sets):
        poster_path, doc_path = args.out / "posters" / f"{s['id']}.png", args.out / "documents" / f"{s['id']}.png"
        poster_image(s["poster"], args.seed + k).save(poster_path, optimize=True)
        doc_image(s).save(doc_path, optimize=True)
        s["files"] = {"poster": poster_path.relative_to(ROOT).as_posix(), "document": doc_path.relative_to(ROOT).as_posix()}
        for kind, path in (("document", doc_path), ("poster", poster_path)):  # 세트마다 문서 → 포스터 순서로 읽는다
            cases.append({"id": f"{s['id']}-{'doc' if kind == 'document' else 'poster'}", "set": s["id"], "kind": kind, "split": "sim",
                          "image": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)})
    meta = {"dataset_id": f"synthetic-{args.out.name}", "seed": args.seed, **({"shuffle_pairs": True} if args.shuffle_pairs else {}), "sets": len(sets),
            "match": sum(s["label"] == "match" for s in sets), "mismatch": sum(s["label"] == "mismatch" for s in sets),
            "note": "가상 축제·기관·전화번호(국번 000). 기준 문서는 디자인 용역 표준계약서(산업통상부 고시 제2025-033호) 전문 양식을 따른다."}
    (args.out / "sets.json").write_text(json.dumps({**meta, "sets": sets}, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "manifest.json").write_text(json.dumps({**meta, "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
