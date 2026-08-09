from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Iterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import ApiSettings
from .storage import SEOUL


def _database_uri(path: Path) -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"


@contextmanager
def _connect(settings: ApiSettings) -> Iterator[sqlite3.Connection]:
    if not settings.db_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="수집 데이터베이스를 찾을 수 없습니다.",
        )

    try:
        connection = sqlite3.connect(
            _database_uri(settings.db_path),
            uri=True,
            timeout=5,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="수집 데이터베이스에 연결할 수 없습니다.",
        ) from exc

    try:
        yield connection
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="수집 데이터를 조회할 수 없습니다.",
        ) from exc
    finally:
        connection.close()


def _location(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "observed_at": row["observed_at_kst"],
        "query_time": row["query_time"],
        "route_id": row["route_id"],
        "vehicle_id": row["vehicle_id"],
        "plate_no": row["plate_no"],
        "route_type_code": row["route_type_code"],
        "station_id": row["station_id"],
        "station_seq": row["station_seq"],
        "station_name": row["station_name"],
        "remaining_seats": row["remaining_seats"],
        "crowded": row["crowded"],
        "low_plate": row["low_plate"],
        "state_code": row["state_code"],
        "tagless_code": row["tagless_code"],
    }


def _encode_cursor(run_id: int, vehicle_id: str) -> str:
    raw = json.dumps([run_id, vehicle_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[0], int)
            or value[0] < 1
            or not isinstance(value[1], str)
        ):
            raise ValueError
        return value[0], value[1]
    except (
        ValueError,
        TypeError,
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise HTTPException(status_code=400, detail="cursor 형식이 올바르지 않습니다.") from exc


def _as_kst(value: datetime | None, *, default: datetime) -> datetime:
    result = value or default
    if result.tzinfo is None:
        result = result.replace(tzinfo=SEOUL)
    return result.astimezone(SEOUL)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    api_settings = settings or ApiSettings.load()
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API 키가 필요합니다.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = credentials.credentials
        if len(token) > 512:
            valid = False
        else:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            valid = any(
                hmac.compare_digest(digest, expected)
                for expected in api_settings.api_key_hashes
            )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API 키가 올바르지 않습니다.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    app = FastAPI(
        title="GBIS Collector API",
        description="경기도 광역버스 위치·잔여좌석 수집 데이터 읽기 전용 API",
        version="1.0.0",
    )
    router = APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        with _connect(api_settings) as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @router.get("/health")
    def health() -> dict[str, Any]:
        with _connect(api_settings) as connection:
            row = connection.execute(
                """
                SELECT
                    MAX(collected_at_kst) AS last_collected_at,
                    COUNT(DISTINCT route_id) AS route_count,
                    COUNT(*) AS collection_run_count,
                    COALESCE(SUM(vehicle_count), 0) AS observation_count,
                    COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS failure_count
                FROM collection_runs
                """
            ).fetchone()
        return {
            "status": "ok" if row["last_collected_at"] else "empty",
            **dict(row),
        }

    @router.get("/routes")
    def routes() -> dict[str, Any]:
        with _connect(api_settings) as connection:
            rows = connection.execute(
                """
                WITH runs AS (
                    SELECT
                        route_id,
                        MIN(collected_at_kst) AS first_collected_at,
                        MAX(collected_at_kst) AS last_collected_at,
                        SUM(vehicle_count) AS observation_count
                    FROM collection_runs
                    GROUP BY route_id
                ), stations AS (
                    SELECT route_id, COUNT(*) AS station_count
                    FROM route_stations
                    GROUP BY route_id
                )
                SELECT
                    runs.route_id,
                    COALESCE(stations.station_count, 0) AS station_count,
                    runs.observation_count,
                    runs.first_collected_at,
                    runs.last_collected_at
                FROM runs
                LEFT JOIN stations ON stations.route_id = runs.route_id
                ORDER BY runs.route_id
                """
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @router.get("/routes/{route_id}/stations")
    def route_stations(route_id: str) -> dict[str, Any]:
        if not route_id.isdigit():
            raise HTTPException(status_code=400, detail="route_id는 숫자여야 합니다.")
        with _connect(api_settings) as connection:
            rows = connection.execute(
                """
                SELECT
                    route_id, station_id, station_seq, station_name, mobile_no,
                    region_name, x, y, center_yn, synced_at_kst
                FROM route_stations
                WHERE route_id = ?
                ORDER BY station_seq
                """,
                (route_id,),
            ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="노선 정류장 정보가 없습니다.")
        return {"route_id": route_id, "items": [dict(row) for row in rows]}

    @router.get("/locations/latest")
    def latest_locations(route_id: str | None = None) -> dict[str, Any]:
        if route_id is not None and not route_id.isdigit():
            raise HTTPException(status_code=400, detail="route_id는 숫자여야 합니다.")

        route_filter = "AND route_id = ?" if route_id else ""
        parameters: tuple[Any, ...] = (route_id,) if route_id else ()
        with _connect(api_settings) as connection:
            rows = connection.execute(
                f"""
                WITH latest_runs AS (
                    SELECT route_id, MAX(id) AS run_id
                    FROM collection_runs
                    WHERE error IS NULL
                      AND (result_code IS NULL OR result_code = '0')
                      {route_filter}
                    GROUP BY route_id
                )
                SELECT
                    locations.*,
                    stations.station_name
                FROM latest_runs
                JOIN bus_locations AS locations ON locations.run_id = latest_runs.run_id
                LEFT JOIN route_stations AS stations
                  ON stations.route_id = locations.route_id
                 AND stations.station_seq = locations.station_seq
                ORDER BY locations.route_id, locations.station_seq, locations.vehicle_id
                """,
                parameters,
            ).fetchall()
        return {"items": [_location(row) for row in rows]}

    @router.get("/locations")
    def location_history(
        route_id: str,
        from_at: Annotated[datetime | None, Query(alias="from")] = None,
        to_at: Annotated[datetime | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1)] = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not route_id.isdigit():
            raise HTTPException(status_code=400, detail="route_id는 숫자여야 합니다.")
        if limit > api_settings.max_page_size:
            raise HTTPException(
                status_code=400,
                detail=f"limit은 최대 {api_settings.max_page_size}입니다.",
            )

        now = datetime.now(SEOUL)
        end = _as_kst(to_at, default=now)
        start = _as_kst(from_at, default=end - timedelta(days=1))
        if start > end:
            raise HTTPException(status_code=400, detail="from은 to보다 늦을 수 없습니다.")
        if end - start > timedelta(days=api_settings.max_history_days):
            raise HTTPException(
                status_code=400,
                detail=f"조회 기간은 최대 {api_settings.max_history_days}일입니다.",
            )

        conditions = [
            "runs.route_id = ?",
            "runs.collected_at_kst >= ?",
            "runs.collected_at_kst <= ?",
        ]
        parameters: list[Any] = [
            route_id,
            start.isoformat(timespec="seconds"),
            end.isoformat(timespec="seconds"),
        ]
        if cursor:
            cursor_run_id, cursor_vehicle_id = _decode_cursor(cursor)
            conditions.append(
                "(locations.run_id < ? OR "
                "(locations.run_id = ? AND locations.vehicle_id > ?))"
            )
            parameters.extend([cursor_run_id, cursor_run_id, cursor_vehicle_id])

        parameters.append(limit + 1)
        with _connect(api_settings) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    locations.*,
                    stations.station_name
                FROM collection_runs AS runs
                JOIN bus_locations AS locations ON locations.run_id = runs.id
                LEFT JOIN route_stations AS stations
                  ON stations.route_id = locations.route_id
                 AND stations.station_seq = locations.station_seq
                WHERE {' AND '.join(conditions)}
                ORDER BY locations.run_id DESC, locations.vehicle_id ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(last["run_id"], last["vehicle_id"])
        return {
            "items": [_location(row) for row in page],
            "next_cursor": next_cursor,
        }

    app.include_router(router)
    return app


def run() -> None:
    import uvicorn

    settings = ApiSettings.load()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
