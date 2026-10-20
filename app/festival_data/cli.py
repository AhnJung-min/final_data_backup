"""CLI for live collection, offline normalization, and poster/API comparison."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from uuid import uuid4

from .client import ApiClient, valid_content_id
from .compare import reconcile
from .handoff import attach_ocr_result, attach_poster
from .ingest import ingest
from .match import match_festivals
from .normalize import iso_date, normalize_response, parse_items, text, title_key
from .poster import poster_findings, validate_poster
from .schema import ROOT, now, read_json, repo_path, repo_relative, validate, validate_handoff, write_json
from .settings import ApiLimitError, CollectionError, Settings
from .review import make_review
from .worklist import festival_years, make_worklist, poster_hint


def positive(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.") from None
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.")
    return number


def parser():
    command = argparse.ArgumentParser(description="축제 API 수집·공통 스키마 정규화·포스터 비교")
    command.add_argument("--env-file", type=Path, help="프로젝트 .env 대신 읽을 파일")
    subcommands = command.add_subparsers(dest="command", required=True)
    collect = subcommands.add_parser("collect", help="전국문화축제표준데이터 수집")
    collect.add_argument("--name", help="축제명. API 조회 후 정규화된 이름으로 다시 확인")
    collect.add_argument("--year", type=int, help="행사 시작 연도로 결과 필터링")
    collect.add_argument("--output-dir", type=Path, help="지정하지 않으면 실행별 폴더 생성")
    collect.add_argument("--tourapi", action="store_true", help="TourAPI 행사 목록과 이미지 후보도 수집")
    collect.add_argument("--detail-images", action="store_true", help="TourAPI 상세 이미지 목록도 수집 (--tourapi 필요)")
    collect.add_argument("--download-images", action="store_true", help="이미지 후보 파일 다운로드 (--tourapi 필요)")
    collect.add_argument("--start-date", help="TourAPI 조회 시작일 YYYY-MM-DD. 기본: --year의 1월 1일")
    collect.add_argument("--end-date", help="TourAPI 조회 종료일 YYYY-MM-DD. 기본: --year의 12월 31일")
    collect.add_argument("--resume", action="store_true", help="--output-dir의 저장된 행사 목록과 cache/tourapi-detail을 재사용하고 남은 축제만 조회")
    collect.add_argument("--max-calls", type=positive, help="이번 실행의 API 호출 상한(재시도 포함, 이미지 다운로드 제외). 도달하면 저장 후 중단")
    collect.add_argument("--poster-hints-only", action="store_true", help="이미지명에 포스터/poster가 있고 행사 연도 외의 20xx가 없는 상세 이미지만 다운로드")
    normalize = subcommands.add_parser("normalize", help="저장된 표준데이터 응답을 오프라인 정규화")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument("--name")
    normalize.add_argument("--year", type=int)
    compare = subcommands.add_parser("compare", help="포스터 추출 JSON과 정규화된 API JSON 비교")
    compare.add_argument("--poster", type=Path, required=True)
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    check = subcommands.add_parser("validate", help="완성 레코드의 JSON Schema와 일정 검증")
    check.add_argument("file", type=Path)
    kind = check.add_mutually_exclusive_group()
    kind.add_argument("--ocr-result", action="store_true", help="공통 레코드 대신 OCR 인계 JSON 검증")
    kind.add_argument("--poster", action="store_true", help="포스터 추출 표준 1.0 JSON 검증과 확인 필요 항목 출력")
    attach = subcommands.add_parser("attach-ocr", help="팀원의 OCR 인계 JSON을 공통 레코드에 연결")
    attach.add_argument("--record", type=Path, required=True)
    attach.add_argument("--ocr", type=Path, required=True)
    attach.add_argument("--output", type=Path, required=True)
    attach_standard = subcommands.add_parser("attach-poster", help="포스터 추출 표준 JSON을 공통 레코드 poster로 연결")
    attach_standard.add_argument("--record", type=Path, required=True)
    attach_standard.add_argument("--poster", type=Path, required=True)
    attach_standard.add_argument("--output", type=Path, required=True)
    plan = subcommands.add_parser("worklist", help="collect 실행 폴더로 T1/T2/T4 작업 목록 worklist.csv 생성. 사람이 입력한 열은 보존")
    plan.add_argument("--run-dir", type=Path, required=True, help="collect --tourapi --detail-images의 --output-dir")
    plan.add_argument("--output", type=Path, help="기본: <run-dir>/worklist.csv")
    plan.add_argument("--year", type=int, help="기본: collect의 --year")
    plan.add_argument("--owners", help="쉼표로 구분한 담당자. 시도 단위로 T4+T2 작업량이 고르게 owner를 채움")
    plan.add_argument("--priority", type=Path, help="먼저 처리할 축제명 파일(UTF-8, 한 줄에 하나). 예: 문화관광축제 목록")
    review = subcommands.add_parser("review", help="T1/T2 후보 검토용 review.html 생성. 이미지는 원본 URL로만 표시")
    review.add_argument("--run-dir", type=Path, required=True)
    review.add_argument("--output", type=Path, help="기본: <run-dir>/review.html")
    review.add_argument("--year", type=int)
    register = subcommands.add_parser("ingest", help="채택한 포스터를 해시 검증 후 공통 레코드·포스터 초안·등록부에 기록")
    register.add_argument("--run-dir", type=Path, required=True)
    register.add_argument("--decisions", type=Path, help="review.html에서 내보낸 decisions.json")
    register.add_argument("--worklist", type=Path, help="기본: <run-dir>/worklist.csv (있을 때)")
    register.add_argument("--intake", type=Path, help="직접 저장한 <wl_id>.<확장자> 폴더. 기본: <run-dir>/intake")
    register.add_argument("--output-dir", type=Path, help="기본: <run-dir>/ingest")
    register.add_argument("--registry", type=Path, help="sha256 등록부. 기본: FESTIVAL_OUTPUT_DIR/poster-registry.json")
    register.add_argument("--known-images", type=Path, action="append", default=[], help="이미 보유한 이미지 폴더(예: data/img). 중복 검사에 먼저 등록")
    register.add_argument("--collector", help="decisions 항목의 수집자. worklist 항목은 owner 열 사용")
    register.add_argument("--year", type=int)
    return command


def prevent_input_overwrite(output, *inputs):
    if any(output.resolve() == source.resolve() for source in inputs):
        raise CollectionError("출력 경로는 입력 원본 파일과 다르게 지정하세요.")


def image_candidate(festival, retrieved_at, detail=None):
    item = festival if detail is None else detail
    name = text(item.get("imgname")) if detail is not None else None
    return {
        "title": text(festival.get("title")), "content_id": text(festival.get("contentid")),
        "start_date_raw": text(festival.get("eventstartdate")), "end_date_raw": text(festival.get("eventenddate")),
        "source_url": text(item.get("firstimage" if detail is None else "originimgurl")),
        "thumbnail_url": text(item.get("firstimage2" if detail is None else "smallimageurl")),
        "copyright_type": text(item.get("cpyrhtDivCd")),
        "api_field": "firstimage" if detail is None else "originimgurl",
        "endpoint": "searchFestival2" if detail is None else "detailImage2",
        "retrieved_at": retrieved_at, "image_name": name,
        "image_serial": text(item.get("serialnum")) if detail is not None else None,
        "poster_name_hint": bool(name and ("포스터" in name or "poster" in name.casefold())),
        "poster_auto": poster_hint(name, festival_years(festival)),
        "poster_review_status": "pending", "image": None, "download_error": None,
    }


def reuse_snapshot(path, filters):
    """--resume keeps the first run's festival list so cached details and later worklists stay consistent."""
    if not path.exists():
        return None
    snapshot = read_json(path)
    if snapshot.get("filters") != filters:
        raise CollectionError(f"{path.name}의 수집 조건이 이번 실행과 다릅니다. 같은 조건으로 실행하거나 새 --output-dir를 사용하세요.")
    return snapshot


def matched_first(festivals, standard_items, year):
    """Spend the daily quota first on TourAPI festivals that also have a 표준데이터 row."""
    standard = [item for item in standard_items if not year or str(item.get("fstvlStartDate") or "").startswith(str(year))]
    linked = {link["content_id"] for link in match_festivals(standard, festivals)["pairs"].values()}
    return sorted(festivals, key=lambda item: text(item.get("contentid")) not in linked), len(linked)


def collect_details(client, festivals, cache, resume):
    """Call detailImage2 per festival, caching each response at once; quota/auth errors stop the loop."""
    details, errors, stopped, remaining, cached = [], [], None, 0, 0
    for item in festivals:
        content_id = text(item.get("contentid"))
        if not content_id:
            continue
        if not valid_content_id(content_id):
            errors.append({"content_id": content_id, "error": "TourAPI 콘텐츠 ID는 숫자여야 합니다."})
            continue
        path = cache / f"{content_id}.json"
        if resume and path.exists():
            details.append((item, read_json(path)))
            cached += 1
            continue
        if stopped:
            remaining += 1
            continue
        try:
            pages = client.tourapi_images(content_id)
        except ApiLimitError as error:
            stopped, remaining = {"content_id": content_id, "error": str(error)}, remaining + 1
            continue
        except CollectionError as error:
            errors.append({"content_id": content_id, "error": str(error)})
            continue
        snapshot = {"content_id": content_id, "retrieved_at": now(), "pages": pages}
        write_json(path, snapshot)
        details.append((item, snapshot))
    return details, errors, stopped, remaining, cached


def collect_data(args, settings):
    if args.download_images and not args.tourapi:
        raise CollectionError("--download-images에는 --tourapi가 필요합니다.")
    if args.detail_images and not args.tourapi:
        raise CollectionError("--detail-images에는 --tourapi가 필요합니다.")
    if args.poster_hints_only and not (args.download_images and args.detail_images):
        raise CollectionError("--poster-hints-only에는 --detail-images와 --download-images가 필요합니다.")
    if args.resume and not args.output_dir:
        raise CollectionError("--resume에는 이어서 수집할 --output-dir가 필요합니다.")
    settings.require_key("data_go_kr")
    start, end = None, None
    if args.tourapi:
        settings.require_key("tourapi")
        if not args.year and not args.start_date:
            raise CollectionError("TourAPI 수집에는 --year 또는 --start-date가 필요합니다.")
        start = iso_date(args.start_date) if args.start_date else f"{args.year}-01-01"
        end = iso_date(args.end_date) if args.end_date else f"{args.year}-12-31" if args.year else None
        if end and date.fromisoformat(start) > date.fromisoformat(end):
            raise CollectionError("조회 종료일이 시작일보다 빠릅니다.")
    directory = args.output_dir or settings.output_dir / ("run_" + now().replace(":", "").replace("+", "_") + "_" + uuid4().hex[:6])
    client = ApiClient(settings)
    client.max_calls = args.max_calls
    filters = {"name": args.name, "year": args.year}
    snapshot = reuse_snapshot(directory / "data_go_kr.raw.json", filters) if args.resume else None
    if snapshot is None:
        pages = client.festivals(args.name)
        snapshot = {"provider": "data_go_kr", "mode": "live", "retrieved_at": now(), "filters": filters, "pages": pages}
        write_json(directory / "data_go_kr.raw.json", snapshot)
    bundle = normalize_response(snapshot, name=args.name, year=args.year, retrieved_at=snapshot["retrieved_at"], mode="live")
    write_json(directory / "data_go_kr.normalized.json", bundle)
    summary = {"output_dir": str(directory), "festival_records": len(bundle["records"]), "festival_pages": len(snapshot["pages"])}
    if args.tourapi:
        tour_filters = {"name": args.name, "start_date": start, "end_date": end}
        tour_snapshot = reuse_snapshot(directory / "tourapi.raw.json", tour_filters) if args.resume else None
        if tour_snapshot is None:
            tour_snapshot = {"provider": "tourapi", "mode": "live", "retrieved_at": now(), "filters": tour_filters, "pages": client.tourapi(start, end)}
            write_json(directory / "tourapi.raw.json", tour_snapshot)
        festivals = [item for item in parse_items(tour_snapshot)
                     if not (args.name and title_key(item.get("title")) != title_key(args.name))
                     and not (args.year and not str(item.get("eventstartdate", "")).startswith(str(args.year)))]
        images = [image_candidate(item, tour_snapshot["retrieved_at"]) for item in festivals]
        detail_errors, stopped = [], None
        if args.detail_images:
            festivals, summary["detail_matched_first"] = matched_first(festivals, parse_items(snapshot), args.year)
            details, detail_errors, stopped, remaining, cached = collect_details(client, festivals, directory / "cache/tourapi-detail", args.resume)
            for item, detail_snapshot in details:
                images.extend(image_candidate(item, detail_snapshot["retrieved_at"], detail) for detail in parse_items(detail_snapshot))
            write_json(directory / "tourapi.detail-images.raw.json", {"provider": "tourapi", "mode": "live", "records": [record for _, record in details], "errors": detail_errors, "stopped": stopped})
            summary["detail_image_queries"] = len(details) - cached + len(detail_errors)
            summary["detail_image_cached"] = cached
            summary["detail_image_failures"] = len(detail_errors)
            summary["detail_image_remaining"] = remaining
        previous = {}
        if args.resume and (directory / "tourapi.images.json").exists():
            previous = {entry["source_url"]: entry["image"] for entry in read_json(directory / "tourapi.images.json")["images"]
                        if entry.get("image") and entry.get("source_url")}
        for entry in images:
            if not (args.download_images and entry["source_url"]) or (args.poster_hints_only and not entry["poster_auto"]):
                continue
            reused = previous.get(entry["source_url"])
            if reused and reused.get("file") and repo_path(reused["file"]).exists():
                entry["image"] = reused
                continue
            try:
                entry["image"] = client.download_image(entry["source_url"], directory / "images")
                entry["image"]["file"] = repo_relative(entry["image"]["file"])
            except CollectionError as error:
                entry["download_error"] = str(error)
        write_json(directory / "tourapi.images.json", {"provider": "tourapi", "images": images})
        summary["image_candidates"] = len(images)
        summary["poster_name_hints"] = sum(entry["poster_name_hint"] for entry in images)
        summary["poster_auto_candidates"] = sum(entry["poster_auto"] for entry in images)
        failures = sum(entry["download_error"] is not None for entry in images)
        summary["image_download_failures"] = failures
        summary["api_calls"] = getattr(client, "calls", None)
        if stopped:
            print(json.dumps(summary, ensure_ascii=False))
            raise CollectionError(f"API 호출을 중단했습니다. {stopped['error']} 상세 이미지 미조회 {summary['detail_image_remaining']}건은 한도가 초기화된 뒤 같은 --output-dir에 --resume을 붙여 이어서 수집하세요.")
        if failures or detail_errors:
            print(json.dumps(summary, ensure_ascii=False))
            raise CollectionError(f"상세 이미지 조회 {len(detail_errors)}건, 이미지 다운로드 {failures}건 실패. 출력 목록을 확인하고 --resume으로 재시도하세요.")
    summary["api_calls"] = getattr(client, "calls", None)
    return summary


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "collect":
            summary = collect_data(args, Settings.load(ROOT, args.env_file))
        elif args.command == "normalize":
            prevent_input_overwrite(args.output, args.input)
            payload = read_json(args.input)
            retrieved_at = payload.get("retrieved_at") if isinstance(payload, dict) else None
            mode = payload.get("mode", "offline") if isinstance(payload, dict) else "offline"
            bundle = normalize_response(payload, name=args.name, year=args.year, retrieved_at=retrieved_at, mode=mode)
            write_json(args.output, bundle)
            summary = {"output": str(args.output), "records": len(bundle["records"]), "mode": mode}
        elif args.command == "compare":
            prevent_input_overwrite(args.output, args.poster, args.reference)
            result, report = reconcile(read_json(args.poster), read_json(args.reference))
            report_path = args.output.with_name(args.output.stem + ".report.json")
            prevent_input_overwrite(report_path, args.poster, args.reference)
            write_json(args.output, result)
            write_json(report_path, report)
            summary = {"output": str(args.output), "report": str(report_path), "reference_status": result["reference_status"], "counts": report["counts"]}
        elif args.command == "attach-ocr":
            prevent_input_overwrite(args.output, args.record, args.ocr)
            result = attach_ocr_result(read_json(args.record), read_json(args.ocr))
            write_json(args.output, result)
            summary = {"output": str(args.output), "reference_status": result["reference_status"]}
        elif args.command == "attach-poster":
            prevent_input_overwrite(args.output, args.record, args.poster)
            result = attach_poster(read_json(args.record), read_json(args.poster))
            write_json(args.output, result)
            summary = {"output": str(args.output), "reference_status": result["reference_status"]}
        elif args.command == "worklist":
            owners = [name.strip() for name in (args.owners or "").split(",") if name.strip()]
            summary = make_worklist(args.run_dir, args.output or args.run_dir / "worklist.csv", args.year, owners, args.priority)
        elif args.command == "review":
            summary = make_review(args.run_dir, args.output or args.run_dir / "review.html", args.year)
        elif args.command == "ingest":
            settings = Settings.load(ROOT, args.env_file)
            if not args.decisions and not (args.worklist or args.run_dir / "worklist.csv").exists():
                raise CollectionError("--decisions 또는 worklist.csv가 필요합니다.")
            summary = ingest(
                args.run_dir, ApiClient(settings), output_dir=args.output_dir or args.run_dir / "ingest",
                registry_path=args.registry or settings.output_dir / "poster-registry.json", decisions=args.decisions,
                worklist=args.worklist or args.run_dir / "worklist.csv", intake=args.intake or args.run_dir / "intake",
                known_images=args.known_images, collector=args.collector, year=args.year)
            if summary["failures"]:
                print(json.dumps(summary, ensure_ascii=False))
                raise CollectionError(f"이미지 {len(summary['failures'])}건을 받지 못했습니다. 주소를 확인하고 다시 실행하면 등록된 건은 건너뜁니다.")
        elif args.command == "validate" and args.poster:
            document = read_json(args.file)
            validate_poster(document)
            summary = {"file": str(args.file), "schema_valid": True, "findings": poster_findings(document)}
        else:
            (validate_handoff if args.ocr_result else validate)(read_json(args.file))
            summary = {"file": str(args.file), "schema_valid": True}
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except CollectionError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Avoid traceback/URL/value output when processing malformed external data.
        print(f"오류: 입력 파일·응답 형식·설정을 확인하세요 ({type(error).__name__}).", file=sys.stderr)
        return 1
