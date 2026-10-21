"""기관 로고·CI 참조 세트(data/runtime/logo-assets) 전처리 카탈로그.

실행: .\\.venv\\Scripts\\python.exe -m app.poster_ocr.logo_catalog build [--assets data/runtime/logo-assets] [--out data/runtime/logo-catalog]
원본 폴더는 읽기만 한다(이름 바꾸기·옮기기·지우기 없음). 이 프로세스는 로고 파일을 해석하지 않고 바이트 해시와 머리말 판별만 한다.
그림을 여는 일(래스터 해독, 벡터 렌더링, EPS 미리보기)은 모두 logo_render_worker.py를 별도 프로세스(python -I)로 맡긴다.
단계: 1) 목록(실제 형식·해시·출처·이용 조건) 2) 표기 변형 태그(경로 낱말 + 화소 색) 3) 중복 묶음(완전 중복, 같은 이름 다른 형식, dHash 근사)
4) 파생 PNG(긴 변 512, 썸네일 160)와 품질 표시 5) gold-v2h 포스터 기관 표기 → 기관 id 대응.
산출: <out>/catalog.json, <out>/linkage.json, <out>/render_report.json, <out>/png/<기관 폴더>/<sha256 앞 16자>.png, <out>/thumb/...
파생 PNG는 내부 개발용 참조다. 공개 저장소·docs/에 두지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "data/runtime/logo-assets"
OUT = ROOT / "data/runtime/logo-catalog"
SOURCES = ROOT / "data/logo-sources/manifest.json"
GOLD = ROOT / "data/benchmarks/gold-v2h"
CASES = ROOT / "data/benchmarks/posters-v2.json"
WORKER = Path(__file__).with_name("logo_render_worker.py")
WORKER_PYTHON = Path("C:/Users/playdata2/AppData/Local/Programs/Python/Python312/python.exe")
RENDER_VERSION = "logo-render@2"  # logo_render_worker.RENDER_VERSION과 같아야 이전 렌더 결과를 다시 쓴다.

RASTER = {"png", "jpeg", "gif", "bmp", "webp", "tiff", "psd"}
VECTOR_RENDER = {"ai_pdf", "pdf", "svg"}
POSTSCRIPT = {"ai_ps", "eps", "ps"}
VECTOR = VECTOR_RENDER | POSTSCRIPT | {"eps_dosbin"}
CONTAINER = {"zip", "rar", "7z"}
DOCUMENT = {"ole", "html"}
EXT_FORMATS = {".ai": {"ai_pdf", "ai_ps"}, ".eps": {"eps", "eps_dosbin", "ps"}, ".png": {"png"}, ".jpg": {"jpeg"}, ".jpeg": {"jpeg"},
               ".gif": {"gif"}, ".svg": {"svg"}, ".pdf": {"pdf"}, ".zip": {"zip"}, ".bmp": {"bmp"}, ".tif": {"tiff"}, ".tiff": {"tiff"},
               ".psd": {"psd"}, ".webp": {"webp"}}
LIMIT_MB = 400
DHASH_MAX = 3
A_RATIO = 2 ** 0.5

# ---------- 1) 형식 판별·이름 복원 ----------


def sniff(head, ext):
    """머리말 바이트로 실제 형식을 정한다(확장자는 AI·EPS 구분에만 쓴다)."""
    if head.startswith(b"%PDF"):
        return "ai_pdf" if ext == ".ai" else "pdf"
    if head.startswith(b"\xc5\xd0\xd3\xc6"):
        return "eps_dosbin"
    if head.startswith(b"%!PS"):
        if ext == ".ai":
            return "ai_ps"
        return "eps" if b"EPSF" in head[:40] else "ps"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:11] == b"CDR":
        return "cdr"
    if head.startswith(b"8BPS"):
        return "psd"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf"):
        return "7z"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    low = head[:65536].lower()
    if b"<svg" in low:
        return "svg"
    if b"<html" in low or b"<!doctype html" in low:
        return "html"
    if head.startswith((b"\x01\x00\x09\x00", b"\xd7\xcd\xc6\x9a")):
        return "wmf"
    if head.startswith(b"\x01\x00\x00\x00") and b" EMF" in head[:64]:
        return "emf"
    return "unknown"


def kind_of(fmt):
    if fmt in RASTER:
        return "raster"
    if fmt in VECTOR:
        return "vector"
    if fmt in CONTAINER:
        return "container"
    if fmt in DOCUMENT:
        return "document"
    return "other"


def family(fmt):
    if fmt in RASTER:
        return "raster:" + fmt
    if fmt in ("ai_pdf", "ai_ps"):
        return "vector:ai"
    if fmt in ("eps", "eps_dosbin", "ps"):
        return "vector:eps"
    return "vector:" + fmt


HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
# UTF-8을 다른 코드페이지로 읽었을 때 생기는 글자(Latin-1 보충, cp1252 문장부호, cp437 그리스·수학·상자 기호).
SUSPECT = re.compile(r"[\u0080-\u00ff\u0152-\u0192\u02c6\u02dc\u0393-\u03c6\u2013-\u2122\u2190-\u2265\u2302-\u2321\u2500-\u25a0]")
PCT = re.compile(r"%[0-9A-Fa-f]{2}")
CHAINS = (("latin1",), ("cp1252",), ("cp437",), ("cp949", "latin1"), ("cp949", "cp1252"))


def _chain(text, chain):
    steps = [text]
    for codec in chain:
        text = text.encode(codec).decode("utf-8")
        steps.append(text)
    return steps


def _suspects(text):
    return len(SUSPECT.findall(text))


def repair_name(name):
    """깨진 파일 이름 한 토막을 되살린다: 퍼센트 인코딩, UTF-8을 latin1·cp1252·cp437로 읽은 것, 그 결과를 다시 cp949로 읽은 것(이중), NFD 자모.
    엄격 복호가 되고, 결과에 한글이 있고, 의심 글자가 줄어든 경우에만 받아들인다."""
    fixes, text = [], name
    if PCT.search(text):
        try:
            decoded = unquote(text, errors="strict")
        except UnicodeDecodeError:
            decoded = text
        if decoded != text:
            text = decoded
            fixes.append("percent")
    if any(ord(ch) > 127 for ch in text):
        for chain in CHAINS:
            try:
                steps = _chain(text, chain)
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            result = steps[-1]
            if result == text or not HANGUL.search(result) or any(0x80 <= ord(ch) <= 0x9f for ch in result):
                continue
            before = _suspects(steps[1]) if len(chain) == 2 else _suspects(text)
            if before == 0 or _suspects(result) >= before:
                continue
            text = result
            fixes.append("mojibake_" + "+".join(chain))
            break
    nfc = unicodedata.normalize("NFC", text)
    if nfc != text:
        text = nfc
        fixes.append("nfd")
    return text, fixes


def repair_path(rel):
    parts, fixes = [], []
    for part in rel.split("/"):
        fixed, how = repair_name(part)
        parts.append(fixed)
        fixes.extend(h for h in how if h not in fixes)
    return "/".join(parts), fixes


# ---------- 2) 표기 변형 태그 ----------

TAG_RULES = {
    # 구성
    "comp:symbol": r"심볼|심벌|심블|symbol|엠블[럼렘]|emblem|휘장|logo\s*mark|로고\s*마크|상징\s*마크|(?<!워드)(?<!워드 )마크(?!.{0,3}타입)",
    "comp:logotype": r"로고\s*타입|logo\s*type|워드\s*마크|word\s*mark|레터링|lettering|글자",
    "comp:signature": r"시그니[처쳐]|signature|조합|combination|combi|lock\s*up|국영문|한영",
    "comp:slogan": r"슬로건|slogan|구호|캐치\s*프레이즈|catch\s*phrase|비전|vision",
    "comp:brand": r"브랜드|brand|(?<![a-z])bi(?![a-z])|b\.i\.?",
    "comp:ci": r"(?<![a-z])ci(?![a-z])|c\.i\.?|(?<![a-z])cip(?![a-z])",
    "comp:flag": r"깃발|flag|(?<![가-힣])[시군구도]기(?![가-힣])",
    "comp:character": r"캐릭터|character|마스코트|mascot",
    "comp:manual": r"매뉴얼|메뉴얼|manual|guide|가이드|편람|규정|지침|사용법|기본\s*디자인",
    # 언어
    "lang:ko": r"국문|한글|한국어|(?<![a-z])kor?(?![a-z])|korean|(?<![a-z])kr(?![a-z])",
    "lang:en": r"영문|영어|(?<![a-z])eng?(?![a-z])|english",
    "lang:hanja": r"한자|한문|漢",
    "lang:mixed": r"한영|국영문|국문\s*영문|국·영|(?<![a-z])ko[_\-\s]?en(?![a-z])|kor[_\-\s]?eng",
    # 배치
    "layout:horizontal": r"가로|좌우|horizontal|horizon|(?<![a-z])hor(?![a-z])|landscape|wide",
    "layout:vertical": r"세로|상하|vertical|(?<![a-z])vert?(?![a-z])|portrait",
    "layout:basic": r"기본형|기본|basic|default|standard|표준",
    "layout:applied": r"응용형|응용|application|(?<![a-z])app(?![a-z])|변형|활용",
    # 색
    "color:color": r"컬러|칼라|color|colour|원색|풀\s*컬러",
    "color:mono": r"흑백|단색|mono|black|(?<![a-z])bk(?![a-z])|(?<![a-z])bw(?![a-z])|b&w|먹색|그레이|gray|grey|\(g\)|grayscale|블랙",
    "color:inverse": r"반전|역상|reverse|negative|inverse|white|화이트|흰|백색|네거티브",
    "color:gold": r"금색|골드|gold|금박",
    "color:silver": r"은색|실버|silver|은박",
    "color:transparent": r"투명|transparent|누끼|배경\s*없",
    # 색 공간(참고)
    "space:cmyk": r"cmyk",
    "space:rgb": r"(?<![a-z])rgb(?![a-z])",
    "space:spot": r"pantone|별색|spot|팬톤|(?<![a-z])dic(?![a-z])",
}
TAG_RX = {tag: re.compile(rule, re.I) for tag, rule in TAG_RULES.items()}
LOGO_PARTS = ("comp:symbol", "comp:logotype", "comp:signature", "comp:slogan", "comp:brand", "comp:ci")
FMT_TOKEN = re.compile(r"(?<![a-z0-9])(ai|jpe?g|png|eps|pdf|svg|gif|psd|bmp|cmyk|rgb|out|converted|illust|일러스트|이미지|파일|file|web|print"
                       r"|인쇄용|웹용|원본|ver\d*)(?![a-z0-9])", re.I)


def path_tags(text):
    """경로(기관·구역 아래, 확장자 뺀 것)에서 표기 변형 태그를 뽑는다."""
    text = unicodedata.normalize("NFC", text).lower()
    tags = [tag for tag, rx in TAG_RX.items() if rx.search(text)]
    if "lang:ko" in tags and "lang:en" in tags and "lang:mixed" not in tags:
        tags.append("lang:mixed")
    return tags


def pixel_tags(render):
    """렌더 결과의 화소 측정값으로 색 태그를 붙인다."""
    color, page = render.get("color") or {}, render.get("page") or {}
    if not color.get("content_px"):
        return []
    tags = []
    chroma = color.get("chroma_frac", 0)
    if chroma < 0.05:
        tags.append("px:achromatic")
        if color.get("white_frac", 0) >= 0.8:
            tags.append("px:white")
        elif color.get("black_frac", 0) >= 0.6:
            tags.append("px:black")
        else:
            tags.append("px:gray")
    else:
        hues = color.get("hues") or {}
        if hues:
            top = next(iter(hues))
            tags.append("px:hue:" + top)
        if chroma >= 0.2 and sum(1 for v in hues.values() if v >= 0.1) >= 3:
            tags.append("px:multicolor")
    if page.get("bg"):
        tags.append("px:bg:" + page["bg"])
    return tags


def norm_stem(name):
    stem = os.path.splitext(name.lower())[0]
    stem = re.sub(r"\[converted\]", " ", stem)
    stem = FMT_TOKEN.sub(" ", stem)
    return re.sub(r"[\s_\-.()\[\]]+", "", stem)


# ---------- 출처·이용 조건 ----------


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def rel_saved(saved):
    """provenance의 saved_path를 logo-assets 기준 상대 경로(NFC)로."""
    text = unicodedata.normalize("NFC", str(saved or "").replace("\\", "/"))
    at = text.find("logo-assets/")
    return text[at + len("logo-assets/"):] if at >= 0 else text


def folder_to_id(folder):
    head, _, rest = folder.partition("__")
    return f"{head}:{rest.replace('__', ':')}" if rest else folder


def provenance_maps(assets):
    """기관 provenance.json과 commons/provenance.json에서 경로·해시 → 출처 정보."""
    by_path, by_sha, extracted_from = {}, {}, set()
    for prov_path in sorted(assets.glob("*/provenance.json")):
        if prov_path.parent.name == "commons":
            continue
        prov = read_json(prov_path, {}) or {}
        downloads = {}
        for item in prov.get("files") or []:
            info = {"kind": "download", "label": item.get("label"), "source_url": item.get("source_url"), "final_url": item.get("final_url"),
                    "page": item.get("page") or prov.get("page"), "downloaded_at": item.get("downloaded_at")}
            rel = rel_saved(item.get("saved_path"))
            downloads[rel] = info
            by_path[rel] = info
            if item.get("sha256"):
                by_sha.setdefault(item["sha256"], info)
        for block in prov.get("extracted") or []:
            zip_rel = rel_saved(block.get("from"))
            extracted_from.add(zip_rel)
            parent = downloads.get(zip_rel, {})
            for entry in block.get("entries") or []:
                info = {"kind": "zip_member", "label": parent.get("label"), "source_url": parent.get("source_url"),
                        "page": parent.get("page") or prov.get("page"), "zip": zip_rel, "member": entry.get("name"),
                        "downloaded_at": parent.get("downloaded_at")}
                rel = rel_saved(entry.get("saved_path"))
                by_path[rel] = info
                if entry.get("sha256"):
                    by_sha.setdefault(entry["sha256"], info)
    commons = read_json(assets / "commons/provenance.json", {}) or {}
    commons_ids = {}
    for item in commons.get("files") or []:
        rel = rel_saved(item.get("saved_path"))
        info = {"kind": "commons", "label": item.get("title"), "source_url": item.get("source_url"), "page": item.get("file_page"),
                "license": item.get("license_short"), "restrictions": item.get("restrictions") or None, "role": item.get("role"),
                "commons_version": item.get("commons_version_timestamp"), "downloaded_at": item.get("downloaded_at")}
        by_path[rel] = info
        if item.get("sha256"):
            by_sha.setdefault(item["sha256"], info)
        if item.get("id"):
            commons_ids[rel.split("/")[1] if rel.startswith("commons/") else rel.split("/")[0]] = item["id"]
    return by_path, by_sha, extracted_from, commons_ids


def terms_for(index_entity, source_entry):
    """INDEX(사람 검증)와 수집 대상 목록(manifest)의 이용 조건 표시를 합친다."""
    ie, se = index_entity or {}, source_entry or {}
    review = ie.get("terms_review") or ""
    dev_use = ie.get("dev_use") or (se.get("dev_use") or {}).get("status")
    lic = se.get("license") or {}
    hold = "보류" in review
    terms = {"hold": hold, "review": bool(review) and not hold, "terms_review": review or None, "dev_use": dev_use,
             "limited_use": (dev_use or "").startswith("용도 한정"),
             "approval_required": bool(lic.get("approval_required")) or (dev_use or "").startswith("협의"),
             "license": lic.get("category"), "kogl_type": lic.get("kogl_type"), "terms_decision": ie.get("terms_decision")}
    terms["blocked"] = terms["hold"] or terms["review"] or terms["limited_use"]
    return terms


# ---------- 1) 목록 ----------


def sha256_and_head(path, head_bytes=65536):
    digest = hashlib.sha256()
    head = b""
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            if not head:
                head = block[:head_bytes]
            digest.update(block)
    return digest.hexdigest(), head


def walk_files(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "_excluded")
        for name in sorted(filenames):
            yield Path(dirpath) / name


def inventory(assets, index, sources):
    """원본·압축 해제·commons 파일 목록. 압축을 푼 ZIP과 _excluded는 건너뛴다."""
    by_path, by_sha, extracted_from, commons_ids = provenance_maps(assets)
    entities = {Path(e["folder"]).name: e for e in index.get("entities") or []}
    files, skipped = [], Counter()
    areas = []
    for folder in sorted(p for p in assets.iterdir() if p.is_dir()):
        if folder.name == "commons":
            for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
                areas.append((sub.name, "commons", sub))
            continue
        for area in ("original", "extracted"):
            if (folder / area).is_dir():
                areas.append((folder.name, area, folder / area))
        if (folder / "_excluded").is_dir():
            skipped["excluded_files"] += sum(1 for _ in walk_files(folder / "_excluded"))
    for entity_dir, area, base in areas:
        for path in walk_files(base):
            rel = path.relative_to(assets).as_posix()
            if area == "commons" and path.name == "provenance.json":
                continue
            ext = path.suffix.lower()
            if ext == ".zip" or area == "original":
                with open(path, "rb") as stream:
                    head = stream.read(8)
                if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
                    zip_rel = unicodedata.normalize("NFC", rel)
                    if zip_rel in extracted_from or (assets / entity_dir / "extracted" / path.stem).is_dir():
                        skipped["zip_extracted"] += 1
                        continue
            sha, head = sha256_and_head(path)
            fmt = sniff(head, ext)
            display, fixes = repair_path(rel)
            inner = "/".join(display.split("/")[2:])  # 기관 폴더·구역(commons/폴더) 아래
            if area == "commons":
                eid = commons_ids.get(entity_dir) or folder_to_id(entity_dir)
            else:
                eid = entities[entity_dir]["id"] if entity_dir in entities else folder_to_id(entity_dir)
            nfc = unicodedata.normalize("NFC", rel)
            prov = by_path.get(nfc) or by_path.get(rel)
            prov_status = "path" if prov else None
            if not prov and sha in by_sha:
                prov, prov_status = by_sha[sha], "sha256"
            row = {"file_id": "f" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12], "entity_id": eid, "entity_dir": entity_dir,
                   "area": area, "path": rel, "display_path": display, "display_name": display.rsplit("/", 1)[-1], "name_fix": fixes,
                   "ext": ext, "format": fmt, "kind": kind_of(fmt), "bytes": path.stat().st_size, "sha256": sha,
                   "provenance": dict(prov or {}, status=prov_status or "missing")}
            if fmt in CONTAINER:
                row["provenance"]["note"] = "압축을 풀지 않은 묶음"
            tags = path_tags(os.path.splitext(inner)[0])
            if area == "commons" and (prov or {}).get("role") == "flag" and "comp:flag" not in tags:
                tags.append("comp:flag")
            row["tags"] = tags
            stem_text = os.path.splitext(display.rsplit("/", 1)[-1])[0].lower()
            if row["kind"] in ("raster", "vector"):
                if TAG_RX["comp:manual"].search(stem_text):
                    row["logo_kind"] = "manual"
                elif "comp:character" in tags and not any(t in tags for t in LOGO_PARTS):
                    row["logo_kind"] = "character"
                else:
                    row["logo_kind"] = "logo"
            else:
                row["logo_kind"] = row["kind"]
            if ext in EXT_FORMATS and fmt not in EXT_FORMATS[ext]:
                row["ext_mismatch"] = True
            files.append(row)
    return files, skipped


# ---------- 4) 렌더링 ----------


def render_jobs(files):
    jobs, seen = [], set()
    for row in files:
        if row["kind"] not in ("raster", "vector"):
            continue
        job_id = f"{row['entity_dir']}/{row['sha256'][:16]}"
        row["render_id"] = job_id
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append({"id": job_id, "path": "", "format": row["format"], "png": f"png/{job_id}.png", "thumb": f"thumb/{job_id}.png",
                     "bytes": row["bytes"], "_rel": row["path"]})
    return jobs


def run_renders(jobs, assets, out, worker_python, procs, timeout, force):
    """이전 결과를 다시 쓰고, 나머지만 격리 워커로 렌더링한다."""
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    merged_path = work / "render_result.json"
    previous = read_json(merged_path, {}) or {}
    old = {r["id"]: r for r in previous.get("results") or [] if r.get("version") == RENDER_VERSION}
    todo, reused = [], {}
    for job in jobs:
        prev = old.get(job["id"])
        done = prev and (prev.get("status") != "ok" or ((out / job["png"]).is_file() and (out / job["thumb"]).is_file()))
        if prev and done and not force and prev.get("status") not in ("missing",):
            reused[job["id"]] = prev
        else:
            todo.append(dict({k: v for k, v in job.items() if not k.startswith("_")}, path=str((assets / job["_rel"]).resolve())))
    info = {"worker": previous.get("worker"), "elapsed_s": 0.0, "rendered_now": len(todo), "reused": len(reused),
            "worker_python": str(worker_python)}
    fresh = {}
    if todo:
        jobs_path, new_path = work / "jobs.json", work / "render_new.json"
        jobs_path.write_text(json.dumps({"jobs": todo}, ensure_ascii=False), encoding="utf-8")
        started = time.time()
        proc = subprocess.run([str(worker_python), "-I", str(WORKER), str(jobs_path), str(out), "--procs", str(procs), "--timeout", str(timeout),
                               "--result", str(new_path)], cwd=str(out), stdin=subprocess.DEVNULL, capture_output=True)
        info["elapsed_s"] = round(time.time() - started, 1)
        if proc.returncode != 0:
            raise SystemExit("렌더 워커 실패: " + proc.stderr.decode("utf-8", "replace")[-2000:])
        payload = read_json(new_path, {}) or {}
        info["worker"] = payload.get("worker")
        info["worker_params"] = payload.get("params")
        fresh = {r["id"]: r for r in payload.get("results") or []}
        jobs_path.unlink(missing_ok=True)
        new_path.unlink(missing_ok=True)
    results = {**reused, **fresh}
    merged = {"schema": "logo-render-result@1", "version": RENDER_VERSION, "worker": info["worker"],
              "results": [results[j["id"]] for j in jobs if j["id"] in results]}
    merged_path.write_text(json.dumps(merged, ensure_ascii=False) + "\n", encoding="utf-8")
    return results, info


def prune(out, jobs):
    """이번 목록에 없는 예전 파생 PNG를 지운다(우리가 만든 png/·thumb/ 안에서만)."""
    keep = {(out / j["png"]).resolve() for j in jobs} | {(out / j["thumb"]).resolve() for j in jobs}
    removed = 0
    for sub in ("png", "thumb"):
        for path in (out / sub).rglob("*.png") if (out / sub).is_dir() else []:
            if path.resolve() not in keep:
                path.unlink()
                removed += 1
    return removed


def quality_flags(row, render):
    """품질 표시: tiny, low_res, huge, white_on_transparent, sheet_like(+이유), wide와 렌더 상태 표시."""
    flags, reasons = [], []
    status = render.get("status") if render else None
    src = render.get("source_px") if render else None
    if src and row["format"] in RASTER | {"eps_dosbin"}:
        short, long = min(src), max(src)
        if short < 64:
            flags.append("tiny")
        if long < 256:
            flags.append("low_res")
        if src[0] * src[1] > 50e6:
            flags.append("huge")
    page = (render or {}).get("page") or {}
    color = (render or {}).get("color") or {}
    if page.get("bg") == "transparent" and color.get("white_frac", 0) >= 0.8 and color.get("content_px"):
        flags.append("white_on_transparent")
    if (render or {}).get("pages", 1) > 1:
        reasons.append("multi_page")
    size = (render or {}).get("page_pt") or src
    if size and min(size) > 0 and abs(max(size) / min(size) - A_RATIO) <= 0.03:
        reasons.append("a_page_aspect")
    if page.get("bbox_frac") is not None and status in ("ok", "placeholder") and page.get("bbox_frac", 1) < 0.5:
        reasons.append("content_lt_50pct")
    if reasons:
        flags.append("sheet_like")
    out_px = (render or {}).get("out_px") or src
    if out_px and min(out_px) > 0 and max(out_px) / min(out_px) > 4:
        flags.append("wide")
    if status in ("error", "timeout", "crashed", "bomb", "missing", "unsupported"):
        flags.append("render_failed")
    elif status == "needs_ghostscript":
        flags.append("needs_ghostscript")
    elif status == "placeholder":
        flags.append("placeholder_ai")
    elif status == "empty":
        flags.append("empty_render")
    if (render or {}).get("render_source", "").startswith("eps_"):
        flags.append("preview_only")
    if (render or {}).get("off_artboard"):
        flags.append("off_artboard")
    if (render or {}).get("svg_gradient") and color.get("content_px") and color.get("chroma_frac", 1) < 0.05:
        flags.append("svg_gradient_check")  # 그라디언트 SVG가 무채색으로 나옴: MuPDF가 색을 못 그렸을 수 있음
    if (render or {}).get("icc") in ("cmyk_naive", "icc_failed"):
        flags.append((render or {})["icc"])
    if page.get("bg") == "busy":
        flags.append("busy_background")
    if (render or {}).get("frames", 1) > 1:
        flags.append("multi_frame")
    if row["provenance"]["status"] == "missing":
        flags.append("no_provenance")
    if row.get("ext_mismatch"):
        flags.append("ext_mismatch")
    return flags, reasons


def render_view(render, job_id):
    if not render:
        return {"status": "not_rendered"}
    keep = ("status", "error", "render_source", "pages", "page_pt", "ink_page_pt", "source_px", "out_px", "icc", "pil_format", "mode", "dpi", "trim",
            "placeholder", "off_artboard", "svg_css_rules", "svg_gradient", "png_bytes", "thumb_bytes", "seconds")
    view = {k: render[k] for k in keep if k in render}
    page = render.get("page") or {}
    view.update({"bg": page.get("bg"), "page_bbox_frac": page.get("bbox_frac"), "page_content_frac": page.get("content_frac"),
                 "transparent_frac": page.get("transparent_frac")})
    if render.get("status") in ("ok", "placeholder", "empty") and "png_bytes" in render:
        view["png"], view["thumb"] = f"png/{job_id}.png", f"thumb/{job_id}.png"
    return view


# ---------- 3) 중복 ----------


def union_groups(items, linked):
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in linked:
        parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(items[i])
    return [g for g in groups.values() if len(g) > 1]


def dedup(files):
    by_id = {row["file_id"]: row for row in files}
    renderable = [row for row in files if row["kind"] in ("raster", "vector")]
    # 완전 중복(sha256)
    exact = []
    by_sha = defaultdict(list)
    for row in renderable:
        by_sha[row["sha256"]].append(row)
    for sha, rows in sorted(by_sha.items()):
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda r: (r["area"] == "commons", r["area"] != "original", len(r["display_path"]), r["display_path"]))
        gid = f"x{len(exact) + 1:03d}"
        exact.append({"id": gid, "sha256": sha, "files": [r["file_id"] for r in rows], "representative": rows[0]["file_id"],
                      "entities": sorted({r["entity_id"] for r in rows}), "cross_entity": len({r["entity_id"] for r in rows}) > 1})
        for r in rows:
            r["dup"]["exact_group"] = gid
            r["dup"]["is_exact_rep"] = r is rows[0]
    # 같은 이름·다른 형식(기관 안, commons 제외)
    stems = []
    by_stem = defaultdict(list)
    for row in renderable:
        if row["area"] != "commons":
            by_stem[(row["entity_id"], norm_stem(row["display_name"]))].append(row)
    for (eid, stem), rows in sorted(by_stem.items()):
        fams = {family(r["format"]) for r in rows}
        if len(fams) < 2:
            continue
        vec = [r for r in rows if r["format"] in VECTOR_RENDER and r["render"].get("status") == "ok"]
        pngs = [r for r in rows if r["format"] == "png" and r["render"].get("source_px")]
        rasters = [r for r in rows if r["kind"] == "raster" and r["render"].get("source_px")]
        order = {"ai_pdf": 0, "svg": 1, "pdf": 2}
        if vec:
            rep = min(vec, key=lambda r: (order[r["format"]], "sheet_like" in r["flags"], -r["bytes"]))
            reason = "renderable_vector"
        elif pngs:
            rep = max(pngs, key=lambda r: r["render"]["source_px"][0] * r["render"]["source_px"][1])
            reason = "largest_png"
        elif rasters:
            rep = max(rasters, key=lambda r: r["render"]["source_px"][0] * r["render"]["source_px"][1])
            reason = "largest_raster"
        else:
            rep, reason = rows[0], "first"
        gid = f"s{len(stems) + 1:04d}"
        stems.append({"id": gid, "entity_id": eid, "stem": stem, "formats": sorted(fams), "files": [r["file_id"] for r in rows],
                      "representative": rep["file_id"], "reason": reason})
        for r in rows:
            r["dup"]["stem_group"] = gid
            r["dup"]["is_stem_rep"] = r is rep
    # dHash 근사 중복(같은 기관, 다른 바이트, 해밍 거리 ≤ 3) — 표시만, 사람 확인용
    def hashable(r):
        color = r["render"].get("_color") or {}
        return (r["render"].get("status") == "ok" and color.get("dhash") and color.get("content_px", 0) >= 50
                and "white_on_transparent" not in r["flags"] and color["dhash"] != "0" * 16)

    def near_groups(rows_by_entity):
        out = []
        for eid, rows in sorted(rows_by_entity.items()):
            hashes = [int(r["render"]["_color"]["dhash"], 16) for r in rows]
            links = [(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows))
                     if rows[i]["sha256"] != rows[j]["sha256"] and bin(hashes[i] ^ hashes[j]).count("1") <= DHASH_MAX]
            for group in union_groups(rows, links):
                if len({r["sha256"] for r in group}) > 1:
                    out.append((eid, group))
        return out

    per_entity, per_entity_raster = defaultdict(list), defaultdict(list)
    for row in renderable:
        if hashable(row):
            per_entity[row["entity_id"]].append(row)
            if row["kind"] == "raster":
                per_entity_raster[row["entity_id"]].append(row)
    near = []
    for eid, group in near_groups(per_entity):
        gid = f"n{len(near) + 1:04d}"
        near.append({"id": gid, "entity_id": eid, "files": [r["file_id"] for r in group], "formats": sorted({r["format"] for r in group}),
                     "mixed_kind": len({r["kind"] for r in group}) > 1})
        for r in group:
            r["dup"]["near_dup_group"] = gid
    raster_only = near_groups(per_entity_raster)
    summary = {"exact_groups": len(exact), "exact_redundant_copies": sum(len(g["files"]) - 1 for g in exact),
               "exact_cross_entity": sum(g["cross_entity"] for g in exact), "stem_groups": len(stems),
               "stem_group_files": sum(len(g["files"]) for g in stems), "stem_rep_reason": dict(Counter(g["reason"] for g in stems)),
               "near_dup_groups": len(near), "near_dup_files": sum(len(g["files"]) for g in near),
               "near_dup_groups_raster_only": len(raster_only), "near_dup_files_raster_only": sum(len(g) for _, g in raster_only),
               "near_dup_entities": len({g["entity_id"] for g in near})}
    del by_id
    return {"summary": summary, "exact_groups": exact, "stem_groups": stems, "near_dup_groups": near}


# ---------- 5) 포스터 기관 표기 → 기관 id ----------

SIDO_SHORT = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "전남광주통합특별시", "대전": "대전광역시",
              "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
              "전북": "전북특별자치도", "전남": "전남광주통합특별시", "전남광주": "전남광주통합특별시", "경북": "경상북도", "경남": "경상남도",
              "제주": "제주특별자치도"}
SIDO_ALIAS = {"서울시": "서울특별시", "부산시": "부산광역시", "대구시": "대구광역시", "인천시": "인천광역시", "대전시": "대전광역시", "울산시": "울산광역시",
              "광주광역시": "전남광주통합특별시", "전라남도": "전남광주통합특별시", "전남도": "전남광주통합특별시", "강원도": "강원특별자치도",
              "전라북도": "전북특별자치도", "전북도": "전북특별자치도", "제주도": "제주특별자치도", "세종시": "세종특별자치시", "충북도": "충청북도",
              "충남도": "충청남도", "경북도": "경상북도", "경남도": "경상남도", "경기도청": "경기도"}
LG_SUFFIX = re.compile(r"청사|청|[가-힣]{1,8}(?:과|실|국|팀|사업소)")
AGENCY = re.compile(r"(부|처|청|위원회|본부)$")
TIERS = ("official_ready", "official_unrendered", "official_blocked", "commons_only", "no_files", "not_collected")


def norm_org(text):
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"\((재|사|주|유)\)|재단법인|사단법인|주식회사", "", text)
    return re.sub(r"[\s·\-_.,()\[\]]+", "", text)


def alias_table(entity_names):
    """기관 id → 이름들로 정확 별칭과 짧은 별칭(시·군·구 뺀 이름, 시도 약칭) 표를 만든다."""
    exact, short, own = defaultdict(set), defaultdict(set), defaultdict(set)
    for eid, e in entity_names.items():
        for n in (e.get("name"), e.get("full_name")):
            if n:
                own[norm_org(n)].add(eid)  # 자기 이름이 먼저(현재 이름·옛 이름으로 겹치는 기관보다)
        names = {e.get("name"), e.get("full_name"), e.get("current_name")}
        for legacy in e.get("legacy") or []:
            names.add(str(legacy).split(":", 1)[-1].replace("|", ""))
        parts = eid.split(":")
        if parts[0] == "lg":
            names.add("".join(parts[1:]))
            name = parts[-1]
            if len(parts) == 3:
                if name.endswith("시"):
                    names.update({name[:-1] + "특례시", parts[1] + name[:-1] + "특례시"})
                if len(name) >= 3 and name[-1] in "시군구":
                    short[norm_org(name[:-1])].add(eid)
            elif len(parts) == 2:
                for key, full in SIDO_SHORT.items():
                    if full == name:
                        short[norm_org(key)].add(eid)
        for n in names:
            if n:
                exact[norm_org(n)].add(eid)
    for alias, full in SIDO_ALIAS.items():
        if "lg:" + full in entity_names:
            exact[norm_org(alias)].add("lg:" + full)
    for key, ids in own.items():
        if len(exact.get(key, ())) > 1:
            exact[key] = set(ids)
    return exact, short


def disambiguate(cands, sido_full, poster_names):
    if len(cands) <= 1:
        return set(cands)
    keep = {c for c in cands if sido_full and c.split(":")[1:2] == [sido_full]}
    if len(keep) == 1:
        return keep
    keep2 = {c for c in cands if len(c.split(":")) == 3 and norm_org(c.split(":")[1]) in poster_names}
    return keep2 if len(keep2) == 1 else set(cands)


def resolve_org(name, sido_full, poster_names, exact, short, ministries):
    """포스터 기관 이름 하나를 기관 id로. 반환 (방법, 후보 집합)."""
    key = norm_org(name)
    if not key:
        return "no_name", set()
    cands, how = set(), None
    if key in exact:
        cands, how = exact[key], "exact"
    elif key in short:
        cands, how = short[key], "short"
    else:
        # '강릉시청', '원주시문화관광과'처럼 지자체 이름 + 청사·부서 접미: 가장 긴 지자체 별칭을 앞에서 찾는다.
        for cut in range(len(key) - 1, 1, -1):
            base, rest = key[:cut], key[cut:]
            if base in exact and all(c.startswith("lg:") for c in exact[base]) and LG_SUFFIX.fullmatch(rest):
                cands, how = exact[base], "suffix"
                break
        if not cands:
            for prefix in sorted(set(SIDO_SHORT) | set(SIDO_SHORT.values()), key=len, reverse=True):
                rest = key[len(norm_org(prefix)):]
                if key.startswith(norm_org(prefix)) and rest and (rest in exact or rest in short):
                    full = SIDO_SHORT.get(prefix, prefix)
                    picked = {c for c in exact.get(rest, set()) | short.get(rest, set()) if c.startswith(f"lg:{full}:")}
                    if picked:
                        cands, how = picked, "sido+name"
                        break
    if cands:
        picked = disambiguate(cands, sido_full, poster_names)
        return (how, picked) if len(picked) == 1 else ("ambiguous", picked)
    if key.endswith("의회"):
        return "council", set()
    if key in ministries:
        return "gov_symbol", {"gov:정부상징"}
    if AGENCY.search(key) and not key.endswith("재단"):
        return "central_agency", set()
    return "unmatched", set()


def unmatched_category(key):
    if re.search(r"(재단|공사|공단|진흥원|원)$", key):
        return "재단·공사·공단·진흥원"
    if re.search(r"(축제|추진위원회|조직위원회|협회|연합회|회|협의회|보존회)$", key):
        return "축제위·협회·단체"
    if re.search(r"(대학교|학교|교육청)$", key):
        return "학교·교육청"
    if re.search(r"(시|군|구|도)$", key):
        return "지자체 표기"
    return "기타(기업·브랜드 등)"


def linkage(gold_dir, cases_path, sources, index, entity_summary):
    cases = {c["id"]: c for c in (read_json(cases_path, {}) or {}).get("cases") or []}
    src = {e["id"]: e for e in sources.get("entries") or []}
    idx = {e["id"]: e for e in index.get("entities") or []}
    names = {}
    for eid in set(src) | set(idx) | set(entity_summary):
        s, i = src.get(eid) or {}, idx.get(eid) or {}
        names[eid] = {"name": i.get("name") or s.get("name") or eid.split(":")[-1], "full_name": s.get("full_name"),
                      "current_name": s.get("current_name"), "legacy": [x.get("entity") for x in s.get("legacy_entities") or [] if isinstance(x, dict)]}
    exact, short = alias_table(names)
    gov = src.get("gov:정부상징") or {}
    ministries = {norm_org(m["name"]) for m in gov.get("ministry_signatures_in_data") or []}
    mentions = []
    for path in sorted(Path(gold_dir).glob("*.poster.json")):
        doc = read_json(path, {}) or {}
        pid = path.name.split(".")[0]
        case = cases.get(pid) or {}
        sido_full = SIDO_SHORT.get((case.get("strata") or {}).get("sido"))
        items = (doc.get("organizations") or {}).get("items") or []
        poster_names = {norm_org(o.get("name")) for o in items if o.get("name")}
        for o in items:
            how, cands = resolve_org(o.get("name"), sido_full, poster_names, exact, short, ministries)
            row = {"poster": pid, "split": case.get("split"), "name": o.get("name"), "form": o.get("form"), "legibility": o.get("legibility"),
                   "roles": o.get("roles"), "match": how, "entity_id": next(iter(cands)) if len(cands) == 1 else None,
                   "candidates": sorted(cands) if len(cands) > 1 else None}
            eid = row["entity_id"]
            if eid:
                summ = entity_summary.get(eid)
                if summ:
                    row["tier"] = summ["tier"]
                    row["official_logo_files"], row["reference_ready"] = summ["official_logo_files"], summ["reference_ready"]
                    row["commons_files"] = summ["commons_files"]
                else:
                    row["tier"] = "not_collected"
                s = src.get(eid) or {}
                freq = s.get("data_frequency") or {}
                row["entity_kind"] = s.get("kind")
                row["selected_via_gold"] = bool(s.get("kind") in ("foundation", "public_institution") and freq.get("gold_posters")
                                                and not (freq.get("std_records_2026plus", 0) >= 4 or freq.get("std_records", 0) >= 10))
            elif how == "unmatched":
                row["category"] = unmatched_category(norm_org(o.get("name")))
            mentions.append(row)
    named = [m for m in mentions if m["name"]]
    logo = [m for m in named if m["form"] == "logo"]

    def tally(rows):
        tiers = Counter(m.get("tier") or m["match"] for m in rows)
        official = sum(tiers[t] for t in ("official_ready", "official_unrendered", "official_blocked"))
        return {"mentions": len(rows), "matched_entity": sum(1 for m in rows if m["entity_id"]), "by_match": dict(Counter(m["match"] for m in rows)),
                "by_tier": dict(tiers), "official_any": official, "official_ready": tiers["official_ready"]}

    posters = defaultdict(list)
    for m in logo:
        posters[m["poster"]].append(m)
    unmatched = Counter(m["name"] for m in named if m["match"] == "unmatched")
    gold_sel = [m for m in logo if m.get("selected_via_gold")]
    entities_used = Counter(m["entity_id"] for m in logo if m["entity_id"])
    out = {
        "schema": "logo-catalog-linkage@1", "gold": "data/benchmarks/gold-v2h", "posters": len({m["poster"] for m in mentions}),
        "notes": [
            "gold-v2h organizations[]의 기관 이름(사람 판독)을 별칭 표(이름·정식 이름·현재 이름·옛 기관·시도 약칭·시군구 약칭·'청'·부서 접미)로 기관 id에 대응했다.",
            "tier: official_ready=공식 수집 파일 중 참조 가능·렌더 성공 파일이 있음, official_unrendered=참조 가능하지만 렌더 실패(Ghostscript 필요 등)만 있음, "
            "official_blocked=공식 파일은 있으나 이용 조건(보류·확인 필요·용도 한정)으로 참조 제외, commons_only=커먼즈 파일만 있음, "
            "no_files=수집 대상이었으나 파일 없음, not_collected=목록에만 있고 받지 않음(협의·승인 필요 등).",
            "주의: data/logo-sources/manifest.json은 공공기관·재단을 고를 때 gold-v2h 기관 등장 빈도(reserve·test 분할 포함)를 썼다"
            "((gold 포스터 수×2 + 2026년 이후 표준데이터 건수) ≥ 4 또는 표준데이터 10건 이상). 그래서 이 대응률은 새 포스터에서보다 높게 나온다. "
            "selected_via_gold=true는 gold 빈도가 없었다면 기준을 넘지 못했을 기관이다.",
        ],
        "counts": {"all_items": len(mentions), "named": tally(named), "logo_named": tally(logo),
                   "logo_unnamed": sum(1 for m in mentions if m["form"] == "logo" and not m["name"]),
                   "posters_with_named_logo": len(posters),
                   "posters_with_ready_logo": sum(1 for v in posters.values() if any(m.get("tier") == "official_ready" for m in v)),
                   "posters_all_named_logos_ready": sum(1 for v in posters.values() if all(m.get("tier") == "official_ready" for m in v)),
                   "distinct_entities_logo": len(entities_used),
                   "selected_via_gold": {"entities": len({m["entity_id"] for m in gold_sel}), "logo_mentions": len(gold_sel),
                                         "logo_mentions_official_any": sum(1 for m in gold_sel if (m.get("tier") or "").startswith("official"))}},
        "by_split": {split: tally([m for m in logo if m["split"] == split]) for split in sorted({m["split"] or "unknown" for m in logo})},
        "top_entities": [{"entity_id": e, "logo_mentions": n, "tier": (entity_summary.get(e) or {}).get("tier", "not_collected")}
                         for e, n in entities_used.most_common(30)],
        "unmatched_top": [{"name": n, "mentions": c, "category": unmatched_category(norm_org(n))} for n, c in unmatched.most_common(40)],
        "unmatched_by_category": dict(Counter(m["category"] for m in named if m.get("category"))),
        "mentions": mentions,
    }
    return out


# ---------- 기관 요약·집계 ----------


def entity_summaries(files, index, sources):
    idx = {e["id"]: e for e in index.get("entities") or []}
    src = {e["id"]: e for e in sources.get("entries") or []}
    by_entity = defaultdict(list)
    for row in files:
        by_entity[row["entity_id"]].append(row)
    out = {}
    for eid in sorted(set(idx) | set(by_entity)):
        rows, ie, se = by_entity.get(eid, []), idx.get(eid), src.get(eid) or {}
        official = [r for r in rows if r["area"] != "commons"]
        commons = [r for r in rows if r["area"] == "commons"]
        logos = [r for r in official if r["logo_kind"] == "logo"]
        usable = [r for r in official if r["usable_for_reference"]]
        ready = [r for r in official if r["reference_ready"]]
        commons_ready = [r for r in commons if r["reference_ready"]]
        if ready:
            tier = "official_ready"
        elif usable:
            tier = "official_unrendered"
        elif logos:
            tier = "official_blocked"
        elif commons:
            tier = "commons_only"
        elif ie:
            tier = "no_files"
        else:
            tier = "not_collected"
        reps = [r for r in ready if r["dup"].get("is_exact_rep", True) and r["dup"].get("is_stem_rep", True)]
        reps.sort(key=lambda r: ("sheet_like" in r["flags"], r["format"] not in VECTOR_RENDER, not any(t in r["tags"] for t in LOGO_PARTS),
                                 r["display_path"]))
        terms = (rows[0]["terms"] if rows else terms_for(ie, se))
        out[eid] = {
            "entity_id": eid, "name": (ie or {}).get("name") or se.get("name") or eid.split(":")[-1], "kind": se.get("kind"),
            "folder": Path(ie["folder"]).name if ie else (rows[0]["entity_dir"] if rows else None), "in_index": bool(ie),
            "commons_only": not ie, "status": (ie or {}).get("status"), "verdict": (ie or {}).get("verdict"), "scope": (ie or {}).get("scope"),
            "issues": len((ie or {}).get("issues") or []), "terms": terms, "tier": tier,
            "files": len(rows), "official_files": len(official), "commons_files": len(commons), "official_logo_files": len(logos),
            "usable_files": len(usable), "reference_ready": len(ready), "commons_ready": len(commons_ready),
            "by_format": dict(Counter(r["format"] for r in rows)), "by_logo_kind": dict(Counter(r["logo_kind"] for r in rows)),
            "vector_ok": sum(1 for r in official if r["format"] in VECTOR_RENDER and r["render"].get("status") == "ok"),
            "tags": dict(Counter(t for r in official for t in r["tags"] if not t.startswith("space:"))),
            "flags": dict(Counter(f for r in rows for f in r["flags"])),
            "representatives": [r["file_id"] for r in reps[:12]],
            "data_frequency": se.get("data_frequency"),
        }
    return out


def disk_usage(out):
    usage = Counter()
    for path in out.rglob("*"):
        if path.is_file():
            top = path.relative_to(out).parts[0]
            key = top if top in ("png", "thumb", "work") else "json"
            usage[key + "_bytes"] += path.stat().st_size
            usage[key + "_files"] += 1
    total = sum(v for k, v in usage.items() if k.endswith("_bytes"))
    return dict(usage, total_bytes=total, total_mb=round(total / 1e6, 1), limit_mb=LIMIT_MB, under_limit=total / 1e6 <= LIMIT_MB)


def build(assets=ASSETS, out=OUT, sources_path=SOURCES, gold=GOLD, cases=CASES, worker_python=None, procs=4, timeout=90.0, render=True,
          force=False):
    started = time.time()
    assets, out = Path(assets).resolve(), Path(out).resolve()
    if out == assets or assets in out.parents:
        raise SystemExit("--out은 logo-assets 밖이어야 합니다.")
    index = read_json(assets / "INDEX.json", {}) or {}
    sources = read_json(sources_path, {}) or {}
    idx = {e["id"]: e for e in index.get("entities") or []}
    src = {e["id"]: e for e in sources.get("entries") or []}
    files, skipped = inventory(assets, index, sources)
    for row in files:
        row["terms"] = terms_for(idx.get(row["entity_id"]), src.get(row["entity_id"]))
        row["commons_only"] = row["entity_id"] not in idx
        row["usable_for_reference"] = row["logo_kind"] == "logo" and not row["terms"]["blocked"]
        row["dup"] = {}
    out.mkdir(parents=True, exist_ok=True)
    jobs = render_jobs(files)
    results, info = ({}, {"rendered_now": 0, "reused": 0, "elapsed_s": 0.0, "worker": None})
    if render:
        python = Path(worker_python) if worker_python else (WORKER_PYTHON if WORKER_PYTHON.is_file() else Path(sys.executable))
        results, info = run_renders(jobs, assets, out, python, procs, timeout, force)
        info["pruned"] = prune(out, jobs)
    for row in files:
        res = results.get(row.get("render_id")) if row.get("render_id") else None
        row["render"] = render_view(res, row.get("render_id"))
        row["render"]["_color"] = (res or {}).get("color")
        row["flags"], row["sheet_reasons"] = quality_flags(row, res)
        row["tags"] = row["tags"] + pixel_tags(res or {})
        row["color"] = {k: v for k, v in ((res or {}).get("color") or {}).items() if k != "dhash"} or None
        row["dhash"] = ((res or {}).get("color") or {}).get("dhash")
        row["reference_ready"] = row["usable_for_reference"] and row["render"].get("status") == "ok"
    groups = dedup(files)
    for row in files:
        row["render"].pop("_color", None)
    entities = entity_summaries(files, index, sources)
    link = linkage(gold, cases, sources, index, entities)
    count_rows = [r for r in files]
    counts = {
        "files": len(files), "skipped": dict(skipped), "by_area": dict(Counter(r["area"] for r in files)),
        "by_format": dict(Counter(r["format"] for r in files).most_common()),
        "by_format_area": {a: dict(Counter(r["format"] for r in files if r["area"] == a).most_common()) for a in ("original", "extracted", "commons")},
        "by_kind": dict(Counter(r["kind"] for r in files)), "by_logo_kind": dict(Counter(r["logo_kind"] for r in files)),
        "by_render_status": dict(Counter(r["render"].get("status") for r in files)),
        "by_tag": dict(sorted(Counter(t for r in files for t in r["tags"]).items())),
        "by_flag": dict(Counter(f for r in files for f in r["flags"]).most_common()),
        "sheet_reasons": dict(Counter(s for r in files for s in r["sheet_reasons"])),
        "name_fix": {"files": sum(1 for r in files if r["name_fix"]), "by_kind": dict(Counter(h for r in files for h in r["name_fix"]))},
        "provenance": dict(Counter(r["provenance"]["status"] for r in files)),
        "terms": {"hold_entities": sum(1 for e in entities.values() if e["terms"]["hold"]),
                  "review_entities": sum(1 for e in entities.values() if e["terms"]["review"]),
                  "limited_use_entities": sum(1 for e in entities.values() if e["terms"]["limited_use"]),
                  "approval_required_entities": sum(1 for e in entities.values() if e["terms"]["approval_required"]),
                  "blocked_files": sum(1 for r in files if r["terms"]["blocked"] and r["logo_kind"] == "logo")},
        "usable_for_reference": sum(r["usable_for_reference"] for r in count_rows),
        "reference_ready": sum(r["reference_ready"] for r in count_rows),
        "official_logo_like": sum(1 for r in files if r["area"] != "commons" and r["kind"] in ("raster", "vector")),
        "commons_logo_like": sum(1 for r in files if r["area"] == "commons" and r["kind"] in ("raster", "vector")),
        "entities": {"total": len(entities), "in_index": sum(e["in_index"] for e in entities.values()),
                     "commons_only": sum(e["commons_only"] for e in entities.values()), "by_tier": dict(Counter(e["tier"] for e in entities.values()))},
        "dedup": groups["summary"],
    }
    created = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    params = {"long_side": 512, "thumb": 160, "trim_margin": 0.02, "dhash_max_distance": DHASH_MAX, "tiny_short_lt": 64, "low_res_long_lt": 256,
              "huge_mp_gt": 50, "wide_aspect_gt": 4, "a_page_aspect": round(A_RATIO, 4), "a_page_tolerance": 0.03, "sheet_content_lt": 0.5,
              "white_on_transparent_white_frac": 0.8, "render_version": RENDER_VERSION}
    catalog = {
        "schema": "logo-catalog@1", "created_at": created, "assets_root": "data/runtime/logo-assets", "source_list": "data/logo-sources/manifest.json",
        "notes": [
            "비상업 개발용 내부 참조 카탈로그. 원본(logo-assets)은 그대로 두고 파생 PNG만 이 폴더에 만든다. 재배포·공개 저장소 업로드 금지.",
            "path는 디스크의 실제 경로(바꾸지 않음), display_path·display_name은 깨진 이름을 되살린 표시용 이름이다.",
            "usable_for_reference=false: 이용 조건 보류·확인 필요·용도 한정 기관이거나, 로고가 아닌 파일(매뉴얼·캐릭터 단독·묶음·문서).",
            "approval_required는 표시만 한다(참조 제외 조건이 아님). 커먼즈만 있는 기관은 공식 수집을 '협의·승인 필요'로 건너뛴 곳이 많다.",
            "reference_ready=usable_for_reference이고 렌더 성공(status=ok). 파생 PNG는 내용 영역(+여백 2%)으로 잘랐다(render.trim은 원본 쪽 대비 비율).",
            "PostScript AI/EPS는 Ghostscript가 없어 렌더하지 않았다(needs_ghostscript). DOS EPS는 내장 TIFF 미리보기로 대신했다(preview_only).",
            "dHash 근사 중복은 표시만 한다. 사람이 확인해야 한다.",
        ],
        "params": params, "counts": counts, "files": files, "entities": list(entities.values()),
        "dedup": {k: v for k, v in groups.items() if k != "summary"},
    }
    (out / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "linkage.json").write_text(json.dumps(link, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report = render_report(files, results, info, params, out)
    report["elapsed_build_s"] = round(time.time() - started, 1)
    (out / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report["disk"] = disk_usage(out)
    (out / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return catalog, link, report


def render_report(files, results, info, params, out):
    by_render = {}
    for row in files:
        if row.get("render_id") and row["render_id"] not in by_render:
            by_render[row["render_id"]] = row
    rows = [(rid, row, results.get(rid) or {}) for rid, row in by_render.items()]
    secs = sorted(r.get("seconds", 0) for _, _, r in rows if r.get("seconds") is not None)
    q = lambda f: secs[min(len(secs) - 1, int(len(secs) * f))] if secs else None
    failures = [{"path": row["path"], "display_path": row["display_path"], "format": row["format"], "status": res.get("status"), "error": res.get("error")}
                for _, row, res in rows if res.get("status") in ("error", "timeout", "crashed", "bomb", "missing", "unsupported")]
    status_format = defaultdict(Counter)
    for _, row, res in rows:
        status_format[row["format"]][res.get("status", "not_rendered")] += 1
    return {
        "schema": "logo-catalog-render@1", "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "worker": info.get("worker"), "worker_python": info.get("worker_python"), "worker_params": info.get("worker_params"), "params": params,
        "notes": ["렌더는 파일 바이트(sha256)와 기관 폴더가 같으면 한 번만 한다. 같은 실행에서 이미 만든 결과는 다시 쓴다(--force로 다시 렌더)."],
        "jobs": len(rows), "rendered_now": info.get("rendered_now"), "reused": info.get("reused"), "pruned": info.get("pruned", 0),
        "worker_elapsed_s": info.get("elapsed_s"),
        "by_status": dict(Counter(res.get("status", "not_rendered") for _, _, res in rows)),
        "by_format": {fmt: dict(c) for fmt, c in sorted(status_format.items())},
        "by_render_source": dict(Counter(res.get("render_source") for _, _, res in rows if res.get("render_source"))),
        "icc": dict(Counter(res.get("icc") for _, _, res in rows if res.get("icc"))),
        "needs_ghostscript": dict(Counter(row["format"] for _, row, res in rows if res.get("status") == "needs_ghostscript")),
        "out_long_side": dict(Counter("512" if max(res["out_px"]) == 512 else "<512" for _, _, res in rows if res.get("out_px"))),
        "trimmed": sum(1 for _, _, res in rows if res.get("trim") and res["trim"] != [0.0, 0.0, 1.0, 1.0]),
        "seconds": {"total": round(sum(secs), 1), "p50": q(0.5), "p90": q(0.9), "max": secs[-1] if secs else None},
        "slowest": [{"display_path": row["display_path"], "format": row["format"], "seconds": res.get("seconds")}
                    for _, row, res in sorted(rows, key=lambda t: -(t[2].get("seconds") or 0))[:10]],
        "failures": failures[:300], "failure_count": len(failures),
        "quality_flags": dict(Counter(f for row in files for f in row["flags"]).most_common()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="목록·태그·중복·파생 PNG·포스터 대응을 만든다")
    b.add_argument("--assets", type=Path, default=ASSETS)
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--sources", type=Path, default=SOURCES)
    b.add_argument("--gold", type=Path, default=GOLD)
    b.add_argument("--cases", type=Path, default=CASES)
    b.add_argument("--worker-python", type=Path, default=None, help="PyMuPDF가 있는 Python(기본: 시스템 Python 3.12)")
    b.add_argument("--procs", type=int, default=4)
    b.add_argument("--timeout", type=float, default=90.0, help="렌더 파일 하나당 제한 시간(초)")
    b.add_argument("--no-render", action="store_true", help="렌더하지 않고 목록만")
    b.add_argument("--force", action="store_true", help="이전 렌더 결과를 쓰지 않고 다시 렌더")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    catalog, link, report = build(args.assets, args.out, args.sources, args.gold, args.cases, args.worker_python, args.procs, args.timeout,
                                  not args.no_render, args.force)
    c = catalog["counts"]
    lk = link["counts"]["logo_named"]
    print(f"파일 {c['files']}개(원본 {c['by_area'].get('original', 0)}, 압축 해제 {c['by_area'].get('extracted', 0)}, 커먼즈 {c['by_area'].get('commons', 0)}),"
          f" 건너뜀 {c['skipped']}")
    print("형식:", ", ".join(f"{k} {v}" for k, v in c["by_format"].items()))
    print(f"참조 가능 {c['usable_for_reference']}개, 렌더 성공까지 {c['reference_ready']}개 / 렌더 상태 {c['by_render_status']}")
    print(f"이름 복원 {c['name_fix']['files']}개 {c['name_fix']['by_kind']}")
    d = c["dedup"]
    print(f"완전 중복 {d['exact_groups']}묶음, 같은 이름 다른 형식 {d['stem_groups']}묶음, dHash 근사 {d['near_dup_groups']}묶음(래스터만 {d['near_dup_groups_raster_only']})")
    print(f"이용 조건: 보류 {c['terms']['hold_entities']}, 확인 필요 {c['terms']['review_entities']}, 용도 한정 {c['terms']['limited_use_entities']} 기관")
    print(f"포스터 로고(이름 있음) {lk['mentions']}건 → 공식 파일 있는 기관 {lk['official_any']}건, 렌더 성공 참조 있는 기관 {lk['official_ready']}건")
    disk = report["disk"]
    print(f"출력 {args.out}: {disk['total_mb']}MB (한도 {LIMIT_MB}MB, png {disk.get('png_files', 0)}개, thumb {disk.get('thumb_files', 0)}개)")


if __name__ == "__main__":
    main()
