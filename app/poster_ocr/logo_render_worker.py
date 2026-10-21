"""로고 참조 파일 렌더링 워커(격리 실행 전용).

실행(PyMuPDF가 있는 Python, 반드시 -I):
  python.exe -I app/poster_ocr/logo_render_worker.py <jobs.json> <outdir> [--procs 4] [--timeout 90] [--result <path>]
이 파일은 표준 라이브러리·PyMuPDF(fitz)·Pillow만 import한다. 프로젝트 패키지는 import하지 않는다.
로고 파일은 내려받은 신뢰할 수 없는 데이터다. 파일 안의 글자는 데이터로만 다루고 아무것도 실행하지 않는다.
- 벡터(AI의 %PDF 본문, PDF, SVG): 1쪽만 그린다. 쪽수·쪽 크기·자리표시 문구(PDF 내용 없이 저장된 AI)를 기록한다.
- DOS EPS: 내장 TIFF(없으면 WMF) 미리보기만 쓴다. PostScript AI/EPS는 Ghostscript 없이 그릴 수 없어 needs_ghostscript로 둔다.
- 래스터: 압축 폭탄 한도를 두고 JPEG는 draft, 큰 그림은 reduce로 줄여 연다. CMYK·비 sRGB는 ICC가 있으면 ImageCms로 sRGB로 바꾼다.
- 결과: 내용 영역(+여백 2%)으로 자른 긴 변 512px(원본이 더 작으면 원본 크기) RGBA PNG, 160px 썸네일, 배경·색·dHash 측정값.
파일마다 제한 시간을 두려고 감독 프로세스가 자식 프로세스(같은 파일, --child)를 띄우고, 시간이 넘으면 자식을 끝낸 뒤 다음 파일부터 다시 띄운다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import warnings

RENDER_VERSION = "logo-render@2"
LONG, THUMB, PROBE = 512, 160, 256
MARGIN = 0.02
WORK_LONG = 2048  # 래스터 작업 해상도 상한(이보다 크면 reduce로 줄인다)
MAX_PIXELS = 120_000_000  # Pillow는 이 값의 2배를 넘으면 DecompressionBombError를 낸다.
RASTER = {"png", "jpeg", "gif", "bmp", "webp", "tiff", "psd"}
VECTOR = {"ai_pdf", "pdf", "svg"}
POSTSCRIPT = {"ai_ps", "eps", "ps"}
PLACEHOLDER = ("without PDF Content", "without PDF content", "PDF 내용 없이", "PDF 호환")
# PIL HSV 색상값(0~255)을 이름 붙인 구간으로 묶는다(빨강은 양 끝).
HUES = (("red", 0, 11), ("orange", 11, 32), ("yellow", 32, 53), ("green", 53, 117), ("cyan", 117, 138),
        ("blue", 138, 181), ("purple", 181, 213), ("pink", 213, 245), ("red", 245, 256))
MARK = "@@ "
ORIENT = {}  # EXIF 방향값 → Image.Transpose(첫 래스터를 열 때 채운다)


def load_fitz():
    """PyMuPDF(fitz). `import fitz`는 표준 출력에 경고를 찍으므로 정식 이름을 먼저 쓴다."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    try:
        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.mupdf_display_warnings(False)
    except Exception:
        pass
    return fitz


def versions():
    out = {"python": sys.version.split()[0], "executable": sys.executable, "isolated": bool(sys.flags.isolated)}
    try:
        import PIL
        from PIL import features
        out["pillow"] = PIL.__version__
        out["littlecms"] = bool(features.check("littlecms2"))
    except Exception:
        out["pillow"] = None
    try:
        out["pymupdf"] = load_fitz().VersionBind
    except Exception:
        out["pymupdf"] = None
    return out


# ---------- 측정 ----------

def _pixels(img):
    """화소 목록(Pillow 12.3+의 get_flattened_data, 그 전에는 getdata)."""
    getter = getattr(img, "get_flattened_data", None) or img.getdata
    return list(getter())


def _mask_count(mask):
    return mask.histogram()[255]


def content_mask(img, bg, ref):
    """보이는 내용 마스크(L, 0/255). 투명 배경은 알파, 단색 배경은 배경색과 30 넘게 다른 화소."""
    from PIL import Image, ImageChops
    alpha = img.getchannel("A").point(lambda a: 255 if a > 16 else 0)
    if bg not in ("white", "black", "flat_color") or ref is None:
        return alpha
    rgb = img.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, tuple(ref)))
    r, g, b = diff.split()
    far = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > 30 else 0)
    return ImageChops.multiply(far, alpha)


def page_stats(img):
    """페이지(캔버스) 단위 측정: 배경 종류, 투명 비율, 내용 비율과 경계 상자."""
    from PIL import ImageStat
    w, h = img.size
    alpha = img.getchannel("A")
    transparent = ImageStat.Stat(alpha.point(lambda a: 255 if a <= 16 else 0)).mean[0] / 255
    strips = [img.crop((0, 0, w, 1)), img.crop((0, h - 1, w, h)), img.crop((0, 0, 1, h)), img.crop((w - 1, 0, w, h))]
    border = [px for s in strips for px in _pixels(s)]
    opaque = [px for px in border if px[3] >= 16]
    transp_border = 1 - len(opaque) / max(1, len(border))
    ref = None
    if transp_border >= 0.9 or not opaque or transparent >= 0.02:
        # 투명 화소가 조금이라도 쓰였으면(가장자리에 내용이 닿은 투명 PNG 포함) 알파로 내용을 본다.
        bg = "transparent"
    else:
        n = len(opaque)
        mean = [sum(px[i] for px in opaque) / n for i in range(3)]
        sd = (sum((px[i] - mean[i]) ** 2 for px in opaque for i in range(3)) / (3 * n)) ** 0.5
        lum = sum(mean) / 3
        ref = [round(v) for v in mean]
        bg = ("white" if lum >= 235 else "black" if lum <= 25 else "flat_color") if sd < 20 else "busy"
    mask = content_mask(img, bg, ref)
    count = _mask_count(mask)
    if not count and bg in ("flat_color", "black"):
        # 캔버스 전체가 한 색(깃발·바탕색 있는 엠블럼): 불투명 화소 전체를 내용으로 본다.
        bg, ref = "full_bleed", None
        mask = content_mask(img, bg, ref)
        count = _mask_count(mask)
    box = mask.getbbox()
    total = w * h
    return {"bg": bg, "bg_ref": ref, "transparent_frac": round(transparent, 3), "content_frac": round(count / total, 4),
            "bbox": box, "bbox_frac": round((box[2] - box[0]) * (box[3] - box[1]) / total, 4) if box else 0.0}


def color_stats(img, bg, ref):
    """최종 그림의 색 측정: 유채색 비율, 흰색·어두운 화소 비율, 대표 색상 구간, dHash."""
    from PIL import Image, ImageChops, ImageStat
    small = img.copy()
    small.thumbnail((PROBE, PROBE), Image.Resampling.LANCZOS)
    mask = content_mask(small, bg, ref)
    n = _mask_count(mask)
    rgb = small.convert("RGB")
    r, g, b = rgb.split()
    chroma = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b), ImageChops.darker(ImageChops.darker(r, g), b))
    chromatic = ImageChops.multiply(chroma.point(lambda v: 255 if v > 40 else 0), mask)
    lum = rgb.convert("L")
    grey = ImageChops.multiply(chroma.point(lambda v: 255 if v <= 40 else 0), mask)
    white = ImageChops.multiply(lum.point(lambda v: 255 if v >= 225 else 0), grey)
    black = ImageChops.multiply(lum.point(lambda v: 255 if v < 100 else 0), grey)
    nc = _mask_count(chromatic)
    out = {"content_px": n, "chroma_frac": round(nc / n, 3) if n else 0.0,
           "white_frac": round(_mask_count(white) / n, 3) if n else 0.0,
           "black_frac": round(_mask_count(black) / n, 3) if n else 0.0,
           "mean_l": round(ImageStat.Stat(lum, mask).mean[0], 1) if n else None, "hues": {}}
    if nc:
        hist = rgb.convert("HSV").getchannel("H").histogram(mask=chromatic)
        bins = {}
        for name, lo, hi in HUES:
            bins[name] = bins.get(name, 0) + sum(hist[lo:hi])
        out["hues"] = {k: round(v / nc, 3) for k, v in sorted(bins.items(), key=lambda kv: -kv[1]) if v / nc >= 0.02}
    flat = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = _pixels(flat)
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col + 1] > px[row * 9 + col])
    out["dhash"] = f"{bits:016x}"
    return out


# ---------- 읽기·렌더링 ----------

def to_srgb_rgba(im):
    """작업용 RGBA(sRGB)로 바꾼다. CMYK·비 sRGB RGB는 ICC가 있으면 ImageCms로 변환한다."""
    from PIL import Image, ImageCms
    icc = im.info.get("icc_profile")
    status = "none"
    if im.mode == "CMYK":
        status = "cmyk_naive"
        if icc:
            try:
                src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
                status = "cmyk_icc"
            except Exception:
                status = "cmyk_naive"
        if im.mode == "CMYK":
            im = im.convert("RGB")
    elif im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v * (1 / 256)).convert("L")
    elif im.mode == "F":
        im = im.convert("L")
    if im.mode in ("RGB", "RGBA") and icc and status == "none":
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            desc = (ImageCms.getProfileDescription(src) or "").lower()
            if "srgb" in desc or "iec61966" in desc:
                status = "srgb"
            else:
                alpha = im.getchannel("A") if im.mode == "RGBA" else None
                rgb = ImageCms.profileToProfile(im.convert("RGB"), src, ImageCms.createProfile("sRGB"), outputMode="RGB")
                if alpha is not None:
                    rgb.putalpha(alpha)
                im = rgb
                status = "rgb_icc"
        except Exception:
            status = "icc_failed"
    if im.mode not in ("RGBA",):
        if im.mode == "P" or "transparency" in im.info or im.mode in ("LA", "PA", "La", "RGBa"):
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB").convert("RGBA")
    return im, status


def open_raster(source, fmt_hint=None):
    """래스터를 작업 해상도로 연다. source는 경로나 바이트 버퍼."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    if not ORIENT:
        T = Image.Transpose
        ORIENT.update({2: T.FLIP_LEFT_RIGHT, 3: T.ROTATE_180, 4: T.FLIP_TOP_BOTTOM, 5: T.TRANSPOSE, 6: T.ROTATE_270,
                       7: T.TRANSVERSE, 8: T.ROTATE_90})
    with Image.open(source) as im:
        if im.width * im.height > MAX_PIXELS:  # 경고는 process()에서 꺼 두므로 여기서 직접 막는다
            raise Image.DecompressionBombError(f"{im.width}x{im.height} 픽셀이 한도 {MAX_PIXELS}를 넘음")
        info = {"pil_format": im.format, "mode": im.mode, "source_px": list(im.size), "frames": getattr(im, "n_frames", 1)}
        dpi = im.info.get("dpi")
        if dpi:
            try:
                info["dpi"] = [round(float(v)) for v in dpi][:2]
            except (TypeError, ValueError):
                pass
        if im.format == "JPEG":
            im.draft(im.mode if im.mode in ("RGB", "L", "CMYK") else None, (WORK_LONG, WORK_LONG))
        try:
            orientation = im.getexif().get(0x0112, 1)
        except Exception:
            orientation = 1
        if info["frames"] > 1:
            im.seek(0)
        im.load()
        icc = im.info.get("icc_profile")
        work = im
        if work.mode in ("P", "1", "PA"):
            work = work.convert("RGBA" if work.mode != "1" else "L")
        if max(work.size) > 2 * WORK_LONG:
            factor = max(1, max(work.size) // WORK_LONG)
            try:
                work = work.reduce(factor)
            except (ValueError, NotImplementedError):
                work = work.resize((max(1, work.width // factor), max(1, work.height // factor)), Image.Resampling.BOX)
        else:
            work = work.copy()
        if icc:
            work.info["icc_profile"] = icc
        if "transparency" in im.info and work.mode in ("L", "RGB"):
            work.info["transparency"] = im.info["transparency"]
    if orientation in ORIENT:
        work = work.transpose(ORIENT[orientation])
        info["exif_orientation"] = orientation
    work, info["icc"] = to_srgb_rgba(work)
    info["work_px"] = list(work.size)
    return work, info


def tiff_palette_alpha(data):
    """무압축 팔레트+알파 TIFF(Photoshop EPS 미리보기에 흔함)를 RGBA로. Pillow는 이 모드를 모른다."""
    from PIL import Image, TiffImagePlugin
    fp = io.BytesIO(data)
    ifd = TiffImagePlugin.ImageFileDirectory_v2(fp.read(8))
    fp.seek(ifd.next)
    ifd.load(fp)
    w, h = int(ifd[256]), int(ifd[257])
    bits = tuple(ifd.get(258, (8,)))
    if not (ifd.get(262) == 3 and ifd.get(259, 1) == 1 and ifd.get(284, 1) == 1 and ifd.get(277, 1) == 2 and bits[0] == 8):
        raise ValueError("지원하지 않는 TIFF 미리보기")
    if w * h > MAX_PIXELS:
        raise ValueError("미리보기가 너무 큼")
    offsets, counts = ifd[273], ifd[279]
    offsets, counts = (offsets if isinstance(offsets, tuple) else (offsets,)), (counts if isinstance(counts, tuple) else (counts,))
    raw = b"".join(data[o:o + c] for o, c in zip(offsets, counts))[:w * h * 2]
    if len(raw) < w * h * 2:
        raise ValueError("미리보기 데이터가 짧음")
    cmap = ifd[320]
    n = len(cmap) // 3
    palette = []
    for i in range(min(n, 256)):
        palette += [cmap[i] >> 8, cmap[n + i] >> 8, cmap[2 * n + i] >> 8]
    image = Image.frombytes("P", (w, h), raw[0::2])
    image.putpalette(palette)
    image = image.convert("RGBA")
    image.putalpha(Image.frombytes("L", (w, h), raw[1::2]))
    return image


def eps_preview(path):
    """DOS EPS 바이너리 머리말에서 TIFF(없으면 WMF) 미리보기를 꺼낸다. 없으면 None."""
    size = os.path.getsize(path)
    with open(path, "rb") as stream:
        head = stream.read(30)
        if len(head) < 28 or head[:4] != b"\xc5\xd0\xd3\xc6":
            return None
        _, ps_off, ps_len, wmf_off, wmf_len, tif_off, tif_len = struct.unpack("<4s6I", head[:28])
        for kind, off, length in (("eps_tiff_preview", tif_off, tif_len), ("eps_wmf_preview", wmf_off, wmf_len)):
            if length and off + length <= size:
                stream.seek(off)
                data = stream.read(length)
                try:
                    work, info = open_raster(io.BytesIO(data))
                except Exception:  # Pillow가 못 열면 팔레트+알파 TIFF로 한 번 더, 그래도 안 되면 다음 후보
                    if kind != "eps_tiff_preview":
                        continue
                    try:
                        work = tiff_palette_alpha(data)
                    except Exception:
                        continue
                    info = {"pil_format": "TIFF", "mode": "PA", "source_px": list(work.size), "frames": 1, "icc": "none",
                            "work_px": list(work.size)}
                info["render_source"] = kind
                info["eps_sections"] = {"ps": ps_len, "wmf": wmf_len, "tiff": tif_len}
                return work, info
    return None


CSS_RULE = re.compile(r"([^{}]+)\{([^}]*)\}")
CLASS_SEL = re.compile(r"(?:[A-Za-z][\w-]*)?\.([\w-]+)")
TAG = re.compile(r"<([A-Za-z][\w:.-]*)(\s[^<>]*?)(?=/?>)")


PCT_RGB = re.compile(r"rgb\(\s*([\d.]+)%\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)")


def inline_svg_css(data):
    """SVG 글자를 데이터로만 다룬다: .클래스 규칙을 style 속성에 붙이고 잘못된 data:img MIME을 고친다. 바뀐 게 없으면 None."""
    text = data.decode("utf-8", "replace")
    changed = 0
    if "data:img/" in text:
        text = text.replace("data:img/", "data:image/")
        changed += 1
    # MuPDF는 rgb(백분율) 색을 못 읽어 검게 칠한다: #rrggbb로 바꾼다.
    text, n = PCT_RGB.subn(lambda m: "#" + "".join(f"{round(min(100.0, float(v)) * 255 / 100):02x}" for v in m.groups()), text)
    changed += n
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I)
    rules = {}
    for css in blocks:
        css = re.sub(r"/\*.*?\*/", "", css.replace("<![CDATA[", "").replace("]]>", ""), flags=re.S)
        for selectors, body in CSS_RULE.findall(css):
            decl = ";".join(d.strip() for d in body.split(";") if ":" in d)
            for sel in selectors.split(","):
                m = CLASS_SEL.fullmatch(sel.strip())
                if m and decl:
                    rules[m.group(1)] = (rules.get(m.group(1), "") + ";" + decl).strip(";")

    def attach(m):
        attrs = m.group(2)
        cm = re.search(r"\sclass\s*=\s*([\"'])(.*?)\1", attrs)
        decl = ";".join(rules[c] for c in cm.group(2).split() if c in rules) if cm else ""
        if not decl:
            return m.group(0)
        sm = re.search(r"\sstyle\s*=\s*([\"'])(.*?)\1", attrs)
        if sm:  # 요소의 style이 클래스 규칙보다 앞선다(뒤에 둔다)
            attrs = attrs[:sm.start()] + f' style="{decl};{sm.group(2)}"' + attrs[sm.end():]
        else:
            attrs += f' style="{decl}"'
        return f"<{m.group(1)}{attrs}"

    if rules:
        new = TAG.sub(attach, text)
        changed += new != text
        text = new
    return (text.encode("utf-8"), len(rules)) if changed else None


def ink_rect(fitz, page):
    """페이지에 그려지는 모든 것의 범위(MuPDF 좌표). 아트보드 밖에 놓인 AI 작업물을 찾을 때 쓴다."""
    boxes = [fitz.Rect(b[1]) for b in page.get_bboxlog() if not b[0].startswith("ignore")]
    boxes = [b for b in boxes if b.is_valid and not b.is_infinite and b.width < 1e5 and b.height < 1e5]
    if not boxes:
        return None
    out = fitz.Rect(boxes[0])
    for box in boxes[1:]:
        out |= box
    return out if out.width > 0 and out.height > 0 else None


def render_vector(path, fmt):
    """PyMuPDF로 1쪽을 그린다: 256px 시험 렌더로 내용 상자를 찾고, 그 영역을 긴 변 512px로 다시 그린다."""
    from PIL import Image
    fitz = load_fitz()
    css, gradient = None, False
    if fmt == "svg":
        raw = Path(path).read_bytes()
        css, gradient = inline_svg_css(raw), b"Gradient" in raw  # MuPDF SVG는 그라디언트를 제대로 못 그린다
    doc = fitz.open(stream=css[0], filetype="svg") if css else fitz.open(str(path), filetype="svg" if fmt == "svg" else "pdf")
    try:
        if doc.page_count < 1:
            raise ValueError("쪽이 없음")
        page = doc[0]
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            raise ValueError("쪽 크기가 0")
        info = {"pages": doc.page_count, "page_pt": [round(rect.width, 1), round(rect.height, 1)], "render_source": "pymupdf"}
        if css:
            info["svg_css_rules"] = css[1]
        if gradient:
            info["svg_gradient"] = True
        if fmt != "svg":
            text = page.get_text("text")[:4000]
            info["placeholder"] = any(m in text for m in PLACEHOLDER)
            info["text_chars"] = len(text.strip())
        z1 = PROBE / max(rect.width, rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(z1, z1), alpha=True)
        probe = Image.open(io.BytesIO(pix.tobytes("png")))
        probe.load()
        probe = probe.convert("RGBA")
        stats = page_stats(probe)
        if not stats["bbox"] and fmt != "svg":
            # 아트보드(페이지) 밖에 작업물이 있는 AI: 메모리 안에서만 페이지 상자를 작업물 범위로 넓혀 다시 본다(원본 파일은 그대로).
            try:
                ink = ink_rect(fitz, page)
                if ink and not rect.contains(ink):
                    pad = 0.02 * max(ink.width, ink.height)
                    pdf_box = (fitz.Rect(ink.x0 - pad, ink.y0 - pad, ink.x1 + pad, ink.y1 + pad) * ~page.transformation_matrix).normalize()
                    page.set_mediabox(pdf_box)
                else:
                    ink = None
            except Exception as error:
                info["off_artboard_error"] = f"{type(error).__name__}: {str(error)[:120]}"
                ink = None
            if ink:
                rect = page.rect
                z1 = PROBE / max(rect.width, rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(z1, z1), alpha=True)
                probe = Image.open(io.BytesIO(pix.tobytes("png")))
                probe.load()
                probe = probe.convert("RGBA")
                stats = page_stats(probe)
                info["off_artboard"] = True
                info["ink_page_pt"] = [round(rect.width, 1), round(rect.height, 1)]
        info["page"] = stats
        if not stats["bbox"]:
            return probe, info, stats
        x0, y0, x1, y1 = stats["bbox"]
        pad = MARGIN * max(x1 - x0, y1 - y0) + 1
        clip = fitz.Rect(rect.x0 + (x0 - pad) / z1, rect.y0 + (y0 - pad) / z1, rect.x0 + (x1 + pad) / z1, rect.y0 + (y1 + pad) / z1) & rect
        z2 = LONG / max(clip.width, clip.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(z2, z2), clip=clip, alpha=True)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img.load()
        info["trim"] = [round((clip.x0 - rect.x0) / rect.width, 4), round((clip.y0 - rect.y0) / rect.height, 4),
                        round((clip.x1 - rect.x0) / rect.width, 4), round((clip.y1 - rect.y0) / rect.height, 4)]
        return img.convert("RGBA"), info, stats
    finally:
        doc.close()


def trim_raster(work):
    """256px 축소본으로 내용 상자를 찾아 작업 해상도에서 자른다."""
    from PIL import Image
    probe = work.copy()
    probe.thumbnail((PROBE, PROBE), Image.Resampling.LANCZOS)
    stats = page_stats(probe)
    if not stats["bbox"]:
        return work, stats, None
    sx, sy = work.width / probe.width, work.height / probe.height
    x0, y0, x1, y1 = stats["bbox"]
    pad = MARGIN * max(x1 - x0, y1 - y0) + 1
    box = (max(0, int((x0 - pad) * sx)), max(0, int((y0 - pad) * sy)), min(work.width, int((x1 + pad) * sx + 0.999)),
           min(work.height, int((y1 + pad) * sy + 0.999)))
    trim = [round(box[0] / work.width, 4), round(box[1] / work.height, 4), round(box[2] / work.width, 4), round(box[3] / work.height, 4)]
    return work.crop(box), stats, trim


def save_png(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    img.save(tmp, format="PNG")
    os.replace(tmp, path)
    return path.stat().st_size


def process(job, outdir):
    from PIL import Image
    started = time.perf_counter()
    res = {"id": job["id"], "status": "ok", "version": RENDER_VERSION}
    fmt, path = job["format"], Path(job["path"])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if fmt in POSTSCRIPT:
                res["status"] = "needs_ghostscript"
                return res
            if fmt in VECTOR:
                img, info, stats = render_vector(path, fmt)
                res.update(info)
                trimmed = True
            elif fmt == "eps_dosbin":
                got = eps_preview(path)
                if got is None:
                    res["status"] = "needs_ghostscript"
                    return res
                work, info = got
                res.update(info)
                img, stats, trim = trim_raster(work)
                res["page"], res["trim"] = stats, trim
            elif fmt in RASTER:
                work, info = open_raster(path)
                res.update(info)
                res["render_source"] = "raster"
                img, stats, trim = trim_raster(work)
                res["page"], res["trim"] = stats, trim
            else:
                res["status"] = "unsupported"
                return res
            if not stats["bbox"]:
                res["status"] = "empty"
            elif res.get("placeholder"):
                res["status"] = "placeholder"
            if max(img.size) > LONG:
                img.thumbnail((LONG, LONG), Image.Resampling.LANCZOS, reducing_gap=3.0)
            res["out_px"] = list(img.size)
            res["color"] = color_stats(img, stats["bg"], stats["bg_ref"])
            res["png_bytes"] = save_png(img, outdir / job["png"])
            thumb = img.copy()
            thumb.thumbnail((THUMB, THUMB), Image.Resampling.LANCZOS)
            res["thumb_bytes"] = save_png(thumb, outdir / job["thumb"])
    except Exception as error:
        name = type(error).__name__
        res["status"] = "bomb" if name == "DecompressionBombError" else "error"
        res["error"] = f"{name}: {str(error)[:200]}"
    finally:
        res["seconds"] = round(time.perf_counter() - started, 3)
    if res.get("page"):
        res["page"]["bbox"] = list(res["page"]["bbox"]) if res["page"]["bbox"] else None
    return res


# ---------- 자식·감독 프로세스 ----------

def child(shard_path, outdir, offset):
    proto = sys.stdout
    sys.stdout = sys.stderr  # 라이브러리 출력이 통신 줄과 섞이지 않게
    jobs = json.loads(Path(shard_path).read_text(encoding="utf-8"))
    for index in range(offset, len(jobs)):
        proto.write(MARK + json.dumps({"start": index}) + "\n")
        proto.flush()
        result = process(jobs[index], Path(outdir))
        proto.write(MARK + json.dumps({"done": index, "result": result}) + "\n")
        proto.flush()
    proto.write(MARK + json.dumps({"end": True}) + "\n")
    proto.flush()


def run_shard(k, shard, outdir, workdir, timeout, results, lock):
    shard_file = workdir / f"shard-{k}.json"
    shard_file.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")
    offset = 0
    with (workdir / f"worker-{k}.log").open("ab") as log:
        while offset < len(shard):
            proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--child", str(shard_file), str(outdir),
                                     "--offset", str(offset)], stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL, cwd=str(outdir))
            lines = queue.Queue()
            threading.Thread(target=lambda: ([lines.put(line) for line in proc.stdout], lines.put(None)), daemon=True).start()
            current, since, progressed = None, time.monotonic(), False
            while True:
                limit = timeout if current is not None else max(60, timeout)
                try:
                    line = lines.get(timeout=max(0.1, limit - (time.monotonic() - since)))
                except queue.Empty:
                    proc.kill()
                    proc.wait()
                    stuck = current if current is not None else offset
                    with lock:
                        results[shard[stuck]["id"]] = {"id": shard[stuck]["id"], "status": "timeout", "version": RENDER_VERSION,
                                                       "error": f"{timeout}초 제한 시간 초과", "seconds": round(time.monotonic() - since, 1)}
                    offset = stuck + 1
                    break
                if line is None:
                    proc.wait()
                    if current is not None or not progressed:
                        stuck = current if current is not None else offset
                        with lock:
                            results[shard[stuck]["id"]] = {"id": shard[stuck]["id"], "status": "crashed", "version": RENDER_VERSION,
                                                           "error": f"자식 프로세스 종료 코드 {proc.returncode}"}
                        offset = stuck + 1
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text.startswith(MARK.strip()):
                    continue
                try:
                    msg = json.loads(text[len(MARK.strip()):])
                except ValueError:
                    continue
                if "start" in msg:
                    current, since = msg["start"], time.monotonic()
                elif "done" in msg:
                    with lock:
                        results[msg["result"]["id"]] = msg["result"]
                    offset, current, since, progressed = msg["done"] + 1, None, time.monotonic(), True
                elif msg.get("end"):
                    offset = len(shard)
    shard_file.unlink(missing_ok=True)


def supervise(jobs_path, outdir, procs, timeout, result_path):
    started = time.time()
    payload = json.loads(Path(jobs_path).read_text(encoding="utf-8"))
    jobs = payload["jobs"] if isinstance(payload, dict) else payload
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(result_path).resolve().parent
    workdir.mkdir(parents=True, exist_ok=True)
    order = sorted(range(len(jobs)), key=lambda i: -jobs[i].get("bytes", 0))
    shards = [[jobs[i] for i in order[k::procs]] for k in range(procs)]
    results, lock = {}, threading.Lock()

    def guarded(k, shard):
        try:
            run_shard(k, shard, outdir, workdir, timeout, results, lock)
        except Exception as error:  # 감독 쪽 오류: 남은 작업을 오류로 남긴다
            with lock:
                for job in shard:
                    results.setdefault(job["id"], {"id": job["id"], "status": "error", "version": RENDER_VERSION,
                                                   "error": f"감독 오류 {type(error).__name__}: {str(error)[:160]}"})

    threads = [threading.Thread(target=guarded, args=(k, shard)) for k, shard in enumerate(shards) if shard]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for leftover in outdir.rglob("*.png.tmp"):
        leftover.unlink(missing_ok=True)
    out = {"schema": "logo-render-result@1", "version": RENDER_VERSION, "worker": versions(),
           "params": {"long": LONG, "thumb": THUMB, "probe": PROBE, "margin": MARGIN, "work_long": WORK_LONG, "max_pixels": MAX_PIXELS,
                      "timeout_s": timeout, "procs": procs},
           "elapsed_s": round(time.time() - started, 1), "jobs": len(jobs),
           "results": [results.get(job["id"], {"id": job["id"], "status": "missing", "version": RENDER_VERSION}) for job in jobs]}
    Path(result_path).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", help="작업 목록 JSON({jobs:[{id, path, format, png, thumb, bytes}]}) 또는 --child의 샤드 파일")
    parser.add_argument("outdir", help="PNG를 쓸 폴더(작업의 png·thumb 경로 기준)")
    parser.add_argument("--procs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90.0, help="파일 하나당 제한 시간(초)")
    parser.add_argument("--result", help="결과 JSON 경로(기본: jobs 옆 render_result.json)")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--offset", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.jobs, args.outdir, args.offset)
        return
    result = args.result or str(Path(args.jobs).with_name("render_result.json"))
    out = supervise(args.jobs, args.outdir, max(1, args.procs), args.timeout, result)
    counts = {}
    for item in out["results"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    sys.stdout.write(json.dumps({"jobs": out["jobs"], "elapsed_s": out["elapsed_s"], "status": counts}, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
