from __future__ import annotations

import argparse
import fcntl
import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from datetime import datetime

from .api import ApiError, GbisClient, location_items, route_items, station_items
from .config import ConfigError, Settings
from .storage import Storage


PEAK_MINUTES_PER_DAY = 11 * 60
OFFPEAK_RUNS_PER_DAY = 13 * 12
ESTIMATED_RUNS_PER_ROUTE = PEAK_MINUTES_PER_DAY + OFFPEAK_RUNS_PER_DAY


@contextmanager
def singleton_lock(db_path: Path) -> Iterator[None]:
    lock_path = db_path.with_suffix(db_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("다른 수집 작업이 아직 실행 중이므로 이번 실행을 건너뜁니다.")
            yield False
            return
        yield True


def _client(settings: Settings) -> GbisClient:
    return GbisClient(settings.service_key, timeout_seconds=settings.timeout_seconds)


def _key_id(service_key: str) -> str:
    """로그와 DB에 원문 키를 남기지 않는 짧은 식별자."""
    return hashlib.sha256(service_key.encode("utf-8")).hexdigest()[:12]


def _clients(settings: Settings) -> list[tuple[str, GbisClient]]:
    return [
        (key_id, GbisClient(service_key, timeout_seconds=settings.timeout_seconds))
        for service_key in settings.service_keys
        for key_id in (_key_id(service_key),)
    ]


def _route_capacity(settings: Settings) -> int:
    return settings.total_daily_request_limit // ESTIMATED_RUNS_PER_ROUTE


def _active_route_ids(settings: Settings) -> tuple[str, ...]:
    return settings.route_ids[: _route_capacity(settings)]


def collect() -> int:
    settings = Settings.load(require_routes=True)
    storage = Storage(settings.db_path)
    clients = _clients(settings)
    active_route_ids = _active_route_ids(settings)
    successes = 0
    failures = 0
    observations = 0

    if not active_route_ids:
        print(
            f"현재 총 일일 안전 한도 {settings.total_daily_request_limit:,}회로는 "
            f"노선당 예상 {ESTIMATED_RUNS_PER_ROUTE:,}회를 감당할 수 없습니다.",
            file=sys.stderr,
        )
        return 1

    with singleton_lock(settings.db_path) as acquired:
        if not acquired:
            return 0
        with storage.connect() as connection:
            usage = storage.requests_today_by_key(connection)
            # 다중 키 지원 전 기록은 당시 사용하던 첫 번째 키의 사용량으로 보수적으로 계산합니다.
            usage[clients[0][0]] = usage.get(clients[0][0], 0) + usage.pop(None, 0)
            for key_id, _ in clients:
                usage.setdefault(key_id, 0)

            if all(usage[key_id] >= settings.daily_request_limit for key_id, _ in clients):
                print(
                    f"모든 인증키가 키당 일일 요청 안전 한도 "
                    f"{settings.daily_request_limit:,}회에 도달했습니다."
                )
                return 0

            for route_id in active_route_ids:
                available = [
                    (index, key_id, client)
                    for index, (key_id, client) in enumerate(clients)
                    if usage[key_id] < settings.daily_request_limit
                ]
                if not available:
                    print("모든 인증키의 오늘 위치 API 안전 한도를 사용했습니다.")
                    break
                _, key_id, client = min(
                    available,
                    key=lambda item: (usage[item[1]], item[0]),
                )
                # 성공 여부와 관계없이 외부 API 요청을 보낸 시점부터 사용량으로 계산합니다.
                usage[key_id] += 1
                try:
                    response = client.bus_locations(route_id)
                    header, locations = location_items(response.payload)
                    result_code = str(header.get("resultCode", ""))
                    error = None if result_code in {"0", ""} else header.get("resultMessage") or "API 오류"
                    storage.save_collection(
                        connection,
                        route_id=route_id,
                        api_key_id=key_id,
                        header=header,
                        locations=locations,
                        http_status=response.status_code,
                        elapsed_ms=response.elapsed_ms,
                        raw_body=response.raw_body,
                        store_raw=settings.store_raw_responses,
                        error=error,
                    )
                    if error:
                        failures += 1
                    else:
                        successes += 1
                        observations += len(locations)
                except ApiError as exc:
                    storage.save_collection(
                        connection,
                        route_id=route_id,
                        api_key_id=key_id,
                        header={},
                        locations=[],
                        http_status=exc.status_code,
                        elapsed_ms=exc.elapsed_ms,
                        raw_body=exc.raw_body,
                        store_raw=settings.store_raw_responses,
                        error=str(exc),
                    )
                    failures += 1
                    print(f"[{route_id}] {exc}", file=sys.stderr)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 완료: 노선 {successes}개, 차량 관측 {observations}건, 실패 {failures}건")
    return 0 if failures == 0 else 1


def search_route(keyword: str) -> int:
    settings = Settings.load(require_routes=False)
    response = _client(settings).search_routes(keyword)
    header, routes = route_items(response.payload)
    if str(header.get("resultCode", "")) not in {"0", ""}:
        raise ApiError(header.get("resultMessage") or "노선 검색에 실패했습니다.")
    if not routes:
        print("검색 결과가 없습니다.")
        return 0

    print("routeId\t노선명\t유형\t지역")
    for route in routes:
        print(
            "\t".join(
                str(route.get(key, ""))
                for key in ("routeId", "routeName", "routeTypeName", "regionName")
            )
        )
    return 0


def sync_metadata() -> int:
    settings = Settings.load(require_routes=True)
    storage = Storage(settings.db_path)
    clients = _clients(settings)
    active_route_ids = _active_route_ids(settings)
    failures = 0

    if not active_route_ids:
        print("현재 일일 안전 한도로 활성화할 수 있는 노선이 없습니다.", file=sys.stderr)
        return 1

    with singleton_lock(settings.db_path) as acquired:
        if not acquired:
            return 0
        with storage.connect() as connection:
            for index, route_id in enumerate(active_route_ids):
                _, client = clients[index % len(clients)]
                try:
                    response = client.route_stations(route_id)
                    header, stations = station_items(response.payload)
                    if str(header.get("resultCode", "")) not in {"0", ""}:
                        raise ApiError(header.get("resultMessage") or "정류장 동기화 실패")
                    count = storage.save_route_stations(connection, route_id, stations)
                    print(f"[{route_id}] 정류장 {count}개 동기화")
                except ApiError as exc:
                    failures += 1
                    print(f"[{route_id}] {exc}", file=sys.stderr)
    return 0 if failures == 0 else 1


def doctor() -> int:
    settings = Settings.load(require_routes=True)
    capacity = _route_capacity(settings)
    active_route_ids = _active_route_ids(settings)
    estimated = len(active_route_ids) * ESTIMATED_RUNS_PER_ROUTE
    total_limit = settings.total_daily_request_limit
    print(f"설정 파일: {settings.routes_file}")
    print(f"데이터베이스: {settings.db_path}")
    print(f"인증키: {len(settings.service_keys)}개")
    print(f"후보 노선: {len(settings.route_ids)}개")
    print(
        f"자동 활성 노선: {', '.join(active_route_ids) or '-'} "
        f"({len(active_route_ids)}개, 계산상 최대 {capacity}개)"
    )
    print(f"예상 일일 위치 API 호출: {estimated:,}회")
    print(
        f"코드상 일일 안전 한도: 키당 {settings.daily_request_limit:,}회, "
        f"총 {total_limit:,}회"
    )
    standby_count = len(settings.route_ids) - len(active_route_ids)
    if standby_count:
        print(f"대기 후보 노선: {standby_count}개 (키 또는 한도 추가 시 순서대로 자동 활성화)")
    if not active_route_ids:
        print(
            "경고: 활성 가능한 노선이 없습니다. 키를 추가하거나 cron 간격을 늘리세요.",
            file=sys.stderr,
        )
        return 1
    print("설정 검사가 통과했습니다.")
    return 0


def stats() -> int:
    settings = Settings.load(require_routes=False)
    storage = Storage(settings.db_path)
    with storage.connect() as connection:
        rows = storage.summary(connection)
        today = storage.requests_today(connection)
        usage = storage.requests_today_by_key(connection)
    print(f"데이터베이스: {settings.db_path}")
    print(f"오늘 위치 API 요청: {today:,}/{settings.total_daily_request_limit:,}")
    if len(settings.service_keys) > 1:
        legacy_usage = usage.pop(None, 0)
        for index, service_key in enumerate(settings.service_keys, 1):
            key_id = _key_id(service_key)
            used = usage.get(key_id, 0) + (legacy_usage if index == 1 else 0)
            print(f"  키 {index} ({key_id}): {used:,}/{settings.daily_request_limit:,}")
    if not rows:
        print("아직 수집된 데이터가 없습니다.")
        return 0
    print("routeId\t실행\t차량 관측\t실패\t마지막 수집(KST)")
    for row in rows:
        print(
            f"{row['route_id']}\t{row['runs']}\t{row['observations'] or 0}"
            f"\t{row['failures'] or 0}\t{row['last_collected_at_kst']}"
        )
    return 0


def serve_api() -> int:
    from .web_api import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="경기도 광역버스 잔여좌석 데이터 수집기")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="설정된 노선의 현재 차량 위치·잔여좌석 수집")
    search_parser = subparsers.add_parser("search-route", help="노선 번호로 routeId 검색")
    search_parser.add_argument("keyword", help="검색할 노선 번호 또는 이름")
    subparsers.add_parser("sync-metadata", help="설정된 노선의 정류장 목록 동기화")
    subparsers.add_parser("doctor", help="인증키·노선·예상 호출량 설정 검사")
    subparsers.add_parser("stats", help="수집 현황 요약")
    subparsers.add_parser("serve-api", help="수집 데이터 읽기 전용 HTTP API 실행")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect":
            return collect()
        if args.command == "search-route":
            return search_route(args.keyword)
        if args.command == "sync-metadata":
            return sync_metadata()
        if args.command == "doctor":
            return doctor()
        if args.command == "stats":
            return stats()
        if args.command == "serve-api":
            return serve_api()
    except (ConfigError, ApiError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    return 2
