"""collect 실행 결과에서 포스터 데이터셋(posters-v2)을 만든다.

축제당 포스터 1장, 바이트·지각 해시 중복 제거, 시·도 층화로 실험용 N장과 예비분을 나누고
원본 바이트를 그대로 data/img-v2에 복사한다. 원본 픽셀은 바꾸지 않는다(공공누리 제3유형).
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import random
import shutil
import subprocess

from PIL import Image, ImageOps

from app.festival_data.normalize import parse_items

ROOT = Path(__file__).resolve().parents[2]
SIDO = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산",
        "36": "세종", "41": "경기", "43": "충북", "44": "충남", "46": "전남", "47": "경북", "48": "경남",
        "50": "제주", "51": "강원", "52": "전북", "12": "전남광주"}
KFES_URL = "https://korean.visitkorea.or.kr/kfes/detail/fstvlDetail.do?cmsCntntsId={}"
EXT = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}
NEAR_DUPLICATE_BITS = 6


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dhash(image, size=8):
    """64비트 차이 해시. 재인코딩·크기 변경된 같은 디자인을 찾는 데만 쓴다."""
    gray = ImageOps.exif_transpose(image).convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = pixels[row * (size + 1) + col], pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def image_facts(path):
    """원본 파일을 읽기만 해서 형식·크기·방향·투명도·해시를 기록한다."""
    raw = Path(path).read_bytes()
    with Image.open(path) as image:
        image.load()
        orientation = int(image.getexif().get(274, 1))
        facts = {"width": image.width, "height": image.height, "format": image.format.lower(), "mode": image.mode,
                 "exif_orientation": orientation, "has_alpha": "A" in image.getbands() or "transparency" in image.info,
                 "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "dhash": dhash(image)}
    facts["aspect_hw"] = round(facts["height"] / facts["width"], 4)
    return facts


def sido(item):
    code = str(item.get("lDongRegnCd") or "")
    return SIDO.get(code[:2]) or SIDO.get(code[:5]) or (item.get("addr1") or "미상").split(" ")[0]


def team_hashes():
    """main의 data/img 포스터 바이트 해시. 중복 수만 세고 파일은 가져오지 않는다."""
    try:
        listing = subprocess.run(["git", "-c", "core.quotepath=off", "ls-tree", "-r", "origin/main", "data/img/"],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    hashes = set()
    for line in listing.splitlines():
        meta, _, name = line.partition("\t")
        blob = meta.split()[2]
        data = subprocess.run(["git", "cat-file", "blob", blob], cwd=ROOT, capture_output=True, check=True).stdout
        hashes.add(hashlib.sha256(data).hexdigest())
    return hashes


def allocate(counts, total):
    """층 크기에 비례해 배정하고 남는 자리는 큰 나머지 순으로 준다(최대 해밀턴 방식)."""
    size = sum(counts.values())
    quotas = {key: total * count / size for key, count in counts.items()}
    shares = {key: min(counts[key], int(quota)) for key, quota in quotas.items()}
    for key in sorted(quotas, key=lambda k: (quotas[k] - int(quotas[k]), k), reverse=True):
        if sum(shares.values()) >= total:
            break
        if shares[key] < counts[key]:
            shares[key] += 1
    return shares


def stratified(rows, total, key, rng):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    for members in groups.values():
        members.sort(key=lambda r: r["content_id"])
        rng.shuffle(members)
    shares = allocate({k: len(v) for k, v in groups.items()}, total)
    chosen = [row for k, members in sorted(groups.items()) for row in members[:shares[k]]]
    return chosen, shares


def build(run_dir, out_dir, manifest_path, total, dev, seed):
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    festivals = {str(item["contentid"]): item for item in parse_items(read_json(run_dir / "tourapi.raw.json"))}
    images = [e for e in read_json(run_dir / "tourapi.images.json")["images"] if e.get("poster_auto") and e.get("image")]
    funnel = {"tourapi_rows": len(festivals), "poster_named_images": len(images)}
    by_festival = defaultdict(list)
    for entry in images:
        by_festival[entry["content_id"]].append(entry)
    rows, extra = [], []
    for content_id, entries in sorted(by_festival.items()):
        entries.sort(key=lambda e: e.get("image_serial") or "")
        extra += entries[1:]
        entry, item = entries[0], festivals[content_id]
        facts = image_facts(ROOT / entry["image"]["file"])
        if facts["sha256"] != entry["image"]["sha256"]:
            raise ValueError(f"{content_id}: 저장 이미지 해시가 수집 기록과 다릅니다.")
        start, end = item.get("eventstartdate"), item.get("eventenddate")
        rows.append({"content_id": content_id, "title": item.get("title"), "sido": sido(item), "addr1": item.get("addr1"),
                     "start": f"{start[:4]}-{start[4:6]}-{start[6:]}", "end": f"{end[:4]}-{end[4:6]}-{end[6:]}",
                     "entry": entry, "facts": facts, "createdtime": item.get("createdtime"), "modifiedtime": item.get("modifiedtime")})
    funnel["festivals_with_poster"] = len(rows)
    funnel["extra_poster_images_same_festival"] = len(extra)
    # 바이트 동일 → 지각 해시 근접 순으로 축제 간 중복을 제거한다.
    kept, duplicates, seen_sha = [], [], {}
    for row in rows:
        sha = row["facts"]["sha256"]
        near = next((k for k in kept if hamming(k["facts"]["dhash"], row["facts"]["dhash"]) <= NEAR_DUPLICATE_BITS), None)
        if sha in seen_sha or near:
            duplicates.append({"content_id": row["content_id"], "title": row["title"],
                               "duplicate_of": (seen_sha.get(sha) or near)["content_id"], "kind": "sha256" if sha in seen_sha else "dhash"})
            continue
        seen_sha[sha] = row
        kept.append(row)
    funnel["after_dedup"] = len(kept)
    if len(kept) < total:
        raise ValueError(f"중복 제거 후 {len(kept)}장으로 목표 {total}장보다 적습니다.")
    rng = random.Random(seed)
    chosen, shares = stratified(kept, total, lambda r: r["sido"], rng)
    chosen_ids = {r["content_id"] for r in chosen}
    reserve = [r for r in kept if r["content_id"] not in chosen_ids]
    dev_rows, dev_shares = stratified(chosen, dev, lambda r: r["sido"], random.Random(seed + 1))
    dev_ids = {r["content_id"] for r in dev_rows}
    chosen.sort(key=lambda r: (r["start"], r["content_id"]))
    reserve.sort(key=lambda r: (r["start"], r["content_id"]))
    team = team_hashes()
    created = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    cases = []
    for prefix, members, folder in (("v2", chosen, "originals"), ("r2", reserve, "reserve")):
        for number, row in enumerate(members, 1):
            case_id = f"{prefix}-{number:03d}"
            facts, entry = row["facts"], row["entry"]
            relative = Path("data/img-v2") / folder / f"{case_id}.{EXT[facts['format']]}"
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / entry["image"]["file"], target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != facts["sha256"]:
                raise ValueError(f"{case_id}: 복사본 해시 불일치")
            split = "reserve" if prefix == "r2" else ("dev" if row["content_id"] in dev_ids else "test")
            cases.append({
                "id": case_id, "image": relative.as_posix(), "sha256": facts["sha256"], "split": split,
                "group_id": f"tour-{row['content_id']}", "label": None,
                "gold": f"data/benchmarks/gold-v2/{case_id}.poster.json" if prefix == "v2" else None,
                "strata": {"sido": row["sido"], "orientation": "portrait" if facts["aspect_hw"] >= 1 else "landscape",
                           "start_month": row["start"][:7]},
                "image_meta": {k: facts[k] for k in ("width", "height", "format", "mode", "exif_orientation", "has_alpha", "bytes", "aspect_hw", "dhash")},
                "source": {"kind": "tourapi", "content_id": row["content_id"], "endpoint": entry["endpoint"],
                           "image_url": entry["source_url"], "image_name": entry["image_name"], "image_serial": entry["image_serial"],
                           "source_page": KFES_URL.format(row["content_id"]), "retrieved_at": entry["retrieved_at"],
                           "tourapi_createdtime": row["createdtime"], "tourapi_modifiedtime": row["modifiedtime"],
                           "in_team_data_img": None if team is None else facts["sha256"] in team},
                "listing": {"title": row["title"], "start": row["start"], "end": row["end"], "addr1": row["addr1"],
                            "note": "TourAPI 목록값. 정답이 아니며 라벨 작업·OCR 입력에 쓰지 않는다."},
                "licence": {"type": "KOGL-3", "cpyrhtDivCd": entry["copyright_type"], "attribution": "한국관광공사 TourAPI",
                            "modification": "prohibited", "internal_eval_only": True, "publish_ok": False},
            })
    manifest = {
        "schema_version": "poster.dataset.v2", "dataset_id": "posters-v2", "created_at": created,
        "selection_method": (f"TourAPI searchFestival2(2026-09-01~12-31, 시작 연도 2026) → detailImage2에서 이름에 '포스터/poster'가 있고 "
                             f"다른 20xx 연도가 없는 첫 이미지(축제당 1장) → sha256·dHash(≤{NEAR_DUPLICATE_BITS}bit) 중복 제거 → "
                             f"시·도 비례 층화 무작위 {total}장(seed {seed}), 그중 dev {dev}장 층화(seed {seed + 1})"),
        "source_run": run_dir.as_posix(), "funnel": {**funnel, "selected": len(chosen), "reserve": len(reserve),
                                                     "dev": len(dev_rows), "test": len(chosen) - len(dev_rows)},
        "allocation": shares, "dev_allocation": dev_shares, "duplicates": duplicates,
        "limitations": ["TourAPI CMS 축소본이 대부분(약 443×627)이라 작은 글자 판독에 한계가 있다.",
                        "이미지명에 '포스터'가 없는 축제의 포스터는 포함하지 않는다(선택 편향).",
                        "목록값(listing)은 TourAPI 기준이며 포스터 인쇄 내용과 다를 수 있다."],
        "cases": cases,
    }
    write_json(manifest_path, manifest)
    sums = "".join(f"{c['sha256']}  {Path(c['image']).relative_to('data/img-v2').as_posix()}\n" for c in cases)
    (ROOT / "data/img-v2/SHA256SUMS").write_text(sums, encoding="utf-8")
    write_index(cases, ROOT / "data/img-v2/index.csv")
    write_gallery(cases, ROOT / "data/img-v2/gallery.html")
    return manifest


def write_index(cases, path):
    columns = ["id", "split", "title", "start", "end", "sido", "addr1", "width", "height", "format", "bytes",
               "content_id", "image_name", "image_url", "source_page", "licence"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for c in cases:
            m, s, l = c["image_meta"], c["source"], c["listing"]
            writer.writerow([c["id"], c["split"], l["title"], l["start"], l["end"], c["strata"]["sido"], l["addr1"],
                             m["width"], m["height"], m["format"], m["bytes"], s["content_id"], s["image_name"],
                             s["image_url"], s["source_page"], "공공누리 제3유형(출처표시·변경금지) · 한국관광공사"])


def write_gallery(cases, path):
    """로컬 확인용 목록 페이지. 원본을 그대로(잘림 없이) 보여 준다."""
    cards = []
    for c in cases:
        l = c["listing"]
        cards.append(f'<figure><a href="{html.escape(Path(c["image"]).relative_to("data/img-v2").as_posix())}">'
                     f'<img loading="lazy" src="{html.escape(Path(c["image"]).relative_to("data/img-v2").as_posix())}" alt=""></a>'
                     f'<figcaption><b>{c["id"]}</b> · {c["split"]}<br>{html.escape(l["title"] or "")}<br>{l["start"]} ~ {l["end"]} · {html.escape(c["strata"]["sido"])}</figcaption></figure>')
    page = ("<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>posters-v2 갤러리</title><style>body{font:14px/1.5 system-ui,'Malgun Gothic',sans-serif;margin:16px;background:#f6f7f8;color:#1f2328}"
            "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}figure{margin:0;background:#fff;border:1px solid #d9dde2;border-radius:6px;padding:8px}"
            "img{width:100%;height:220px;object-fit:contain;background:#eceef1}figcaption{font-size:12px;margin-top:6px}</style>"
            "<h1>posters-v2 (내부 확인용)</h1><p>출처: ⓒ한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지). 원본을 수정하지 않았으며 외부에 공개하지 않습니다.</p>"
            f"<main>{''.join(cards)}</main></html>")
    Path(path).write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data/img-v2")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/benchmarks/posters-v2.json")
    parser.add_argument("--total", type=int, default=200)
    parser.add_argument("--dev", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20261019)
    args = parser.parse_args()
    manifest = build(args.run_dir, args.out_dir, args.manifest, args.total, args.dev, args.seed)
    print(json.dumps(manifest["funnel"], ensure_ascii=False))


if __name__ == "__main__":
    main()
