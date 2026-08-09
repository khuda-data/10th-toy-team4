from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


COLLECTOR_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = COLLECTOR_ROOT.parent


class ConfigError(ValueError):
    """실행에 필요한 설정이 없거나 잘못된 경우."""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    meaningful_lines = [
        line.strip() for line in raw_lines if line.strip() and not line.strip().startswith("#")
    ]
    # 초기 실험에서 인증키만 한 줄로 저장한 파일도 안전하게 받아들입니다.
    if len(meaningful_lines) == 1 and "=" not in meaningful_lines[0]:
        os.environ.setdefault("GBIS_SERVICE_KEY", meaningful_lines[0])
        return

    for line_number, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"{path}:{line_number}: KEY=VALUE 형식이 아닙니다.")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _as_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name}은 true 또는 false여야 합니다.")


def _positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name}은 정수여야 합니다.") from exc
    if parsed <= 0:
        raise ConfigError(f"{name}은 0보다 커야 합니다.")
    return parsed


def _bounded_int(value: str, *, name: str, minimum: int, maximum: int) -> int:
    parsed = _positive_int(value, name=name)
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{name}은 {minimum}~{maximum} 범위여야 합니다.")
    return parsed


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _read_service_keys() -> tuple[str, ...]:
    multiple = os.environ.get("GBIS_SERVICE_KEYS", "")
    if multiple.strip():
        # 공공데이터포털 인증키에는 쉼표가 사용되지 않으므로 .env 한 줄에서 관리합니다.
        values = [value.strip() for value in multiple.split(",")]
        return _unique(values)

    single = os.environ.get("GBIS_SERVICE_KEY", "").strip()
    return (single,) if single else ()


def _read_route_ids(routes_file: Path) -> tuple[str, ...]:
    route_ids_env = os.environ.get("GBIS_ROUTE_IDS", "").strip()
    if route_ids_env:
        values = [value.strip() for value in route_ids_env.split(",")]
    elif routes_file.exists():
        values = []
        for raw_line in routes_file.read_text(encoding="utf-8").splitlines():
            value = raw_line.split("#", 1)[0].strip()
            if value:
                values.append(value)
    else:
        values = []

    route_ids = _unique(values)
    invalid = [route_id for route_id in route_ids if not route_id.isdigit()]
    if invalid:
        raise ConfigError(f"노선 ID는 숫자여야 합니다: {', '.join(invalid)}")
    return route_ids


@dataclass(frozen=True)
class Settings:
    service_keys: tuple[str, ...]
    route_ids: tuple[str, ...]
    db_path: Path
    timeout_seconds: int
    daily_request_limit: int
    store_raw_responses: bool
    routes_file: Path

    @property
    def service_key(self) -> str:
        """단일 키를 기대하는 기존 호출부를 위한 첫 번째 인증키."""
        return self.service_keys[0]

    @property
    def total_daily_request_limit(self) -> int:
        return self.daily_request_limit * len(self.service_keys)

    @classmethod
    def load(cls, *, require_routes: bool = True) -> "Settings":
        env_file = _resolve_path(os.environ.get("GBIS_ENV_FILE", ".env"))
        _load_dotenv(env_file)

        routes_file = _resolve_path(
            os.environ.get("GBIS_ROUTES_FILE", "collector/config/routes.txt")
        )
        route_ids = _read_route_ids(routes_file)
        service_keys = _read_service_keys()

        if not service_keys:
            raise ConfigError(
                "GBIS_SERVICE_KEYS 또는 GBIS_SERVICE_KEY가 없습니다. "
                "collector/.env.example을 루트의 .env로 복사한 뒤 인증키를 입력하세요."
            )
        if require_routes and not route_ids:
            raise ConfigError(
                "수집할 노선 ID가 없습니다. collector/config/routes.txt 또는 "
                "GBIS_ROUTE_IDS를 설정하세요."
            )

        return cls(
            service_keys=service_keys,
            route_ids=route_ids,
            db_path=_resolve_path(os.environ.get("GBIS_DB_PATH", "data/gbis.sqlite3")),
            timeout_seconds=_positive_int(
                os.environ.get("GBIS_REQUEST_TIMEOUT_SECONDS", "20"),
                name="GBIS_REQUEST_TIMEOUT_SECONDS",
            ),
            daily_request_limit=_positive_int(
                os.environ.get("GBIS_DAILY_REQUEST_LIMIT", "1000"),
                name="GBIS_DAILY_REQUEST_LIMIT",
            ),
            store_raw_responses=_as_bool(
                os.environ.get("GBIS_STORE_RAW_RESPONSES", "true"),
                name="GBIS_STORE_RAW_RESPONSES",
            ),
            routes_file=routes_file,
        )


@dataclass(frozen=True)
class ApiSettings:
    db_path: Path
    api_key_hashes: tuple[str, ...]
    host: str
    port: int
    max_page_size: int
    max_history_days: int

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @classmethod
    def from_api_keys(
        cls,
        *,
        db_path: Path,
        api_keys: tuple[str, ...],
        host: str = "127.0.0.1",
        port: int = 8000,
        max_page_size: int = 500,
        max_history_days: int = 30,
    ) -> "ApiSettings":
        return cls(
            db_path=db_path,
            api_key_hashes=tuple(cls.hash_api_key(key) for key in api_keys),
            host=host,
            port=port,
            max_page_size=max_page_size,
            max_history_days=max_history_days,
        )

    @classmethod
    def load(cls) -> "ApiSettings":
        env_file = _resolve_path(os.environ.get("GBIS_ENV_FILE", ".env"))
        _load_dotenv(env_file)

        api_keys = _unique(
            [value.strip() for value in os.environ.get("GBIS_API_KEYS", "").split(",")]
        )
        if not api_keys:
            raise ConfigError(
                "GBIS_API_KEYS가 없습니다. 팀원별 API 키를 쉼표로 구분해 설정하세요."
            )
        if any(len(key) < 16 for key in api_keys):
            raise ConfigError("GBIS_API_KEYS의 각 키는 16자 이상이어야 합니다.")

        host = os.environ.get("GBIS_API_HOST", "127.0.0.1").strip()
        if not host:
            raise ConfigError("GBIS_API_HOST가 비어 있습니다.")

        return cls.from_api_keys(
            db_path=_resolve_path(os.environ.get("GBIS_DB_PATH", "data/gbis.sqlite3")),
            api_keys=api_keys,
            host=host,
            port=_bounded_int(
                os.environ.get("GBIS_API_PORT", "8000"),
                name="GBIS_API_PORT",
                minimum=1,
                maximum=65535,
            ),
            max_page_size=_bounded_int(
                os.environ.get("GBIS_API_MAX_PAGE_SIZE", "500"),
                name="GBIS_API_MAX_PAGE_SIZE",
                minimum=1,
                maximum=5000,
            ),
            max_history_days=_bounded_int(
                os.environ.get("GBIS_API_MAX_HISTORY_DAYS", "30"),
                name="GBIS_API_MAX_HISTORY_DAYS",
                minimum=1,
                maximum=366,
            ),
        )
