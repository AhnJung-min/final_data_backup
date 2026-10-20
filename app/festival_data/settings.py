"""Load a local .env without logging credentials or changing process globals."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from urllib.parse import unquote


class CollectionError(Exception):
    """A user-readable collection error that must not contain a request URL."""


class ApiLimitError(CollectionError):
    """Authentication, daily quota, or this run's call budget: every later call would fail too, so batches stop."""


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line)
        if "=" not in line:
            raise CollectionError(f".env {number}행: NAME=value 형식이 필요합니다.")
        name, value = (part.strip() for part in line.split("=", 1))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise CollectionError(f".env {number}행: 환경변수 이름을 확인하세요.")
        if value[:1] in {"'", '"'}:
            quote = value[0]
            closing = value.find(quote, 1)
            suffix = value[closing + 1:].strip() if closing >= 0 else ""
            if closing < 0 or (suffix and not suffix.startswith("#")):
                raise CollectionError(f".env {number}행: 따옴표를 확인하세요.")
            value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def integer(values, name, default, minimum, maximum):
    try:
        value = int(values.get(name, str(default)))
    except ValueError:
        raise CollectionError(f"{name}에는 정수가 필요합니다.") from None
    if not minimum <= value <= maximum:
        raise CollectionError(f"{name}은 {minimum}~{maximum} 범위로 설정하세요.")
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    data_go_key: str = field(repr=False)
    tourapi_key: str = field(repr=False)
    mobile_app: str = "FestivalDataCollector"
    timeout: int = 30
    retries: int = 2
    page_size: int = 1000
    max_pages: int = 100
    output_dir: Path = Path("data/runtime/festivals")

    @classmethod
    def load(cls, root: Path, env_path: Path | None = None, environ=None):
        values = read_env(env_path or root / ".env")
        values.update(os.environ if environ is None else environ)
        destination = Path(values.get("FESTIVAL_OUTPUT_DIR", "data/runtime/festivals"))
        shared_key = values.get("DATA_GO_KR_SERVICE_KEY", "").strip()
        tourapi_key = values.get("TOURAPI_SERVICE_KEY", "").strip() or shared_key
        return cls(
            root=root,
            data_go_key=unquote(shared_key),
            tourapi_key=unquote(tourapi_key),
            mobile_app=values.get("TOURAPI_MOBILE_APP", "FestivalDataCollector"),
            timeout=integer(values, "FESTIVAL_HTTP_TIMEOUT", 30, 1, 120),
            retries=integer(values, "FESTIVAL_HTTP_RETRIES", 2, 0, 5),
            page_size=integer(values, "FESTIVAL_PAGE_SIZE", 1000, 1, 1000),
            max_pages=integer(values, "FESTIVAL_MAX_PAGES", 100, 1, 1000),
            output_dir=destination if destination.is_absolute() else root / destination,
        )

    def require_key(self, provider):
        key = self.data_go_key if provider == "data_go_kr" else self.tourapi_key
        name = "DATA_GO_KR_SERVICE_KEY" if provider == "data_go_kr" else "DATA_GO_KR_SERVICE_KEY 또는 TOURAPI_SERVICE_KEY"
        if not key or key.lower().startswith(("your_", "replace_")):
            raise CollectionError(f".env의 {name}에 발급받은 서비스 키를 입력하세요.")
        return key
