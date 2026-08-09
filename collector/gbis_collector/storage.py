from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at_utc TEXT NOT NULL,
    collected_at_kst TEXT NOT NULL,
    route_id TEXT NOT NULL,
    api_key_id TEXT,
    query_time TEXT,
    result_code TEXT,
    result_message TEXT,
    vehicle_count INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    raw_response_zlib BLOB,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_kst
    ON collection_runs(collected_at_kst);
CREATE INDEX IF NOT EXISTS idx_collection_runs_route_kst
    ON collection_runs(route_id, collected_at_kst);

CREATE TABLE IF NOT EXISTS bus_locations (
    run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    observed_at_utc TEXT NOT NULL,
    observed_at_kst TEXT NOT NULL,
    query_time TEXT,
    route_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    plate_no TEXT,
    route_type_code INTEGER,
    station_id TEXT,
    station_seq INTEGER,
    remaining_seats INTEGER,
    crowded INTEGER,
    low_plate INTEGER,
    state_code INTEGER,
    tagless_code INTEGER,
    PRIMARY KEY (run_id, vehicle_id)
);

CREATE INDEX IF NOT EXISTS idx_bus_locations_vehicle_time
    ON bus_locations(vehicle_id, observed_at_kst);
CREATE INDEX IF NOT EXISTS idx_bus_locations_route_station_time
    ON bus_locations(route_id, station_seq, observed_at_kst);
CREATE INDEX IF NOT EXISTS idx_bus_locations_full
    ON bus_locations(route_id, remaining_seats, observed_at_kst);

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
    synced_at_kst TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (route_id, station_seq)
);

CREATE INDEX IF NOT EXISTS idx_route_stations_station
    ON route_stations(station_id);
"""


def now_pair() -> tuple[str, str]:
    now_utc = datetime.now(timezone.utc)
    return now_utc.isoformat(timespec="seconds"), now_utc.astimezone(SEOUL).isoformat(timespec="seconds")


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(collection_runs)").fetchall()
        }
        if "api_key_id" not in columns:
            connection.execute("ALTER TABLE collection_runs ADD COLUMN api_key_id TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_runs_key_kst "
            "ON collection_runs(api_key_id, collected_at_kst)"
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def requests_today(self, connection: sqlite3.Connection) -> int:
        today = datetime.now(SEOUL).date().isoformat()
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM collection_runs WHERE substr(collected_at_kst, 1, 10) = ?",
            (today,),
        ).fetchone()
        return int(row["count"])

    def requests_today_by_key(self, connection: sqlite3.Connection) -> dict[str | None, int]:
        today = datetime.now(SEOUL).date().isoformat()
        rows = connection.execute(
            """
            SELECT api_key_id, COUNT(*) AS count
            FROM collection_runs
            WHERE substr(collected_at_kst, 1, 10) = ?
            GROUP BY api_key_id
            """,
            (today,),
        ).fetchall()
        return {row["api_key_id"]: int(row["count"]) for row in rows}

    def save_collection(
        self,
        connection: sqlite3.Connection,
        *,
        route_id: str,
        api_key_id: str | None = None,
        header: dict[str, Any],
        locations: list[dict[str, Any]],
        http_status: int | None,
        elapsed_ms: int,
        raw_body: bytes,
        store_raw: bool,
        error: str | None = None,
    ) -> int:
        observed_at_utc, observed_at_kst = now_pair()
        query_time = header.get("queryTime")
        result_code = header.get("resultCode")
        result_message = header.get("resultMessage")
        compressed = zlib.compress(raw_body, level=6) if store_raw and raw_body else None

        cursor = connection.execute(
            """
            INSERT INTO collection_runs (
                collected_at_utc, collected_at_kst, route_id, api_key_id, query_time,
                result_code, result_message, vehicle_count, http_status,
                elapsed_ms, raw_response_zlib, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed_at_utc,
                observed_at_kst,
                route_id,
                api_key_id,
                query_time,
                None if result_code is None else str(result_code),
                result_message,
                len(locations),
                http_status,
                elapsed_ms,
                compressed,
                error,
            ),
        )
        run_id = int(cursor.lastrowid)

        rows = []
        for index, item in enumerate(locations):
            vehicle_id = item.get("vehId")
            if vehicle_id is None or vehicle_id == "":
                vehicle_id = f"unknown-{index}"
            rows.append(
                (
                    run_id,
                    observed_at_utc,
                    observed_at_kst,
                    query_time,
                    str(item.get("routeId") or route_id),
                    str(vehicle_id),
                    item.get("plateNo"),
                    _integer(item.get("routeTypeCd")),
                    None if item.get("stationId") is None else str(item.get("stationId")),
                    _integer(item.get("stationSeq")),
                    _integer(item.get("remainSeatCnt")),
                    _integer(item.get("crowded")),
                    _integer(item.get("lowPlate")),
                    _integer(item.get("stateCd")),
                    _integer(item.get("taglessCd")),
                )
            )

        connection.executemany(
            """
            INSERT INTO bus_locations (
                run_id, observed_at_utc, observed_at_kst, query_time, route_id,
                vehicle_id, plate_no, route_type_code, station_id, station_seq,
                remaining_seats, crowded, low_plate, state_code, tagless_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return run_id

    def save_route_stations(
        self,
        connection: sqlite3.Connection,
        route_id: str,
        stations: list[dict[str, Any]],
    ) -> int:
        _, synced_at_kst = now_pair()
        rows = []
        for item in stations:
            station_id = item.get("stationId")
            station_seq = _integer(item.get("stationSeq"))
            if station_id is None or station_seq is None:
                continue
            rows.append(
                (
                    route_id,
                    str(station_id),
                    station_seq,
                    item.get("stationName"),
                    None if item.get("mobileNo") is None else str(item.get("mobileNo")),
                    item.get("regionName"),
                    _float(item.get("x")),
                    _float(item.get("y")),
                    item.get("centerYn"),
                    synced_at_kst,
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                )
            )

        connection.executemany(
            """
            INSERT INTO route_stations (
                route_id, station_id, station_seq, station_name, mobile_no,
                region_name, x, y, center_yn, synced_at_kst, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id, station_seq) DO UPDATE SET
                station_id = excluded.station_id,
                station_name = excluded.station_name,
                mobile_no = excluded.mobile_no,
                region_name = excluded.region_name,
                x = excluded.x,
                y = excluded.y,
                center_yn = excluded.center_yn,
                synced_at_kst = excluded.synced_at_kst,
                raw_json = excluded.raw_json
            """,
            rows,
        )
        return len(rows)

    def summary(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT
                route_id,
                COUNT(*) AS runs,
                SUM(vehicle_count) AS observations,
                SUM(CASE WHEN error IS NOT NULL OR result_code NOT IN ('0', 0) THEN 1 ELSE 0 END) AS failures,
                MAX(collected_at_kst) AS last_collected_at_kst
            FROM collection_runs
            GROUP BY route_id
            ORDER BY route_id
            """
        ).fetchall()
