import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from gbis_collector.api import as_list, location_items, route_items, station_items
from gbis_collector.config import _load_dotenv, _read_service_keys


class ApiParsingTest(unittest.TestCase):
    def test_location_list_payload(self) -> None:
        payload = {
            "response": {
                "msgHeader": {"resultCode": 0, "queryTime": "2026-08-03 08:00:00"},
                "msgBody": {
                    "busLocationList": [
                        {"vehId": 1, "routeId": 100, "remainSeatCnt": 0},
                        {"vehId": 2, "routeId": 100, "remainSeatCnt": 12},
                    ]
                },
            }
        }
        header, locations = location_items(payload)
        self.assertEqual(header["resultCode"], 0)
        self.assertEqual(len(locations), 2)
        self.assertEqual(locations[0]["remainSeatCnt"], 0)

    def test_single_item_is_normalized_to_list(self) -> None:
        payload = {
            "response": {
                "msgHeader": {"resultCode": 0},
                "msgBody": {"busRouteList": {"routeId": 100, "routeName": "1000"}},
            }
        }
        _, routes = route_items(payload)
        self.assertEqual(routes, [{"routeId": 100, "routeName": "1000"}])

    def test_null_station_list(self) -> None:
        payload = {"response": {"msgHeader": {"resultCode": 4}, "msgBody": None}}
        header, stations = station_items(payload)
        self.assertEqual(header["resultCode"], 4)
        self.assertEqual(stations, [])

    def test_invalid_list_values_are_ignored(self) -> None:
        self.assertEqual(as_list([{"id": 1}, None, "bad"]), [{"id": 1}])

    def test_single_line_env_is_treated_as_service_key(self) -> None:
        previous = os.environ.pop("GBIS_SERVICE_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env_file = Path(temp_dir) / ".env"
                env_file.write_text("encoded-api-key-only\n", encoding="utf-8")
                _load_dotenv(env_file)
                self.assertEqual(os.environ["GBIS_SERVICE_KEY"], "encoded-api-key-only")
        finally:
            os.environ.pop("GBIS_SERVICE_KEY", None)
            if previous is not None:
                os.environ["GBIS_SERVICE_KEY"] = previous

    def test_multiple_service_keys_are_trimmed_and_deduplicated(self) -> None:
        with patch.dict(
            os.environ,
            {"GBIS_SERVICE_KEYS": " first ,second,first ", "GBIS_SERVICE_KEY": "legacy"},
            clear=True,
        ):
            self.assertEqual(_read_service_keys(), ("first", "second"))

    def test_single_service_key_remains_supported(self) -> None:
        with patch.dict(os.environ, {"GBIS_SERVICE_KEY": "legacy"}, clear=True):
            self.assertEqual(_read_service_keys(), ("legacy",))


if __name__ == "__main__":
    unittest.main()
