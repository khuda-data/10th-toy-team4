from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import httpx
import pandas as pd


DEFAULT_CACHE_PATH = Path("data/gbis_api_cache.sqlite3")


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cache_metadata (
    resource TEXT PRIMARY KEY,
    refreshed_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    route_id TEXT PRIMARY KEY,
    station_count INTEGER NOT NULL,
    observation_count INTEGER NOT NULL,
    first_collected_at TEXT,
    last_collected_at TEXT,
    cached_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_stations (
    route_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    station_seq INTEGER NOT NULL,
    station_name TEXT,
    mobile_no TEXT,
    region_name TEXT,
    x REAL,
    y REAL,
    center_yn TEXT,
    synced_at_kst TEXT,
    cached_at_utc TEXT NOT NULL,
    PRIMARY KEY (route_id, station_seq)
);

CREATE INDEX IF NOT EXISTS idx_api_cache_stations_id
    ON route_stations(station_id);

CREATE TABLE IF NOT EXISTS latest_locations (
    route_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    query_time TEXT,
    plate_no TEXT,
    route_type_code INTEGER,
    station_id TEXT,
    station_seq INTEGER,
    station_name TEXT,
    remaining_seats INTEGER,
    crowded INTEGER,
    low_plate INTEGER,
    state_code INTEGER,
    tagless_code INTEGER,
    cached_at_utc TEXT NOT NULL,
    PRIMARY KEY (route_id, vehicle_id)
);

CREATE INDEX IF NOT EXISTS idx_api_cache_latest_station
    ON latest_locations(route_id, station_seq);

CREATE TABLE IF NOT EXISTS location_history (
    route_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    query_time TEXT,
    plate_no TEXT,
    route_type_code INTEGER,
    station_id TEXT,
    station_seq INTEGER,
    station_name TEXT,
    remaining_seats INTEGER,
    crowded INTEGER,
    low_plate INTEGER,
    state_code INTEGER,
    tagless_code INTEGER,
    cached_at_utc TEXT NOT NULL,
    PRIMARY KEY (route_id, observed_at, vehicle_id)
);

CREATE INDEX IF NOT EXISTS idx_api_cache_history_time
    ON location_history(observed_at);
CREATE INDEX IF NOT EXISTS idx_api_cache_history_route_time
    ON location_history(route_id, observed_at);

CREATE TABLE IF NOT EXISTS history_sync_state (
    route_id TEXT PRIMARY KEY,
    source_max_observed_at TEXT,
    refreshed_at_utc TEXT NOT NULL
);

PRAGMA user_version = 2;
"""


LOCATION_COLUMNS = (
    "observed_at",
    "query_time",
    "route_id",
    "vehicle_id",
    "plate_no",
    "route_type_code",
    "station_id",
    "station_seq",
    "station_name",
    "remaining_seats",
    "crowded",
    "low_plate",
    "state_code",
    "tagless_code",
)


class GBISClientError(RuntimeError):
    """API 호출이나 응답 처리에 실패했을 때 발생합니다."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _route_id(value: str) -> str:
    normalized = str(value).strip()
    if not normalized.isdigit():
        raise ValueError("route_id는 숫자 문자열이어야 합니다.")
    return normalized


def _datetime_param(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    normalized = value.strip()
    if not normalized:
        raise ValueError("날짜/시간 값은 비어 있을 수 없습니다.")
    return normalized


def _parse_datetime(value: str, *, default_timezone: Any = None) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"날짜/시간 형식이 올바르지 않습니다: {value}") from exc
    if parsed.tzinfo is None and default_timezone is not None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: KEY=VALUE 형식이 아닙니다.")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"{path}:{line_number}: 환경변수 이름이 비어 있습니다.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _setting(name: str, file_values: Mapping[str, str]) -> str:
    if name in os.environ:
        return os.environ[name]
    return file_values.get(name, "")


class GBISApiCache:
    """GBIS API 응답을 로컬 SQLite에 저장하고 DataFrame으로 읽습니다."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        timeout_seconds: float = 20.0,
        max_rate_limit_retries: int = 6,
        rate_limit_wait_seconds: float = 1.1,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("base_url은 비어 있을 수 없습니다.")
        if not api_key.strip():
            raise ValueError("api_key는 비어 있을 수 없습니다.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds는 0보다 커야 합니다.")
        if max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries는 0 이상이어야 합니다.")
        if rate_limit_wait_seconds <= 0:
            raise ValueError("rate_limit_wait_seconds는 0보다 커야 합니다.")

        self.base_url = normalized_url
        self.cache_path = Path(cache_path).expanduser()
        self.max_rate_limit_retries = max_rate_limit_retries
        self.rate_limit_wait_seconds = rate_limit_wait_seconds
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._initialize()

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path | str = Path(".env"),
        cache_path: Path | str | None = None,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> "GBISApiCache":
        env_path = Path(env_file).expanduser()
        file_values = _read_env_file(env_path)
        base_url = _setting("GBIS_API_BASE_URL", file_values).strip()
        api_key = _setting("GBIS_API_KEY", file_values).strip()
        if not base_url:
            raise ValueError(
                f"GBIS_API_BASE_URL이 환경변수 또는 {env_path}에 없습니다."
            )
        if not api_key:
            raise ValueError(f"GBIS_API_KEY가 환경변수 또는 {env_path}에 없습니다.")
        configured_cache_path = _setting("GBIS_API_CACHE_PATH", file_values).strip()
        resolved_cache_path = cache_path or configured_cache_path or DEFAULT_CACHE_PATH
        return cls(
            base_url=base_url,
            api_key=api_key,
            cache_path=resolved_cache_path,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self.checkpoint()
        self._http.close()

    def __enter__(self) -> "GBISApiCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.cache_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            route_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(routes)").fetchall()
            }
            if "first_collected_at" not in route_columns:
                connection.execute(
                    "ALTER TABLE routes ADD COLUMN first_collected_at TEXT"
                )

    def checkpoint(self) -> None:
        """Drive 등으로 복사하기 전에 WAL 내용을 기본 DB 파일에 반영합니다."""
        if not self.cache_path.is_file():
            return
        with sqlite3.connect(self.cache_path, timeout=30) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response: httpx.Response | None = None
        try:
            for attempt in range(self.max_rate_limit_retries + 1):
                response = self._http.get(path, params=params)
                if response.status_code != 429 or attempt == self.max_rate_limit_retries:
                    break
                retry_after = response.headers.get("Retry-After", "").strip()
                try:
                    wait_seconds = float(retry_after)
                except ValueError:
                    wait_seconds = min(
                        self.rate_limit_wait_seconds * (2**attempt),
                        30.0,
                    )
                self._sleep(max(wait_seconds, self.rate_limit_wait_seconds))
            assert response is not None
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail: Any = None
            try:
                body = exc.response.json()
                if isinstance(body, dict):
                    detail = body.get("detail")
            except ValueError:
                pass
            message = f"GBIS API가 HTTP {exc.response.status_code}를 반환했습니다."
            if detail:
                message = f"{message} {detail}"
            raise GBISClientError(message) from exc
        except httpx.RequestError as exc:
            raise GBISClientError(f"GBIS API에 연결할 수 없습니다: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GBISClientError("GBIS API 응답이 JSON 형식이 아닙니다.") from exc
        if not isinstance(payload, dict):
            raise GBISClientError("GBIS API 응답의 최상위 값이 객체가 아닙니다.")
        return payload

    @staticmethod
    def _items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        items = payload.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise GBISClientError("GBIS API 응답의 items 형식이 올바르지 않습니다.")
        return items

    @staticmethod
    def _mark_refreshed(
        connection: sqlite3.Connection,
        resource: str,
        refreshed_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO cache_metadata (resource, refreshed_at_utc)
            VALUES (?, ?)
            ON CONFLICT(resource) DO UPDATE SET
                refreshed_at_utc = excluded.refreshed_at_utc
            """,
            (resource, refreshed_at),
        )

    def refresh_routes(self) -> int:
        items = self._items(self._get("/v1/routes"))
        cached_at = _utc_now()
        rows = [
            (
                str(item["route_id"]),
                int(item.get("station_count") or 0),
                int(item.get("observation_count") or 0),
                item.get("first_collected_at"),
                item.get("last_collected_at"),
                cached_at,
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.execute("DELETE FROM routes")
            connection.executemany(
                """
                INSERT INTO routes (
                    route_id, station_count, observation_count,
                    first_collected_at, last_collected_at, cached_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._mark_refreshed(connection, "routes", cached_at)
        return len(rows)

    def refresh_stations(self, route_id: str) -> int:
        normalized_route_id = _route_id(route_id)
        payload = self._get(f"/v1/routes/{normalized_route_id}/stations")
        items = self._items(payload)
        cached_at = _utc_now()
        rows = [
            (
                normalized_route_id,
                str(item["station_id"]),
                int(item["station_seq"]),
                item.get("station_name"),
                item.get("mobile_no"),
                item.get("region_name"),
                item.get("x"),
                item.get("y"),
                item.get("center_yn"),
                item.get("synced_at_kst"),
                cached_at,
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM route_stations WHERE route_id = ?",
                (normalized_route_id,),
            )
            connection.executemany(
                """
                INSERT INTO route_stations (
                    route_id, station_id, station_seq, station_name, mobile_no,
                    region_name, x, y, center_yn, synced_at_kst, cached_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._mark_refreshed(
                connection, f"stations:{normalized_route_id}", cached_at
            )
        return len(rows)

    @staticmethod
    def _location_rows(
        items: list[dict[str, Any]],
        cached_at: str,
    ) -> list[tuple[Any, ...]]:
        return [
            tuple(item.get(column) for column in LOCATION_COLUMNS) + (cached_at,)
            for item in items
        ]

    def refresh_latest(self, route_id: str | None = None) -> int:
        normalized_route_id = _route_id(route_id) if route_id is not None else None
        params = {"route_id": normalized_route_id} if normalized_route_id else None
        items = self._items(self._get("/v1/locations/latest", params=params))
        cached_at = _utc_now()
        rows = self._location_rows(items, cached_at)

        with self._connect() as connection:
            if normalized_route_id:
                connection.execute(
                    "DELETE FROM latest_locations WHERE route_id = ?",
                    (normalized_route_id,),
                )
            else:
                connection.execute("DELETE FROM latest_locations")
            connection.executemany(
                """
                INSERT INTO latest_locations (
                    observed_at, query_time, route_id, vehicle_id, plate_no,
                    route_type_code, station_id, station_seq, station_name,
                    remaining_seats, crowded, low_plate, state_code, tagless_code,
                    cached_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            resource = f"latest:{normalized_route_id or 'all'}"
            self._mark_refreshed(connection, resource, cached_at)
        return len(rows)

    def _last_history_timestamp(self, route_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_max_observed_at
                FROM history_sync_state
                WHERE route_id = ?
                """,
                (route_id,),
            ).fetchone()
        return None if row is None else row["source_max_observed_at"]

    def refresh_history(
        self,
        route_id: str,
        *,
        from_at: datetime | str | None = None,
        to_at: datetime | str | None = None,
        page_size: int = 500,
    ) -> int:
        normalized_route_id = _route_id(route_id)
        if not 1 <= page_size <= 500:
            raise ValueError("page_size는 1~500 범위여야 합니다.")

        previous_checkpoint = self._last_history_timestamp(normalized_route_id)
        start = _datetime_param(from_at)
        if start is None:
            start = previous_checkpoint
        end = _datetime_param(to_at)
        params: dict[str, Any] = {
            "route_id": normalized_route_id,
            "limit": page_size,
        }
        if start is not None:
            params["from"] = start
        if end is not None:
            params["to"] = end

        total = 0
        max_observed_at = previous_checkpoint
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            if cursor is not None:
                params["cursor"] = cursor
            payload = self._get("/v1/locations", params=params)
            items = self._items(payload)
            cached_at = _utc_now()
            rows = self._location_rows(items, cached_at)
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO location_history (
                        observed_at, query_time, route_id, vehicle_id, plate_no,
                        route_type_code, station_id, station_seq, station_name,
                        remaining_seats, crowded, low_plate, state_code, tagless_code,
                        cached_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(route_id, observed_at, vehicle_id) DO UPDATE SET
                        query_time = excluded.query_time,
                        plate_no = excluded.plate_no,
                        route_type_code = excluded.route_type_code,
                        station_id = excluded.station_id,
                        station_seq = excluded.station_seq,
                        station_name = excluded.station_name,
                        remaining_seats = excluded.remaining_seats,
                        crowded = excluded.crowded,
                        low_plate = excluded.low_plate,
                        state_code = excluded.state_code,
                        tagless_code = excluded.tagless_code,
                        cached_at_utc = excluded.cached_at_utc
                    """,
                    rows,
                )
            total += len(rows)
            observed_values = [
                str(item["observed_at"])
                for item in items
                if item.get("observed_at") is not None
            ]
            if observed_values:
                page_max = max(observed_values)
                max_observed_at = max(
                    value for value in (max_observed_at, page_max) if value is not None
                )

            next_cursor = payload.get("next_cursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise GBISClientError("GBIS API의 next_cursor 형식이 올바르지 않습니다.")
            if next_cursor in seen_cursors:
                raise GBISClientError("GBIS API가 동일한 cursor를 반복해서 반환했습니다.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        with self._connect() as connection:
            refreshed_at = _utc_now()
            connection.execute(
                """
                INSERT INTO history_sync_state (
                    route_id, source_max_observed_at, refreshed_at_utc
                ) VALUES (?, ?, ?)
                ON CONFLICT(route_id) DO UPDATE SET
                    source_max_observed_at = excluded.source_max_observed_at,
                    refreshed_at_utc = excluded.refreshed_at_utc
                """,
                (normalized_route_id, max_observed_at, refreshed_at),
            )
            self._mark_refreshed(
                connection,
                f"history:{normalized_route_id}",
                refreshed_at,
            )
        return total

    def _history_start(self, route_id: str) -> str:
        checkpoint = self._last_history_timestamp(route_id)
        if checkpoint is not None:
            return checkpoint

        with self._connect() as connection:
            row = connection.execute(
                "SELECT first_collected_at FROM routes WHERE route_id = ?",
                (route_id,),
            ).fetchone()
        if row is None or row["first_collected_at"] is None:
            self.refresh_routes()
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT first_collected_at FROM routes WHERE route_id = ?",
                    (route_id,),
                ).fetchone()
        if row is None:
            raise GBISClientError(f"노선 {route_id}을 서버에서 찾을 수 없습니다.")
        if row["first_collected_at"] is None:
            raise GBISClientError(
                "서버가 first_collected_at을 제공하지 않습니다. "
                "Oracle 서버의 API 코드를 먼저 업데이트하세요."
            )
        return str(row["first_collected_at"])

    def refresh_full_history(
        self,
        route_id: str,
        *,
        to_at: datetime | str | None = None,
        window_days: int = 30,
        page_size: int = 500,
    ) -> int:
        """최초에는 전체 이력을, 이후에는 마지막 성공 시각부터 동기화합니다."""
        normalized_route_id = _route_id(route_id)
        if not 1 <= window_days <= 30:
            raise ValueError("window_days는 1~30 범위여야 합니다.")

        start = _parse_datetime(self._history_start(normalized_route_id))
        end_value = _datetime_param(to_at)
        if end_value is None:
            end = datetime.now(start.tzinfo or timezone.utc)
        else:
            end = _parse_datetime(end_value, default_timezone=start.tzinfo)
        if start.tzinfo is None and end.tzinfo is not None:
            start = start.replace(tzinfo=end.tzinfo)
        if start > end:
            return 0

        total = 0
        window_start = start
        window_size = timedelta(days=window_days)
        while window_start <= end:
            window_end = min(window_start + window_size, end)
            total += self.refresh_history(
                normalized_route_id,
                from_at=window_start,
                to_at=window_end,
                page_size=page_size,
            )
            if window_end >= end:
                break
            window_start = window_end
        return total

    def refresh_all(
        self,
        route_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {"routes": self.refresh_routes()}
        if route_ids is None:
            routes = self.routes_df()
            normalized_route_ids = [
                str(row.route_id)
                for row in routes.itertuples()
                if int(row.station_count) > 0
            ]
        else:
            normalized_route_ids = [_route_id(value) for value in route_ids]

        counts["stations"] = sum(
            self.refresh_stations(route_id) for route_id in normalized_route_ids
        )
        counts["latest_locations"] = self.refresh_latest()
        return counts

    def _read_dataframe(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> pd.DataFrame:
        with self._connect() as connection:
            return pd.read_sql_query(query, connection, params=params)

    def routes_df(self) -> pd.DataFrame:
        return self._read_dataframe("SELECT * FROM routes ORDER BY route_id")

    def stations_df(self, route_id: str | None = None) -> pd.DataFrame:
        if route_id is None:
            return self._read_dataframe(
                "SELECT * FROM route_stations ORDER BY route_id, station_seq"
            )
        normalized_route_id = _route_id(route_id)
        return self._read_dataframe(
            """
            SELECT * FROM route_stations
            WHERE route_id = ?
            ORDER BY station_seq
            """,
            (normalized_route_id,),
        )

    def latest_locations_df(self, route_id: str | None = None) -> pd.DataFrame:
        if route_id is None:
            return self._read_dataframe(
                """
                SELECT * FROM latest_locations
                ORDER BY route_id, station_seq, vehicle_id
                """
            )
        normalized_route_id = _route_id(route_id)
        return self._read_dataframe(
            """
            SELECT * FROM latest_locations
            WHERE route_id = ?
            ORDER BY station_seq, vehicle_id
            """,
            (normalized_route_id,),
        )

    def history_df(
        self,
        route_id: str | None = None,
        *,
        from_at: datetime | str | None = None,
        to_at: datetime | str | None = None,
    ) -> pd.DataFrame:
        conditions: list[str] = []
        params: list[Any] = []
        if route_id is not None:
            conditions.append("route_id = ?")
            params.append(_route_id(route_id))
        start = _datetime_param(from_at)
        if start is not None:
            conditions.append("observed_at >= ?")
            params.append(start)
        end = _datetime_param(to_at)
        if end is not None:
            conditions.append("observed_at <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self._read_dataframe(
            f"""
            SELECT * FROM location_history
            {where}
            ORDER BY observed_at, route_id, vehicle_id
            """,
            tuple(params),
        )

    def cache_status_df(self) -> pd.DataFrame:
        return self._read_dataframe(
            "SELECT * FROM cache_metadata ORDER BY resource"
        )
