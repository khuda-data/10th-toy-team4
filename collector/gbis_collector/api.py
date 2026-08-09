from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://apis.data.go.kr/6410000"
LOCATION_PATH = "/buslocationservice/v2/getBusLocationListv2"
ROUTE_SEARCH_PATH = "/busrouteservice/v2/getBusRouteListv2"
ROUTE_STATIONS_PATH = "/busrouteservice/v2/getBusRouteStationListv2"


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        raw_body: bytes = b"",
        elapsed_ms: int = 0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw_body = raw_body
        self.elapsed_ms = elapsed_ms


@dataclass(frozen=True)
class ApiResponse:
    payload: dict[str, Any]
    raw_body: bytes
    status_code: int
    elapsed_ms: int


def _encoded_service_key(service_key: str) -> str:
    # 공공데이터포털은 Encoding/Decoding 두 종류의 키를 제공합니다.
    # 이미 URL 인코딩된 키는 그대로 사용해 이중 인코딩을 피합니다.
    if "%" in service_key:
        return service_key
    return quote(service_key, safe="")


class GbisClient:
    def __init__(self, service_key: str, *, timeout_seconds: int = 20) -> None:
        self.service_key = service_key
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, params: dict[str, str]) -> ApiResponse:
        query = urlencode({**params, "format": "json"})
        url = f"{BASE_URL}{path}?serviceKey={_encoded_service_key(self.service_key)}&{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "10th-toy-team4-gbis-collector/0.1",
            },
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read()
                status_code = response.status
        except HTTPError as exc:
            raw_body = exc.read()
            elapsed_ms = round((time.monotonic() - started) * 1000)
            raise ApiError(
                f"GBIS API HTTP 오류: {exc.code}",
                status_code=exc.code,
                raw_body=raw_body,
                elapsed_ms=elapsed_ms,
            ) from exc
        except (URLError, TimeoutError) as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            raise ApiError(f"GBIS API 연결 오류: {exc}", elapsed_ms=elapsed_ms) from exc

        elapsed_ms = round((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(raw_body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            preview = raw_body[:200].decode("utf-8", errors="replace")
            raise ApiError(
                f"GBIS API가 JSON이 아닌 응답을 반환했습니다: {preview}",
                status_code=status_code,
                raw_body=raw_body,
                elapsed_ms=elapsed_ms,
            ) from exc

        if not isinstance(payload, dict):
            raise ApiError(
                "GBIS API JSON 최상위 값이 객체가 아닙니다.",
                status_code=status_code,
                raw_body=raw_body,
                elapsed_ms=elapsed_ms,
            )
        return ApiResponse(payload, raw_body, status_code, elapsed_ms)

    def bus_locations(self, route_id: str) -> ApiResponse:
        return self._request(LOCATION_PATH, {"routeId": route_id})

    def search_routes(self, keyword: str) -> ApiResponse:
        return self._request(ROUTE_SEARCH_PATH, {"keyword": keyword})

    def route_stations(self, route_id: str) -> ApiResponse:
        return self._request(ROUTE_STATIONS_PATH, {"routeId": route_id})


def response_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return {}, {}
    header = response.get("msgHeader") or {}
    body = response.get("msgBody") or {}
    return (
        header if isinstance(header, dict) else {},
        body if isinstance(body, dict) else {},
    )


def as_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def location_items(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header, body = response_parts(payload)
    return header, as_list(body.get("busLocationList"))


def route_items(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header, body = response_parts(payload)
    return header, as_list(body.get("busRouteList"))


def station_items(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header, body = response_parts(payload)
    return header, as_list(body.get("busRouteStationList"))
