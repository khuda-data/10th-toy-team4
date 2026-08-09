import sqlite3
import tempfile
import unittest
from pathlib import Path

from gbis_collector.storage import Storage


class StorageTest(unittest.TestCase):
    def test_existing_database_is_migrated_for_key_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE collection_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        collected_at_utc TEXT NOT NULL,
                        collected_at_kst TEXT NOT NULL,
                        route_id TEXT NOT NULL,
                        query_time TEXT,
                        result_code TEXT,
                        result_message TEXT,
                        vehicle_count INTEGER NOT NULL DEFAULT 0,
                        http_status INTEGER,
                        elapsed_ms INTEGER NOT NULL DEFAULT 0,
                        raw_response_zlib BLOB,
                        error TEXT
                    )
                    """
                )

            storage = Storage(db_path)
            with storage.connect() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(collection_runs)").fetchall()
                }
                self.assertIn("api_key_id", columns)

    def test_save_collection_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "test.sqlite3")
            with storage.connect() as connection:
                run_id = storage.save_collection(
                    connection,
                    route_id="233000031",
                    header={
                        "queryTime": "2026-08-03 08:00:00",
                        "resultCode": 0,
                        "resultMessage": "정상",
                    },
                    locations=[
                        {
                            "routeId": 233000031,
                            "vehId": 123,
                            "plateNo": "경기00가0000",
                            "routeTypeCd": 14,
                            "stationId": 456,
                            "stationSeq": 10,
                            "remainSeatCnt": 0,
                            "lowPlate": 0,
                            "stateCd": 2,
                        }
                    ],
                    http_status=200,
                    elapsed_ms=42,
                    raw_body=b'{"ok":true}',
                    store_raw=True,
                )
                self.assertGreater(run_id, 0)
                location = connection.execute("SELECT * FROM bus_locations").fetchone()
                self.assertEqual(location["remaining_seats"], 0)
                self.assertEqual(location["vehicle_id"], "123")

                summary = storage.summary(connection)
                self.assertEqual(len(summary), 1)
                self.assertEqual(summary[0]["observations"], 1)

    def test_requests_today_are_counted_per_anonymous_key_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "test.sqlite3")
            with storage.connect() as connection:
                for key_id in ("key-a", "key-b", "key-a"):
                    storage.save_collection(
                        connection,
                        route_id="233000031",
                        api_key_id=key_id,
                        header={"resultCode": 0},
                        locations=[],
                        http_status=200,
                        elapsed_ms=1,
                        raw_body=b"{}",
                        store_raw=False,
                    )

                self.assertEqual(
                    storage.requests_today_by_key(connection),
                    {"key-a": 2, "key-b": 1},
                )

    def test_route_station_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "test.sqlite3")
            with storage.connect() as connection:
                count = storage.save_route_stations(
                    connection,
                    "233000031",
                    [
                        {
                            "stationId": 100,
                            "stationSeq": 1,
                            "stationName": "기점",
                            "x": 127.0,
                            "y": 37.0,
                        }
                    ],
                )
                self.assertEqual(count, 1)
                row = connection.execute("SELECT * FROM route_stations").fetchone()
                self.assertEqual(row["station_name"], "기점")


if __name__ == "__main__":
    unittest.main()
