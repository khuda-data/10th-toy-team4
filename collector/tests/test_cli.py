from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gbis_collector.api import ApiResponse
from gbis_collector.cli import (
    ESTIMATED_RUNS_PER_ROUTE,
    _active_route_ids,
    _key_id,
    _route_capacity,
    collect,
)
from gbis_collector.config import Settings
from gbis_collector.storage import Storage


class MultiKeyCollectionTest(unittest.TestCase):
    def test_active_routes_expand_with_key_count_and_stop_at_candidate_count(self) -> None:
        settings = Settings(
            service_keys=("first-key",),
            route_ids=tuple(str(index) for index in range(1, 11)),
            db_path=Path("test.sqlite3"),
            timeout_seconds=1,
            daily_request_limit=1000,
            store_raw_responses=False,
            routes_file=Path("routes.txt"),
        )

        self.assertEqual(_route_capacity(settings), 1)
        self.assertEqual(_active_route_ids(settings), ("1",))

        below_one_route = replace(settings, daily_request_limit=800)
        self.assertEqual(_route_capacity(below_one_route), 0)
        self.assertEqual(_active_route_ids(below_one_route), ())

        two_keys = replace(settings, service_keys=("first-key", "second-key"))
        self.assertEqual(_route_capacity(two_keys), 2)
        self.assertEqual(_active_route_ids(two_keys), ("1", "2"))

        nine_keys = replace(settings, service_keys=tuple(f"key-{index}" for index in range(9)))
        self.assertEqual(_route_capacity(nine_keys), 11)
        self.assertEqual(_active_route_ids(nine_keys), settings.route_ids)

    def test_collect_balances_requests_across_automatically_active_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite3"
            settings = Settings(
                service_keys=("first-key", "second-key"),
                route_ids=("1", "2", "3"),
                db_path=db_path,
                timeout_seconds=1,
                daily_request_limit=ESTIMATED_RUNS_PER_ROUTE,
                store_raw_responses=False,
                routes_file=Path(temp_dir) / "routes.txt",
            )
            response = ApiResponse(
                payload={"response": {"msgHeader": {"resultCode": 0}, "msgBody": {}}},
                raw_body=b"{}",
                status_code=200,
                elapsed_ms=1,
            )

            with (
                patch("gbis_collector.cli.Settings.load", return_value=settings),
                patch(
                    "gbis_collector.cli.GbisClient.bus_locations",
                    autospec=True,
                    return_value=response,
                ) as request,
            ):
                self.assertEqual(collect(), 0)

            self.assertEqual(request.call_count, 2)
            requested_keys = [call.args[0].service_key for call in request.call_args_list]
            self.assertEqual(requested_keys, ["first-key", "second-key"])

            storage = Storage(db_path)
            with storage.connect() as connection:
                self.assertEqual(
                    storage.requests_today_by_key(connection),
                    {_key_id("first-key"): 1, _key_id("second-key"): 1},
                )


if __name__ == "__main__":
    unittest.main()
