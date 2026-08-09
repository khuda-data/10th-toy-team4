import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import httpx

from gbis_client import GBISApiCache, GBISClientError


ROUTE_ID = "233000031"


class GBISApiCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp_dir.name) / "cache.sqlite3"
        self.latest_calls = 0
        self.fail_history_second_page = False
        self.history_requests: list[dict[str, list[str]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer team-key")
            query = parse_qs(request.url.query.decode())
            if request.url.path == "/v1/routes":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "route_id": ROUTE_ID,
                                "station_count": 2,
                                "observation_count": 10,
                                "first_collected_at": "2026-08-01T00:00:00+09:00",
                                "last_collected_at": "2026-08-09T13:00:00+09:00",
                            }
                        ]
                    },
                )
            if request.url.path == f"/v1/routes/{ROUTE_ID}/stations":
                return httpx.Response(
                    200,
                    json={
                        "route_id": ROUTE_ID,
                        "items": [
                            {
                                "route_id": ROUTE_ID,
                                "station_id": "100",
                                "station_seq": 1,
                                "station_name": "기점",
                                "mobile_no": "12345",
                                "region_name": "경기",
                                "x": 127.0,
                                "y": 37.0,
                                "center_yn": "N",
                                "synced_at_kst": "2026-08-09T12:00:00+09:00",
                            },
                            {
                                "route_id": ROUTE_ID,
                                "station_id": "200",
                                "station_seq": 2,
                                "station_name": "종점",
                                "mobile_no": "23456",
                                "region_name": "서울",
                                "x": 127.1,
                                "y": 37.1,
                                "center_yn": "N",
                                "synced_at_kst": "2026-08-09T12:00:00+09:00",
                            },
                        ],
                    },
                )
            if request.url.path == "/v1/locations/latest":
                self.latest_calls += 1
                items = [self._location("10", 1, 12)]
                if self.latest_calls == 1:
                    items.append(self._location("20", 2, 3))
                return httpx.Response(200, json={"items": items})
            if request.url.path == "/v1/locations":
                self.history_requests.append(query)
                if query.get("cursor") == ["next-page"]:
                    if self.fail_history_second_page:
                        return httpx.Response(503, json={"detail": "temporary"})
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                self._location(
                                    "20",
                                    2,
                                    3,
                                    observed_at="2026-08-09T13:00:00+09:00",
                                )
                            ],
                            "next_cursor": None,
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            self._location(
                                "10",
                                1,
                                12,
                                observed_at="2026-08-09T13:00:00+09:00",
                            )
                        ],
                        "next_cursor": "next-page",
                    },
                )
            return httpx.Response(404, json={"detail": "not found"})

        self.cache = GBISApiCache(
            base_url="https://example.test",
            api_key="team-key",
            cache_path=self.cache_path,
            transport=httpx.MockTransport(handler),
        )

    def tearDown(self) -> None:
        self.cache.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _location(
        vehicle_id: str,
        station_seq: int,
        remaining_seats: int,
        *,
        observed_at: str = "2026-08-09T13:05:00+09:00",
    ) -> dict[str, object]:
        return {
            "observed_at": observed_at,
            "query_time": "2026-08-09 13:05:00",
            "route_id": ROUTE_ID,
            "vehicle_id": vehicle_id,
            "plate_no": f"경기00가00{vehicle_id}",
            "route_type_code": 14,
            "station_id": str(station_seq * 100),
            "station_seq": station_seq,
            "station_name": "기점" if station_seq == 1 else "종점",
            "remaining_seats": remaining_seats,
            "crowded": 1,
            "low_plate": 0,
            "state_code": 2,
            "tagless_code": 0,
        }

    def test_refresh_all_and_read_dataframes(self) -> None:
        counts = self.cache.refresh_all()

        self.assertEqual(
            counts,
            {"routes": 1, "stations": 2, "latest_locations": 2},
        )
        self.assertEqual(self.cache.routes_df().iloc[0]["route_id"], ROUTE_ID)
        self.assertEqual(len(self.cache.stations_df(ROUTE_ID)), 2)
        latest = self.cache.latest_locations_df(ROUTE_ID)
        self.assertEqual(len(latest), 2)
        self.assertEqual(int(latest.iloc[0]["remaining_seats"]), 12)

    def test_refresh_latest_replaces_stale_snapshot(self) -> None:
        self.assertEqual(self.cache.refresh_latest(ROUTE_ID), 2)
        self.assertEqual(self.cache.refresh_latest(ROUTE_ID), 1)

        latest = self.cache.latest_locations_df(ROUTE_ID)
        self.assertEqual(latest["vehicle_id"].tolist(), ["10"])

    def test_refresh_history_follows_cursor_and_is_incremental(self) -> None:
        self.assertEqual(self.cache.refresh_history(ROUTE_ID, page_size=1), 2)
        self.assertEqual(len(self.cache.history_df(ROUTE_ID)), 2)
        self.assertEqual(self.history_requests[1]["cursor"], ["next-page"])

        self.history_requests.clear()
        self.assertEqual(self.cache.refresh_history(ROUTE_ID, page_size=1), 2)
        self.assertEqual(
            self.history_requests[0]["from"],
            ["2026-08-09T13:00:00+09:00"],
        )
        self.assertEqual(len(self.cache.history_df(ROUTE_ID)), 2)

    def test_failed_history_page_does_not_advance_checkpoint(self) -> None:
        self.fail_history_second_page = True
        with self.assertRaisesRegex(GBISClientError, "HTTP 503"):
            self.cache.refresh_history(ROUTE_ID, page_size=1)

        self.assertEqual(len(self.cache.history_df(ROUTE_ID)), 1)
        self.history_requests.clear()
        self.fail_history_second_page = False

        self.assertEqual(self.cache.refresh_history(ROUTE_ID, page_size=1), 2)
        self.assertNotIn("from", self.history_requests[0])
        self.assertEqual(len(self.cache.history_df(ROUTE_ID)), 2)

    def test_api_error_is_exposed_without_key(self) -> None:
        with self.assertRaisesRegex(GBISClientError, "HTTP 404") as context:
            self.cache._get("/missing")
        self.assertNotIn("team-key", str(context.exception))

    def test_from_env_reads_dotenv_and_environment_takes_precedence(self) -> None:
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "GBIS_API_BASE_URL=https://file.example",
                    "GBIS_API_KEY='file-key'",
                    "GBIS_API_CACHE_PATH=data/from-file.sqlite3",
                ]
            ),
            encoding="utf-8",
        )
        overridden_cache = Path(self.temp_dir.name) / "from-env.sqlite3"
        seen_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers["Authorization"])
            return httpx.Response(200, json={"items": []})

        with patch.dict(
            os.environ,
            {
                "GBIS_API_BASE_URL": "https://environment.example",
                "GBIS_API_KEY": "environment-key",
            },
            clear=True,
        ):
            client = GBISApiCache.from_env(
                env_file=env_file,
                cache_path=overridden_cache,
                transport=httpx.MockTransport(handler),
            )
            try:
                self.assertEqual(client.base_url, "https://environment.example")
                self.assertEqual(client.cache_path, overridden_cache)
                self.assertEqual(client.refresh_latest(), 0)
            finally:
                client.close()

        self.assertEqual(seen_headers, ["Bearer environment-key"])

    def test_full_history_is_split_into_30_day_windows_and_then_incremental(self) -> None:
        requests: list[dict[str, list[str]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/routes":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "route_id": ROUTE_ID,
                                "station_count": 2,
                                "observation_count": 10,
                                "first_collected_at": "2026-01-01T00:00:00+09:00",
                                "last_collected_at": "2026-03-16T00:00:00+09:00",
                            }
                        ]
                    },
                )
            if request.url.path == "/v1/locations":
                query = parse_qs(request.url.query.decode())
                requests.append(query)
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            self._location(
                                str(len(requests)),
                                1,
                                12,
                                observed_at=query["to"][0],
                            )
                        ],
                        "next_cursor": None,
                    },
                )
            return httpx.Response(404, json={"detail": "not found"})

        cache = GBISApiCache(
            base_url="https://example.test",
            api_key="team-key",
            cache_path=Path(self.temp_dir.name) / "full.sqlite3",
            transport=httpx.MockTransport(handler),
        )
        try:
            total = cache.refresh_full_history(
                ROUTE_ID,
                to_at="2026-03-15T00:00:00+09:00",
            )
            self.assertEqual(total, 3)
            self.assertEqual(len(requests), 3)
            for request_query in requests:
                start = datetime.fromisoformat(request_query["from"][0])
                end = datetime.fromisoformat(request_query["to"][0])
                self.assertLessEqual((end - start).days, 30)

            requests.clear()
            self.assertEqual(
                cache.refresh_full_history(
                    ROUTE_ID,
                    to_at="2026-03-16T00:00:00+09:00",
                ),
                1,
            )
            self.assertEqual(
                requests[0]["from"],
                ["2026-03-15T00:00:00+09:00"],
            )
        finally:
            cache.close()

    def test_rate_limit_response_is_retried(self) -> None:
        attempts = 0
        waits: list[float] = []

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, json={"detail": "slow down"})
            return httpx.Response(200, json={"items": []})

        cache = GBISApiCache(
            base_url="https://example.test",
            api_key="team-key",
            cache_path=Path(self.temp_dir.name) / "retry.sqlite3",
            rate_limit_wait_seconds=0.5,
            transport=httpx.MockTransport(handler),
            sleep=waits.append,
        )
        try:
            self.assertEqual(cache.refresh_routes(), 0)
        finally:
            cache.close()

        self.assertEqual(attempts, 2)
        self.assertEqual(waits, [0.5])


if __name__ == "__main__":
    unittest.main()
