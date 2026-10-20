"""Bounded, paginated public API requests; URLs and keys never enter logs."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from PIL import Image, UnidentifiedImageError

from .normalize import LIMIT_CODES, parse_items, text
from .settings import ApiLimitError, CollectionError, Settings

DATA_GO_URL = "https://api.data.go.kr/openapi/tn_pubr_public_cltur_fstvl_api"
TOURAPI_URL = "https://apis.data.go.kr/B551011/KorService2/searchFestival2"
TOURAPI_IMAGES_URL = "https://apis.data.go.kr/B551011/KorService2/detailImage2"
MAX_BYTES = 25 * 1024 * 1024
EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}


def redact(value, secrets):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in {"servicekey", "authorization", "api_key"} else redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def valid_content_id(content_id):
    content_id = str(content_id)
    return content_id.isascii() and content_id.isdigit() and len(content_id) <= 20


def inspect_image(raw):
    """Return (width, height, format, extension) of image bytes without decoding them twice."""
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height, image_format = image.width, image.height, image.format.lower()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, AttributeError, Image.DecompressionBombError):
        raise CollectionError("내려받은 파일이 유효한 이미지가 아닙니다.") from None
    extension = EXTENSIONS.get(image_format)
    if extension is None:
        raise CollectionError("지원하지 않는 이미지 형식입니다.")
    return width, height, image_format, extension


def store_image(raw, directory: Path):
    """Keep the original bytes under their SHA-256; pixels are never re-encoded."""
    width, height, image_format, extension = inspect_image(raw)
    digest = sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.{extension}"
    if not target.exists():
        target.write_bytes(raw)
    return {"file": str(target), "sha256": digest, "width": width, "height": height, "format": image_format}


class ApiClient:
    def __init__(self, settings: Settings, opener=None, sleeper=None, max_calls=None):
        self.settings = settings
        self.opener = opener or urlopen
        self.sleeper = sleeper or time.sleep
        self.max_calls = max_calls
        self.calls = 0  # API attempts including retries; image downloads do not use the key and are not counted.

    def _bytes(self, url, api=True):
        for attempt in range(self.settings.retries + 1):
            if api:
                if self.max_calls is not None and self.calls >= self.max_calls:
                    raise ApiLimitError(f"이번 실행의 API 호출 한도 {self.max_calls}건(재시도 포함)에 도달했습니다.")
                self.calls += 1
            try:
                request = Request(url, headers={"User-Agent": "FestivalDataCollector/2.0", "Accept": "application/json,image/*"})
                with self.opener(request, timeout=self.settings.timeout) as response:
                    data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise CollectionError("응답 크기가 25MB 제한을 초과했습니다.")
                return data
            except HTTPError as error:
                if error.code in {429, 500, 502, 503, 504} and attempt < self.settings.retries:
                    self.sleeper(min(2 ** attempt, 4))
                    continue
                if api and error.code in {401, 403, 429}:
                    raise ApiLimitError(f"HTTP {error.code}: 인증 실패 또는 호출 한도 초과. 키·활용신청·일일 한도를 확인하세요.") from None
                raise CollectionError(f"HTTP {error.code}: 요청 실패. 인증·호출 제한·서버 상태를 확인하세요.") from None
            except (URLError, TimeoutError, socket.timeout):
                if attempt < self.settings.retries:
                    self.sleeper(min(2 ** attempt, 4))
                    continue
                raise CollectionError("네트워크 연결 또는 응답 시간 초과. 인터넷과 API 접속을 확인하세요.") from None

    def get(self, base_url, parameters):
        raw = self._bytes(base_url + "?" + urlencode(parameters))
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Auth failures are sometimes XML despite type=json.
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                raise CollectionError("API가 JSON 응답을 반환하지 않았습니다.") from None
            code = next((element.text for element in root.iter() if element.tag.rsplit("}", 1)[-1] in {"returnReasonCode", "resultCode"}), "unknown")
            code = code.strip() if code and code.strip().isdigit() else "unknown"
            error = ApiLimitError if code in LIMIT_CODES else CollectionError
            raise error(f"API가 XML 오류를 반환했습니다(코드 {code}). 서비스 키·활용신청·일일 한도를 확인하세요.") from None
        if not isinstance(payload, dict):
            raise CollectionError("API 응답 최상위 형식이 객체가 아닙니다.")
        parse_items(payload)  # Verify the API result code before persistence.
        secrets = [variant for key in [self.settings.data_go_key, self.settings.tourapi_key]
                   for variant in [key, quote(key, safe=""), quote_plus(key)]]
        return redact(payload, secrets)

    def pages(self, url, parameters):
        snapshots = []
        fetched = 0
        previous = set()
        for number in range(1, self.settings.max_pages + 1):
            payload = self.get(url, {**parameters, "pageNo": number, "numOfRows": self.settings.page_size})
            items = parse_items(payload)
            response = payload.get("response", payload)
            body = response.get("body", {})
            count = body.get("totalCount")
            try:
                total = int(count) if count not in (None, "") else None
            except (TypeError, ValueError):
                raise CollectionError("API totalCount 값이 정수가 아닙니다.") from None
            if not items:
                if total is not None and fetched < total:
                    raise CollectionError("전체 건수에 도달하기 전에 빈 페이지가 반환됐습니다. 수집을 다시 실행하세요.")
                snapshots.append(payload)
                return snapshots
            signature = sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if signature in previous:
                raise CollectionError("같은 API 페이지가 반복되어 수집을 중단했습니다.")
            previous.add(signature)
            snapshots.append(payload)
            fetched += len(items)
            if total is not None and fetched >= total:
                return snapshots
            if total is None and len(items) < self.settings.page_size:
                return snapshots
        raise CollectionError("FESTIVAL_MAX_PAGES에 도달했습니다. 제한을 늘리거나 조회 조건을 좁히세요.")

    def festivals(self, name=None):
        key = self.settings.require_key("data_go_kr")
        parameters = {"serviceKey": key, "type": "json"}
        if name:
            parameters["fstvlNm"] = name
        return self.pages(DATA_GO_URL, parameters)

    def tourapi(self, start_date, end_date=None):
        key = self.settings.require_key("tourapi")
        parameters = {"serviceKey": key, "_type": "json", "MobileOS": "ETC", "MobileApp": self.settings.mobile_app, "eventStartDate": start_date.replace("-", "")}
        if end_date:
            parameters["eventEndDate"] = end_date.replace("-", "")
        return self.pages(TOURAPI_URL, parameters)

    def fetch_image(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise CollectionError("이미지 URL은 사용자 인증정보 없는 HTTP(S) 주소여야 합니다.")
        # Image requests do not reuse the API service key.
        return self._bytes(url, api=False)

    def download_image(self, url, directory: Path):
        return {**store_image(self.fetch_image(url), directory), "source_url": url}

    def tourapi_images(self, content_id):
        content_id = str(content_id)
        if not valid_content_id(content_id):
            raise CollectionError("TourAPI 콘텐츠 ID는 숫자여야 합니다.")
        parameters = {
            "serviceKey": self.settings.require_key("tourapi"), "_type": "json",
            "MobileOS": "ETC", "MobileApp": self.settings.mobile_app,
            "contentId": content_id, "imageYN": "Y",
        }
        # KorService2 rejects the former subImageYN parameter.
        return self.pages(TOURAPI_IMAGES_URL, parameters)
