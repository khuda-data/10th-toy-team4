"""개인 공공데이터 API 키로 route_id와 실제 버스 번호를 로컬에 매핑한다."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY = PROJECT_ROOT / "data/csv/history_all.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "route_names.csv"
ROUTE_INFO_URL = (
    "https://apis.data.go.kr/6410000"
    "/busrouteservice/v2/getBusRouteInfoItemv2"
)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def service_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    multiple = os.environ.get("GBIS_SERVICE_KEYS", "")
    candidates = [value.strip() for value in multiple.split(",") if value.strip()]
    single = os.environ.get("GBIS_SERVICE_KEY", "").strip()
    if single:
        candidates.append(single)
    if not candidates:
        raise ValueError(
            "공공데이터 API 키가 없습니다. 프로젝트 .env에 "
            "GBIS_SERVICE_KEY=발급받은키 를 설정하세요."
        )
    return candidates[0]


def route_ids_from_history(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"전체 이력 파일이 없습니다: {path}")
    route_ids: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if "route_id" not in (reader.fieldnames or []):
            raise ValueError(f"route_id 컬럼이 없습니다: {path}")
        for row in reader:
            value = str(row.get("route_id") or "").strip()
            if value:
                route_ids.add(value)
    return sorted(route_ids)


def fetch_route_name(route_id: str, key: str, timeout: int) -> str:
    encoded_key = key if "%" in key else quote(key, safe="")
    query = urlencode({"routeId": route_id, "format": "json"})
    request = Request(
        f"{ROUTE_INFO_URL}?serviceKey={encoded_key}&{query}",
        headers={"Accept": "application/json", "User-Agent": "toy-team4-local-route-map/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"노선 {route_id} 조회 실패: {error}") from error

    response = payload.get("response", payload)
    header = response.get("msgHeader") or {}
    if str(header.get("resultCode", "")) not in {"", "0"}:
        raise RuntimeError(
            f"노선 {route_id} 조회 실패: {header.get('resultMessage') or header}"
        )
    item = (response.get("msgBody") or {}).get("busRouteInfoItem") or {}
    if isinstance(item, list):
        item = item[0] if item else {}
    name = str(item.get("routeName") or "").strip()
    if not name:
        raise RuntimeError(f"노선 {route_id} 응답에 routeName이 없습니다.")
    return name


def existing_names(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        return {
            str(row.get("route_id") or "").strip():
            str(row.get("route_name") or "").strip()
            for row in csv.DictReader(file)
            if str(row.get("route_id") or "").strip()
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 route_id-route_name 매핑 생성")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    key = service_key()
    route_ids = route_ids_from_history(args.history)
    names = existing_names(args.output)
    failures: list[str] = []
    for index, route_id in enumerate(route_ids, 1):
        try:
            name = fetch_route_name(route_id, key, args.timeout)
            names[route_id] = name
            print(f"[{index}/{len(route_ids)}] {route_id} -> {name}")
        except RuntimeError as error:
            failures.append(str(error))
            print(f"[{index}/{len(route_ids)}] {error}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["route_id", "route_name"])
        writer.writerows((route_id, names.get(route_id, "")) for route_id in route_ids)
    filled = sum(bool(names.get(route_id)) for route_id in route_ids)
    print(f"저장: {args.output} ({filled}/{len(route_ids)}개 이름 보유)")
    if failures:
        print("조회 실패 노선:")
        for failure in failures:
            print(f"- {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
