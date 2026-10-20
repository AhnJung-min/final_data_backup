"""Register accepted posters: verify the bytes, keep originals by SHA-256, write 2.1 records and poster stubs.

Inputs are decisions.json from review.html (TourAPI candidates) and worklist.csv rows that have image_url or an
intake/<wl_id>[__N].<ext> file (manual_web). A SHA-256 already registered for another festival stops the batch
before anything is written.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import quote

from .client import MAX_BYTES, inspect_image, store_image
from .poster import validate_poster
from .schema import DATA, new_record, now, read_json, repo_path, repo_relative, validate, write_json
from .settings import CollectionError
from .worklist import build_units, http_url, read_worklist

LICENSES = {"kogl1", "kogl2", "kogl3", "kogl4", "unknown"}
TOURAPI_DATASET = "한국관광공사 국문 관광정보 서비스"
INTAKE = re.compile(r"^(wl-[0-9a-f]{10}(?:-\d+)?)(?:__(\d+))?\.(?:jpe?g|png|webp|gif)$", re.IGNORECASE)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def clean_url(value):
    """Percent-encode pasted non-ASCII characters; existing escapes are kept."""
    value = http_url(value)
    return quote(value, safe=":/?#[]@!$&'()*+,;=%~") if value else None


def tourapi_license(copyright_type):
    match = re.fullmatch(r"Type([1-4])", copyright_type or "")
    return f"kogl{match.group(1)}" if match else "unknown"


def load_registry(path):
    if not Path(path).exists():
        return {"format": "poster-registry/1", "entries": []}
    registry = read_json(path)
    if not isinstance(registry, dict) or not isinstance(registry.get("entries"), list):
        raise CollectionError("등록부 형식이 올바르지 않습니다 (entries 배열 필요).")
    return registry


def known_entries(directories, registered):
    """Images already in the project (e.g. main's data/img/) become entries without a wl_id."""
    entries = []
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            raise CollectionError(f"--known-images 폴더를 찾을 수 없습니다: {directory.name}")
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                digest = sha256(path.read_bytes()).hexdigest()
                if digest not in registered:
                    registered.add(digest)
                    entries.append({"sha256": digest, "wl_id": None, "record_id": None, "provider": "existing",
                                    "file": repo_relative(path), "registered_at": now()})
    return entries


def decision_items(path, units):
    document = read_json(path)
    if not isinstance(document, dict) or document.get("format") != "poster-review/1":
        raise CollectionError("review.html에서 내보낸 decisions.json이 필요합니다.")
    items, rejected = [], 0
    for decision in document.get("decisions", []):
        unit = units.get(decision.get("wl_id"))
        if unit is None:
            raise CollectionError(f"decisions.json의 {decision.get('wl_id')}가 이 실행 폴더에 없습니다. 같은 --run-dir로 만든 review.html인지 확인하세요.")
        if decision.get("decision") == "reject":
            rejected += 1
            continue
        if decision.get("decision") != "accept":
            raise CollectionError(f"{unit['wl_id']}: decision은 accept 또는 reject여야 합니다.")
        # Use the cached API item, not the browser export, as the record's raw response.
        chosen = next((entry for entry in unit["candidates"] if entry["url"] == decision.get("url")), None)
        if chosen is None:
            raise CollectionError(f"{unit['wl_id']}: 선택한 이미지가 실행 폴더의 후보에 없습니다.")
        items.append({
            "unit": unit, "provider": "tourapi", "candidate": chosen, "source_url": chosen["url"], "file": chosen.get("file"),
            "page_url": None, "license": tourapi_license(chosen["copyright"]),
            "license_evidence": f"TourAPI {chosen['endpoint']} cpyrhtDivCd={chosen['copyright']}" if chosen["copyright"] else None,
            "collector": None, "original_filename": None,
        })
    return items, rejected


def worklist_items(rows, units, intake):
    files, problems = {}, []
    if intake and Path(intake).is_dir():
        for path in sorted(Path(intake).iterdir()):
            match = INTAKE.match(path.name)
            if match and path.is_file():
                files.setdefault(match.group(1), []).append(path)
            elif path.is_file():
                problems.append(f"intake/{path.name}: 파일명은 <wl_id>.<확장자> 또는 <wl_id>__2.<확장자>여야 합니다")
    items, no_poster = [], 0
    for row in rows:
        wl_id = row.get("wl_id", "")
        paths = files.pop(wl_id, [])
        url = row.get("image_url", "")
        if row.get("status", "").strip().lower() == "no_poster":
            no_poster += 1
            if url or paths:
                problems.append(f"{wl_id}: status=no_poster인데 image_url 또는 intake 파일이 있습니다")
            continue
        if not url and not paths:
            continue
        unit = units.get(wl_id)
        license = (row.get("license") or "unknown").strip().lower()
        page_url = clean_url(row.get("page_url"))
        if unit is None:
            problems.append(f"{wl_id}: 이 실행 폴더의 worklist에 없는 wl_id입니다")
        elif url and not clean_url(url):
            problems.append(f"{wl_id}: image_url은 http(s) 주소여야 합니다")
        elif page_url is None:
            problems.append(f"{wl_id}: page_url(포스터를 확인한 페이지 주소)이 필요합니다")
        elif license not in LICENSES:
            problems.append(f"{wl_id}: license는 kogl1–kogl4 또는 unknown입니다")
        elif license != "unknown" and not row.get("license_evidence"):
            problems.append(f"{wl_id}: 공공누리 유형에는 license_evidence(표시 위치 등)가 필요합니다")
        else:
            common = {"unit": unit, "provider": "manual_web", "candidate": None, "page_url": page_url, "license": license,
                      "license_evidence": row.get("license_evidence") or None, "collector": row.get("owner") or None}
            if url:
                items.append({**common, "source_url": clean_url(url), "file": None, "original_filename": None})
            items.extend({**common, "source_url": None, "file": str(path), "original_filename": path.name} for path in paths)
    problems.extend(f"intake/{paths[0].name}: worklist.csv에 {wl_id} 행이 없습니다" for wl_id, paths in files.items())
    return items, no_poster, problems


def image_bytes(item, client):
    if item["file"]:
        path = repo_path(item["file"])
        if path.exists():
            if path.stat().st_size > MAX_BYTES:
                raise CollectionError("이미지 파일이 25MB 제한을 초과했습니다.")
            return path.read_bytes()
        if not item["source_url"]:
            raise CollectionError("intake 파일을 찾을 수 없습니다.")
    return client.fetch_image(item["source_url"])


def build_record(item, identity, collector):
    unit, chosen = item["unit"], item["candidate"]
    record = new_record()
    record["id"] = identity["record_id"]
    record["collected_at"] = now()
    record["collector"] = item["collector"] or collector
    record["image"].update({key: identity[key] for key in ["file", "format", "sha256", "width", "height"]}, source_url=item["source_url"])
    source = record["image"]["source"]
    if item["provider"] == "tourapi":
        source.update(
            provider="tourapi", dataset=TOURAPI_DATASET, content_id=unit["content_id"], endpoint=chosen["endpoint"],
            api_field="originimgurl" if chosen["endpoint"] == "detailImage2" else "firstimage", copyright_type=chosen["copyright"],
            retrieved_at=unit["retrieved_at"] if chosen["endpoint"] == "detailImage2" else unit["list_retrieved_at"],
            basis="TourAPI 이미지 후보를 review.html에서 사람이 포스터로 확인", image_name=chosen["name"],
            serial_number=chosen["serial"], raw_response_item=chosen["raw"],
        )
    else:
        source.update(
            provider="manual_web", dataset=None, content_id=None, endpoint=None, api_field=None, copyright_type=None,
            retrieved_at=now(), basis="worklist.csv 수작업 수집: page_url에서 사람이 포스터로 확인", image_name=item["original_filename"],
            serial_number=None, raw_response_item=None,
        )
    source.update(page_url=item["page_url"], license=item["license"], license_evidence=item["license_evidence"])
    record["dataset"]["group_id"] = identity["sha256"]
    record["import_metadata"]["original_filename"] = item["original_filename"]
    record["import_metadata"]["notes"].append(
        f"ingest {unit['wl_id']} ({unit['tier']}): {unit['title']}, 기대 기간 {unit['expected_dates'] or '미상'}. 행사 값은 포스터 추출 후 compare로 연결.")
    validate(record)
    return record


def poster_stub(record):
    document = read_json(DATA / "poster.template.json")
    document["record_id"] = record["id"]
    document["image"] = {key: record["image"][key] for key in ["sha256", "width", "height"]}
    validate_poster(document)
    return document


def ingest(run_dir, client, *, output_dir, registry_path, decisions=None, worklist=None, intake=None,
           known_images=(), collector=None, year=None):
    units = {unit["wl_id"]: unit for unit in build_units(run_dir, year)}
    items, rejected, no_poster, problems = [], 0, 0, []
    if decisions:
        items, rejected = decision_items(decisions, units)
    if worklist and Path(worklist).exists():
        manual, no_poster, problems = worklist_items(read_worklist(worklist), units, intake)
        items += manual
    if problems:
        raise CollectionError(f"worklist·intake 입력 {len(problems)}건을 고친 뒤 다시 실행하세요: " + "; ".join(problems[:10]))
    registry = load_registry(registry_path)
    owners = {entry["sha256"]: entry for entry in registry["entries"]}
    added = known_entries(known_images, set(owners))
    owners.update((entry["sha256"], entry) for entry in added)
    prepared, failures, duplicates, conflicts = {}, [], 0, []
    images = Path(output_dir) / "images"
    for item in items:
        wl_id = item["unit"]["wl_id"]
        try:
            raw = image_bytes(item, client)
            width, height, image_format, extension = inspect_image(raw)
        except CollectionError as error:
            failures.append({"wl_id": wl_id, "error": str(error)})
            continue
        digest = sha256(raw).hexdigest()
        owner = owners.get(digest) or (prepared[digest][0]["unit"] if digest in prepared else None)
        owner_id = owner.get("wl_id") if owner else None
        if owner and owner_id == wl_id:
            duplicates += 1
            continue
        if owner:
            conflicts.append(f"{wl_id} = {owner_id or owner.get('file')}")
            continue
        identity = {"sha256": digest, "record_id": f"festival-{digest[:12]}", "width": width, "height": height,
                    "format": image_format, "file": repo_relative(images / f"{digest}.{extension}")}
        prepared[digest] = (item, identity, raw)
    if conflicts:
        raise CollectionError("같은 이미지(sha256)가 다른 축제 또는 기존 이미지에 이미 있어 아무것도 기록하지 않고 중단했습니다: " + "; ".join(conflicts[:10]))
    outputs = [(build_record(item, identity, collector), raw) for item, identity, raw in prepared.values()]
    for record, raw in outputs:
        store_image(raw, images)
        write_json(Path(output_dir) / "records" / f"{record['id']}.json", record)
        write_json(Path(output_dir) / "posters" / f"{record['id']}.poster.json", poster_stub(record))
    registered_at = now()
    registry["entries"].extend(added)
    registry["entries"].extend({
        "sha256": record["image"]["sha256"], "wl_id": item["unit"]["wl_id"], "record_id": record["id"], "provider": item["provider"],
        "source_url": item["source_url"], "page_url": item["page_url"], "license": item["license"], "content_id": item["unit"]["content_id"],
        "file": record["image"]["file"], "registered_at": registered_at,
    } for (record, _), (item, _, _) in zip(outputs, prepared.values()))
    registry["updated_at"] = registered_at
    write_json(registry_path, registry)
    return {
        "output_dir": str(output_dir), "registry": str(registry_path), "ingested": len(outputs),
        "by_provider": {provider: sum(item["provider"] == provider for item, _, _ in prepared.values()) for provider in ["tourapi", "manual_web"]},
        "already_registered": duplicates, "known_images_added": len(added), "rejected_in_review": rejected,
        "worklist_no_poster": no_poster, "failures": failures,
    }
