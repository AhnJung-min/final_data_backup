"""로고·CI 카탈로그 전처리(app/poster_ocr/logo_catalog.py, logo_render_worker.py) 시험. 모두 tmp_path의 작은 합성 파일만 쓴다."""
from __future__ import annotations

import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

import pytest
from PIL import Image, ImageDraw

from app.poster_ocr import logo_catalog as lc
from app.poster_ocr import logo_render_worker as worker

WORKER = Path(lc.__file__).with_name("logo_render_worker.py")


def has_fitz(python):
    if not Path(python).is_file():
        return False
    probe = subprocess.run([str(python), "-I", "-c", "import pymupdf"], capture_output=True)
    return probe.returncode == 0


def png_bytes(size, box, fill, mode="RGBA", bg=(0, 0, 0, 0), fmt="PNG"):
    image = Image.new(mode, size, bg)
    ImageDraw.Draw(image).rectangle(box, fill=fill)
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def test_sniff_formats():
    assert lc.sniff(b"%PDF-1.5 ...", ".ai") == "ai_pdf"
    assert lc.sniff(b"%PDF-1.5 ...", ".pdf") == "pdf"
    assert lc.sniff(b"%!PS-Adobe-3.0\n", ".ai") == "ai_ps"
    assert lc.sniff(b"%!PS-Adobe-3.0 EPSF-3.0\n", ".eps") == "eps"
    assert lc.sniff(b"\xc5\xd0\xd3\xc6" + b"\0" * 26, ".eps") == "eps_dosbin"
    assert lc.sniff(b"\x89PNG\r\n\x1a\n", ".jpg") == "png"
    assert lc.sniff(b"\xff\xd8\xff\xe0", ".jpg") == "jpeg"
    assert lc.sniff(b"<?xml version='1.0'?>\n<svg xmlns='x'>", ".svg") == "svg"
    assert lc.sniff(b"PK\x03\x04", ".zip") == "zip"
    assert lc.kind_of("psd") == "raster" and lc.kind_of("ai_ps") == "vector" and lc.kind_of("zip") == "container"


def test_repair_name_variants():
    latin1 = "삼척시 상징물.zip".encode("utf-8").decode("latin1")
    double = "울산광역시 심벌마크(AI)".encode("utf-8").decode("latin1").encode("utf-8").decode("cp949")
    cp437 = __import__("unicodedata").normalize("NFD", "가로형로고.jpg").encode("utf-8").decode("cp437")
    assert lc.repair_name(latin1) == ("삼척시 상징물.zip", ["mojibake_latin1"])
    assert lc.repair_name(double) == ("울산광역시 심벌마크(AI)", ["mojibake_cp949+latin1"])
    assert lc.repair_name(cp437) == ("가로형로고.jpg", ["mojibake_cp437", "nfd"])
    assert lc.repair_name("%EC%9B%8C%EB%93%9C%EB%A7%88%ED%81%AC.zip") == ("워드마크.zip", ["percent"])
    for clean in ("국·영문 심볼.ai", "Café.png", "심볼_영월군.ai", "logo (1).png"):
        assert lc.repair_name(clean) == (clean, [])
    fixed, how = lc.repair_path("lg__가/extracted/" + double + "/심볼.ai")
    assert fixed == "lg__가/extracted/울산광역시 심벌마크(AI)/심볼.ai" and how == ["mojibake_cp949+latin1"]


def test_path_tags_dictionary():
    tags = lc.path_tags("ci/심볼마크_국문_가로형_흑백")
    assert {"comp:symbol", "lang:ko", "layout:horizontal", "color:mono"} <= set(tags)
    tags = lc.path_tags("BI/Signature_ENG_vertical_white")
    assert {"comp:signature", "comp:brand", "lang:en", "layout:vertical", "color:inverse"} <= set(tags)
    assert {"lang:mixed", "comp:signature"} <= set(lc.path_tags("국영문 조합형 기본"))
    assert "comp:symbol" not in lc.path_tags("워드마크") and "comp:logotype" in lc.path_tags("워드마크")
    assert lc.norm_stem("심볼_국문(RGB).ai") == lc.norm_stem("심볼 국문.png")
    assert lc.pixel_tags({"color": {"content_px": 10, "chroma_frac": 0.0, "white_frac": 0.9, "black_frac": 0.0}, "page": {"bg": "transparent"}}) == \
        ["px:achromatic", "px:white", "px:bg:transparent"]


def test_resolve_org_aliases():
    names = {"lg:경기도:고양시": {"name": "고양시"}, "lg:강원특별자치도:고성군": {"name": "고성군"}, "lg:경상남도:고성군": {"name": "고성군"},
             "lg:경기도": {"name": "경기도"}, "org:예천문화관광재단": {"name": "예천문화관광재단"},
             "lg:인천광역시:서해구": {"name": "서해구", "legacy": ["GOV:인천광역시|서구"]},
             "org:진주문화관광재단": {"name": "진주문화관광재단"}, "org:진주문화예술재단": {"name": "진주문화예술재단", "current_name": "진주문화관광재단"}}
    exact, short = lc.alias_table(names)
    ministries = {lc.norm_org("문화체육관광부")}

    def resolve(name, sido=None, poster=()):
        return lc.resolve_org(name, sido, {lc.norm_org(n) for n in poster}, exact, short, ministries)

    assert resolve("고양특례시") == ("exact", {"lg:경기도:고양시"})
    assert resolve("고양시청") == ("suffix", {"lg:경기도:고양시"})
    assert resolve("고양시 문화관광과") == ("suffix", {"lg:경기도:고양시"})
    assert resolve("고양") == ("short", {"lg:경기도:고양시"})
    assert resolve("경기") == ("short", {"lg:경기도"})
    assert resolve("고성군")[0] == "ambiguous"
    assert resolve("고성군", "경상남도") == ("exact", {"lg:경상남도:고성군"})
    assert resolve("고성군", None, ["강원특별자치도", "고성군"]) == ("exact", {"lg:강원특별자치도:고성군"})
    assert resolve("(재)예천문화관광재단") == ("exact", {"org:예천문화관광재단"})
    assert resolve("인천광역시 서구") == ("exact", {"lg:인천광역시:서해구"})
    assert resolve("진주문화관광재단") == ("exact", {"org:진주문화관광재단"})  # 자기 이름이 현재 이름 별칭보다 먼저
    assert resolve("진주문화예술재단") == ("exact", {"org:진주문화예술재단"})
    assert resolve("예산군의회") == ("council", set())
    assert resolve("문화체육관광부") == ("gov_symbol", {"gov:정부상징"})
    assert resolve("국가유산청") == ("central_agency", set())
    assert resolve("어떤 기업") == ("unmatched", set())
    assert resolve(None) == ("no_name", set())


def make_assets(root):
    """기관 둘(정상·확인 필요)과 커먼즈만 있는 기관 하나의 합성 참조 세트."""
    assets = root / "logo-assets"
    ga = assets / "lg__테스트도__가군"
    logo = png_bytes((800, 400), (200, 100, 600, 300), (30, 90, 200, 255))
    (ga / "extracted/symbol").mkdir(parents=True)
    (ga / "original").mkdir(parents=True)
    with zipfile.ZipFile(ga / "original/symbol.zip", "w") as bundle:
        bundle.writestr("심볼_국문.png", logo)
    (ga / "extracted/symbol/심볼_국문.png").write_bytes(logo)
    (ga / "original/copy.png").write_bytes(logo)  # 완전 중복
    (ga / "original/심볼_국문.jpg").write_bytes(png_bytes((400, 200), (100, 50, 300, 150), (30, 90, 200), "RGB", (255, 255, 255), "JPEG"))
    (ga / "original/tiny.png").write_bytes(png_bytes((40, 30), (5, 5, 35, 25), (200, 30, 30, 255)))
    (ga / "original/wide_slogan.png").write_bytes(png_bytes((1000, 100), (10, 10, 990, 90), (0, 0, 0, 255)))
    (ga / "original/reverse_white.png").write_bytes(png_bytes((300, 300), (50, 50, 250, 250), (255, 255, 255, 255)))
    broken = "심볼_가로.png".encode("utf-8").decode("latin1")
    (ga / "original" / broken).write_bytes(png_bytes((300, 200), (20, 20, 280, 180), (20, 160, 60, 255)))
    (ga / "original/BS 05 흑백규정.png").write_bytes(png_bytes((300, 200), (20, 20, 280, 180), (0, 0, 0, 255)))
    tif = io.BytesIO()
    preview = Image.new("L", (120, 80), 255)
    ImageDraw.Draw(preview).rectangle((30, 20, 90, 60), fill=0)
    preview.save(tif, "TIFF")
    ps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 120 80\n"
    (ga / "original/mark.eps").write_bytes(struct.pack("<4s6IH", b"\xc5\xd0\xd3\xc6", 30, len(ps), 0, 0, 30 + len(ps), len(tif.getvalue()), 0xFFFF)
                                           + ps + tif.getvalue())
    (ga / "original/old.ai").write_bytes(b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator\n")
    (ga / "_excluded/extracted").mkdir(parents=True)
    (ga / "_excluded/extracted/manual_page.png").write_bytes(logo)
    prefix = "data/runtime/logo-assets/lg__테스트도__가군"
    (ga / "provenance.json").write_text(json.dumps({
        "entity": "lg:테스트도:가군", "page": "https://example.invalid/ci",
        "files": [{"label": "심볼 PNG 다운로드", "source_url": "https://example.invalid/symbol.zip", "saved_path": f"{prefix}/original/symbol.zip"},
                  {"label": "사본", "source_url": "https://example.invalid/copy.png", "saved_path": f"{prefix}/original/copy.png"}],
        "extracted": [{"from": f"{prefix}/original/symbol.zip", "to": f"{prefix}/extracted/symbol",
                       "entries": [{"name": "심볼_국문.png", "saved_path": f"{prefix}/extracted/symbol/심볼_국문.png"}]}]}, ensure_ascii=False), encoding="utf-8")
    na = assets / "lg__테스트도__나군/original"
    na.mkdir(parents=True)
    (na / "logo.png").write_bytes(png_bytes((300, 300), (40, 40, 260, 260), (200, 120, 0, 255)))
    (assets / "lg__테스트도__나군/provenance.json").write_text("{}", encoding="utf-8")
    da = assets / "commons/lg__테스트도__다군"
    da.mkdir(parents=True)
    (da / "Flag of Da.png").write_bytes(png_bytes((300, 200), (0, 0, 299, 199), (10, 120, 220, 255)))
    (assets / "commons/provenance.json").write_text(json.dumps({"files": [
        {"id": "lg:테스트도:다군", "role": "flag", "title": "File:Flag of Da.png", "saved_path": "data/runtime/logo-assets/commons/lg__테스트도__다군/Flag of Da.png",
         "license_short": "Public domain", "restrictions": "insignia"}]}, ensure_ascii=False), encoding="utf-8")
    (assets / "INDEX.json").write_text(json.dumps({"entities": [
        {"id": "lg:테스트도:가군", "name": "가군", "folder": "data/runtime/logo-assets/lg__테스트도__가군", "status": "collected", "verdict": "ok",
         "terms_review": None, "dev_use": "가능", "terms_decision": "collect", "issues": []},
        {"id": "lg:테스트도:나군", "name": "나군", "folder": "data/runtime/logo-assets/lg__테스트도__나군", "status": "collected", "verdict": "ok",
         "terms_review": "확인 필요: 사용 승인 문구", "dev_use": "표시 없음(확인 권장)", "terms_decision": "collect", "issues": ["x"]}]},
        ensure_ascii=False), encoding="utf-8")
    sources = root / "manifest.json"
    sources.write_text(json.dumps({"entries": [
        {"id": "lg:테스트도:가군", "kind": "sigungu", "name": "가군", "full_name": "테스트도 가군", "dev_use": {"status": "가능"}, "license": None},
        {"id": "lg:테스트도:나군", "kind": "sigungu", "name": "나군", "full_name": "테스트도 나군", "dev_use": {"status": "표시 없음(확인 권장)"},
         "license": {"approval_required": True, "category": "승인 필요"}},
        {"id": "lg:테스트도:다군", "kind": "sigungu", "name": "다군", "full_name": "테스트도 다군", "dev_use": {"status": "협의·승인 필요"}, "license": None},
        {"id": "org:테스트재단", "kind": "foundation", "name": "테스트재단", "dev_use": {"status": "가능"}, "license": None,
         "data_frequency": {"gold_posters": 2, "std_records": 1, "std_records_2026plus": 0}}]}, ensure_ascii=False), encoding="utf-8")
    gold = root / "gold"
    gold.mkdir()
    items = [{"name": n, "form": f, "legibility": "legible", "roles": ["host"]} for n, f in
             (("가군", "logo"), ("나군청", "logo"), ("다군", "logo"), ("테스트재단", "logo"), ("어떤 기업", "logo"), (None, "logo"), ("가군", "text"))]
    (gold / "t-001.poster.json").write_text(json.dumps({"organizations": {"items": items}}, ensure_ascii=False), encoding="utf-8")
    cases = root / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "t-001", "split": "dev", "strata": {"sido": "경기"}}]}), encoding="utf-8")
    return assets, sources, gold, cases


def test_build_end_to_end(tmp_path):
    assets, sources, gold, cases = make_assets(tmp_path)
    before = sorted(p.relative_to(assets).as_posix() for p in assets.rglob("*"))
    out = tmp_path / "catalog"
    catalog, link, report = lc.build(assets, out, sources, gold, cases, worker_python=sys.executable, procs=1, timeout=60)
    assert sorted(p.relative_to(assets).as_posix() for p in assets.rglob("*")) == before  # 원본은 그대로
    files = {f["display_name"]: f for f in catalog["files"]}
    counts = catalog["counts"]
    assert counts["skipped"] == {"excluded_files": 1, "zip_extracted": 1}
    assert counts["files"] == 12 and "manual_page.png" not in files
    assert files["심볼_가로.png"]["name_fix"] == ["mojibake_latin1"] and "심볼" not in files["심볼_가로.png"]["path"]
    assert files["심볼_국문.png"]["provenance"]["kind"] == "zip_member" and files["심볼_국문.png"]["provenance"]["label"] == "심볼 PNG 다운로드"
    assert files["mark.eps"]["format"] == "eps_dosbin" and files["mark.eps"]["render"]["render_source"] == "eps_tiff_preview"
    assert files["old.ai"]["render"]["status"] == "needs_ghostscript" and "needs_ghostscript" in files["old.ai"]["flags"]
    assert {"tiny", "low_res"} <= set(files["tiny.png"]["flags"])
    assert "wide" in files["wide_slogan.png"]["flags"]
    assert "white_on_transparent" in files["reverse_white.png"]["flags"] and "px:white" in files["reverse_white.png"]["tags"]
    assert files["BS 05 흑백규정.png"]["logo_kind"] == "manual" and not files["BS 05 흑백규정.png"]["usable_for_reference"]
    assert files["logo.png"]["terms"]["review"] and not files["logo.png"]["usable_for_reference"]
    flag = files["Flag of Da.png"]
    assert flag["commons_only"] and "comp:flag" in flag["tags"] and flag["terms"]["approval_required"] and flag["usable_for_reference"]
    ready = files["심볼_국문.png"]
    assert ready["reference_ready"] and {"comp:symbol", "lang:ko", "px:hue:blue", "px:bg:transparent"} <= set(ready["tags"])
    with Image.open(out / ready["render"]["png"]) as png:
        assert 400 <= max(png.size) <= 512 and png.mode == "RGBA"  # 내용 400px + 여백(원본보다 크게 늘리지 않음)
    with Image.open(out / ready["render"]["thumb"]) as thumb:
        assert max(thumb.size) == 160
    exact = catalog["dedup"]["exact_groups"]
    assert len(exact) == 1 and len(exact[0]["files"]) == 2
    stems = [g for g in catalog["dedup"]["stem_groups"] if g["stem"] == lc.norm_stem("심볼_국문.png")]
    assert len(stems) == 1 and stems[0]["reason"] == "largest_png"
    assert {f["file_id"] for f in catalog["files"] if f["display_name"] == "심볼_국문.png"} == {stems[0]["representative"]}
    near = catalog["dedup"]["near_dup_groups"]
    assert any({"png", "jpeg"} <= set(g["formats"]) for g in near)  # 같은 로고 PNG·JPEG
    ents = {e["entity_id"]: e for e in catalog["entities"]}
    assert ents["lg:테스트도:가군"]["tier"] == "official_ready" and ents["lg:테스트도:나군"]["tier"] == "official_blocked"
    assert ents["lg:테스트도:다군"]["tier"] == "commons_only" and ents["lg:테스트도:다군"]["commons_only"]
    lk = link["counts"]["logo_named"]
    assert lk["mentions"] == 5 and lk["by_tier"] == {"official_ready": 1, "official_blocked": 1, "commons_only": 1, "not_collected": 1, "unmatched": 1}
    assert link["counts"]["selected_via_gold"]["logo_mentions"] == 1 and link["counts"]["logo_unnamed"] == 1
    by_name = {m["name"]: m for m in link["mentions"] if m["form"] == "logo" and m["name"]}
    assert by_name["나군청"]["match"] == "suffix"
    assert report["by_status"]["ok"] >= 10 and report["disk"]["total_bytes"] > 0 and report["disk"]["under_limit"]
    assert json.loads((out / "catalog.json").read_text(encoding="utf-8"))["schema"] == "logo-catalog@1"
    # 다시 돌리면 이전 렌더를 그대로 쓴다.
    _, _, again = lc.build(assets, out, sources, gold, cases, worker_python=sys.executable, procs=1, timeout=60)
    assert again["rendered_now"] == 0 and again["reused"] == report["jobs"]


def test_worker_timeout_and_crash_bookkeeping(tmp_path, monkeypatch):
    """감독 프로세스: 결과 JSON을 쓰고, 지원하지 않는 형식·없는 파일도 결과로 남긴다."""
    good = tmp_path / "a.png"
    good.write_bytes(png_bytes((200, 100), (20, 20, 180, 80), (0, 0, 0, 255)))
    jobs = {"jobs": [{"id": "e/1", "path": str(good), "format": "png", "png": "png/e/1.png", "thumb": "thumb/e/1.png", "bytes": 1},
                     {"id": "e/2", "path": str(tmp_path / "missing.png"), "format": "png", "png": "png/e/2.png", "thumb": "thumb/e/2.png", "bytes": 1},
                     {"id": "e/3", "path": str(good), "format": "cdr", "png": "png/e/3.png", "thumb": "thumb/e/3.png", "bytes": 1}]}
    (tmp_path / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", str(WORKER), str(tmp_path / "jobs.json"), str(tmp_path / "out"), "--procs", "2", "--timeout", "30"],
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    result = json.loads((tmp_path / "render_result.json").read_text(encoding="utf-8"))
    status = {r["id"]: r["status"] for r in result["results"]}
    assert status == {"e/1": "ok", "e/2": "error", "e/3": "unsupported"}
    assert result["worker"]["isolated"] and (tmp_path / "out/png/e/1.png").is_file()


def test_worker_color_measure_in_process():
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((50, 25, 150, 75), fill=(220, 20, 20, 255))
    stats = worker.page_stats(image)
    assert stats["bg"] == "transparent" and stats["bbox"] == (50, 25, 151, 76)
    color = worker.color_stats(image, stats["bg"], stats["bg_ref"])
    assert color["chroma_frac"] == 1.0 and next(iter(color["hues"])) == "red" and color["black_frac"] == 0.0 and len(color["dhash"]) == 16


@pytest.mark.skipif(not has_fitz(lc.WORKER_PYTHON), reason="PyMuPDF가 있는 시스템 Python이 없음")
def test_worker_vector_render(tmp_path):
    svg = tmp_path / "s.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect x="100" y="50" width="200" height="100" fill="#1d5fb3"/></svg>',
                   encoding="utf-8")
    pdf = tmp_path / "p.ai"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page"
                    b"/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R>>endobj 4 0 obj<</Length 35>>stream\n0 0.5 0 rg 200 300 100 100 re f\nendstream"
                    b" endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    css = tmp_path / "c.svg"
    css.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><defs><style>.st0{fill:#e01010;}</style></defs>'
                   '<circle class="st0" cx="100" cy="100" r="60"/></svg>', encoding="utf-8")
    off = tmp_path / "off.ai"  # 작업물이 아트보드(MediaBox) 오른쪽 밖에 있는 AI
    off.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page"
                    b"/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R>>endobj 4 0 obj<</Length 35>>stream\n0 0 0.8 rg 900 300 120 80 re f\nendstream"
                    b" endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    jobs = {"jobs": [{"id": "v/1", "path": str(svg), "format": "svg", "png": "png/v/1.png", "thumb": "thumb/v/1.png"},
                     {"id": "v/2", "path": str(pdf), "format": "ai_pdf", "png": "png/v/2.png", "thumb": "thumb/v/2.png"},
                     {"id": "v/3", "path": str(css), "format": "svg", "png": "png/v/3.png", "thumb": "thumb/v/3.png"},
                     {"id": "v/4", "path": str(off), "format": "ai_pdf", "png": "png/v/4.png", "thumb": "thumb/v/4.png"}]}
    (tmp_path / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    proc = subprocess.run([str(lc.WORKER_PYTHON), "-I", str(WORKER), str(tmp_path / "jobs.json"), str(tmp_path / "out"), "--procs", "1"], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    results = {r["id"]: r for r in json.loads((tmp_path / "render_result.json").read_text(encoding="utf-8"))["results"]}
    assert results["v/1"]["status"] == "ok" and results["v/1"]["page_pt"] == [400.0, 200.0] and max(results["v/1"]["out_px"]) == 512
    assert results["v/2"]["pages"] == 1 and results["v/2"]["page_pt"] == [595.0, 842.0] and results["v/2"]["page"]["bbox_frac"] < 0.05
    flags, reasons = lc.quality_flags({"format": "ai_pdf", "provenance": {"status": "path"}}, results["v/2"])
    assert "sheet_like" in flags and set(reasons) == {"a_page_aspect", "content_lt_50pct"}
    assert results["v/3"]["svg_css_rules"] == 1 and next(iter(results["v/3"]["color"]["hues"])) == "red"  # CSS 클래스 색이 살아남
    assert results["v/4"]["status"] == "ok" and results["v/4"]["off_artboard"] and next(iter(results["v/4"]["color"]["hues"])) == "blue"
    flags, _ = lc.quality_flags({"format": "ai_pdf", "provenance": {"status": "path"}}, results["v/4"])
    assert "off_artboard" in flags


def pa_tiff(width, height, index, alpha):
    """Pillow가 못 여는 팔레트+알파(무압축) TIFF를 직접 쓴다(Photoshop EPS 미리보기 모양)."""
    pixels = bytes(v for pair in zip(index, alpha) for v in pair)
    cmap = [0] * 768
    cmap[1], cmap[256 + 2], cmap[512 + 3] = 65535, 65535, 65535  # 1=빨강, 2=초록, 3=파랑
    entries = [(256, 3, 1, width), (257, 3, 1, height), (258, 3, 2, None), (259, 3, 1, 1), (262, 3, 1, 3), (273, 4, 1, None), (277, 3, 1, 2),
               (278, 3, 1, height), (279, 4, 1, len(pixels)), (284, 3, 1, 1), (320, 3, 768, None), (338, 3, 1, 1)]
    ifd_size = 2 + 12 * len(entries) + 4
    cmap_off = 8 + ifd_size
    strip_off = cmap_off + 768 * 2
    out = bytearray(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(entries)))
    for tag, kind, count, value in entries:
        if tag == 258:
            out += struct.pack("<HHIHH", tag, kind, count, 8, 8)
        elif tag == 273:
            out += struct.pack("<HHII", tag, kind, count, strip_off)
        elif tag == 320:
            out += struct.pack("<HHII", tag, kind, count, cmap_off)
        elif kind == 4:
            out += struct.pack("<HHII", tag, kind, count, value)
        else:
            out += struct.pack("<HHIH2x", tag, kind, count, value)
    out += struct.pack("<I", 0) + struct.pack("<768H", *cmap) + pixels
    return bytes(out)


def test_palette_alpha_tiff_preview(tmp_path):
    data = pa_tiff(4, 2, [1, 2, 3, 0, 0, 1, 1, 0], [255, 255, 255, 0, 0, 255, 128, 0])
    with pytest.raises(Exception):
        Image.open(io.BytesIO(data)).load()  # Pillow 단독으로는 못 연다
    image = worker.tiff_palette_alpha(data)
    assert image.mode == "RGBA" and image.size == (4, 2)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255) and image.getpixel((1, 0)) == (0, 255, 0, 255) and image.getpixel((2, 1))[3] == 128
    ps = b"%!PS-Adobe-3.0 EPSF-3.0\n"
    eps = tmp_path / "pa.eps"
    eps.write_bytes(struct.pack("<4s6IH", b"\xc5\xd0\xd3\xc6", 30, len(ps), 0, 0, 30 + len(ps), len(data), 0xFFFF) + ps + data)
    res = worker.process({"id": "p/1", "path": str(eps), "format": "eps_dosbin", "png": "png/p/1.png", "thumb": "thumb/p/1.png"}, tmp_path)
    assert res["status"] == "ok" and res["render_source"] == "eps_tiff_preview" and res["mode"] == "PA"


def test_inline_svg_css_is_text_only():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg"><style><![CDATA[ /* c */ .a, rect.b {fill:#123456} .c{stroke:red;stroke-width:2} ]]></style>'
           b'<rect class="b" style="opacity:.5" width="1" height="1"/><path class="a c" d="M0 0"/><g class="none"/>'
           b'<stop style="stop-color:rgb(100%,50%,0%)"/><image href="data:img/png;base64,AAAA"/></svg>')
    out, rules = worker.inline_svg_css(svg)
    text = out.decode("utf-8")
    assert rules == 3
    assert '<rect class="b" style="fill:#123456;opacity:.5"' in text
    assert 'class="a c" d="M0 0" style="fill:#123456;stroke:red;stroke-width:2"' in text
    assert '<g class="none"/>' in text and "stop-color:#ff8000" in text and "data:image/png" in text
    assert worker.inline_svg_css(b'<svg><rect fill="#fff"/></svg>') is None
