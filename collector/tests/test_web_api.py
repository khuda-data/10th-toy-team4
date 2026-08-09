import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from gbis_collector.config import ApiSettings
from gbis_collector.storage import Storage
from gbis_collector.web_api import create_app


TEST_API_KEY = "gbis_test_key_1234567890"
ROUTE_ID = "233000031"


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        storage = Storage(self.db_path)
        with storage.connect() as connection:
            storage.save_route_stations(
                connection,
                ROUTE_ID,
                [
                    {
                        "stationId": 100,
                        "stationSeq": 1,
                        "stationName": "기점",
                        "mobileNo": "12345",
                        "x": 127.0,
                        "y": 37.0,
                    },
                    {
                        "stationId": 200,
                        "stationSeq": 2,
                        "stationName": "다음 정류장",
                        "mobileNo": "23456",
                        "x": 127.1,
                        "y": 37.1,
                    },
                ],
            )
            storage.save_collection(
                connection,
                route_id=ROUTE_ID,
                api_key_id="collector-key",
                header={"queryTime": "2026-08-09 08:00:00", "resultCode": 0},
                locations=[
                    {
                        "routeId": ROUTE_ID,
                        "vehId": 10,
                        "plateNo": "경기00가0010",
                        "stationId": 100,
                        "stationSeq": 1,
                        "remainSeatCnt": 12,
                    },
                    {
                        "routeId": ROUTE_ID,
                        "vehId": 20,
                        "plateNo": "경기00가0020",
                        "stationId": 200,
                        "stationSeq": 2,
                        "remainSeatCnt": 3,
                    },
                ],
                http_status=200,
                elapsed_ms=10,
                raw_body=b"{}",
                store_raw=False,
            )

        settings = ApiSettings.from_api_keys(
            db_path=self.db_path,
            api_keys=(TEST_API_KEY,),
            max_page_size=2,
            max_history_days=30,
        )
        self.app = create_app(settings)
        self.auth = {"Authorization": f"Bearer {TEST_API_KEY}"}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _request_async(self, method: str, url: str, **kwargs) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        return asyncio.run(self._request_async(method, url, **kwargs))

    def test_healthz_is_public_but_data_endpoints_require_api_key(self) -> None:
        self.assertEqual(self.request("GET", "/healthz").status_code, 200)

        missing = self.request("GET", "/v1/routes")
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")

        invalid = self.request(
            "GET",
            "/v1/routes",
            headers={"Authorization": "Bearer wrong-key"},
        )
        self.assertEqual(invalid.status_code, 401)

    def test_routes_stations_and_latest_locations(self) -> None:
        routes = self.request("GET", "/v1/routes", headers=self.auth)
        self.assertEqual(routes.status_code, 200)
        self.assertEqual(routes.json()["items"][0]["route_id"], ROUTE_ID)
        self.assertEqual(routes.json()["items"][0]["station_count"], 2)

        stations = self.request(
            "GET",
            f"/v1/routes/{ROUTE_ID}/stations",
            headers=self.auth,
        )
        self.assertEqual(stations.status_code, 200)
        self.assertEqual(stations.json()["items"][1]["station_name"], "다음 정류장")

        latest = self.request(
            "GET",
            "/v1/locations/latest",
            params={"route_id": ROUTE_ID},
            headers=self.auth,
        )
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(len(latest.json()["items"]), 2)
        self.assertEqual(latest.json()["items"][0]["station_name"], "기점")
        self.assertEqual(latest.json()["items"][0]["remaining_seats"], 12)

    def test_location_history_uses_cursor_pagination(self) -> None:
        first = self.request(
            "GET",
            "/v1/locations",
            params={"route_id": ROUTE_ID, "limit": 1},
            headers=self.auth,
        )
        self.assertEqual(first.status_code, 200)
        first_body = first.json()
        self.assertEqual(len(first_body["items"]), 1)
        self.assertIsNotNone(first_body["next_cursor"])

        second = self.request(
            "GET",
            "/v1/locations",
            params={
                "route_id": ROUTE_ID,
                "limit": 1,
                "cursor": first_body["next_cursor"],
            },
            headers=self.auth,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()["items"]), 1)
        self.assertNotEqual(
            first_body["items"][0]["vehicle_id"],
            second.json()["items"][0]["vehicle_id"],
        )

        malformed = self.request(
            "GET",
            "/v1/locations",
            params={"route_id": ROUTE_ID, "cursor": "not-a-cursor"},
            headers=self.auth,
        )
        self.assertEqual(malformed.status_code, 400)

    def test_page_size_limit_is_enforced(self) -> None:
        response = self.request(
            "GET",
            "/v1/locations",
            params={"route_id": ROUTE_ID, "limit": 3},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
