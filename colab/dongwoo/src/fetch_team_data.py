"""
팀 내부 수집 서버(읽기 전용 API)에서 실제 관측 이력을 내려받아 CSV로 저장한다.

팀원 전원이 같은 원천을 쓰도록 되어 있으므로, 모델 비교의 공정성을 위해
개인 서비스키로 따로 수집하지 않고 이 서버 데이터를 기준으로 삼는다.

서버 앞단 Nginx에 요청 제한이 걸려 있어 동시 요청을 많이 보내면 429가 난다.
따라서 저동시성 + 429 지수 백오프 + 노선·날짜 단위 체크포인트로 동작하며,
중간에 끊겨도 다시 실행하면 남은 날짜만 이어받는다.

  export GBIS_API_KEY="..."          # 팀 내부 API 키 (공공데이터포털 키가 아님)
  python3 fetch_team_data.py --days 20
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PART_DIR = DATA_DIR / "team_parts"
DATA_DIR.mkdir(exist_ok=True)
PART_DIR.mkdir(exist_ok=True)

BASE_URL = os.environ.get("GBIS_API_BASE_URL", "https://161.33.212.6")
API_KEY = os.environ.get("GBIS_API_KEY", "")
PAGE_SIZE = 500
WORKERS = 2
MIN_INTERVAL = 0.15  # 전역 최소 요청 간격(초)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_throttle = threading.Lock()
_last_call = [0.0]


def _get(path: str, params: dict | None = None, retries: int = 8) -> dict:
    url = f"{BASE_URL}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    last = None
    for attempt in range(retries):
        with _throttle:  # 전역 스로틀: 요청 사이 최소 간격 보장
            wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                time.sleep(min(60, 2.0 * (2 ** attempt)))  # 지수 백오프
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"GET {path} 실패: {last}")


def active_routes(min_observations: int = 10_000) -> list[str]:
    """아직 수집이 진행 중이고 관측량이 충분한 노선만 고른다."""
    items = _get("/v1/routes")["items"]
    latest_day = max(i["last_collected_at"] for i in items)[:10]
    return [
        i["route_id"] for i in items
        if i["observation_count"] >= min_observations
        and i["last_collected_at"][:10] == latest_day
    ]


def fetch_stations(route_id: str) -> list[dict]:
    try:
        return _get(f"/v1/routes/{route_id}/stations")["items"]
    except (RuntimeError, urllib.error.HTTPError):
        return []


def fetch_day(route_id: str, day: datetime) -> list[dict]:
    rows, cursor = [], None
    day_end = day + timedelta(days=1)
    while True:
        params = {
            "route_id": route_id,
            "from": day.isoformat(timespec="seconds"),
            "to": day_end.isoformat(timespec="seconds"),
            "limit": PAGE_SIZE,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _get("/v1/locations", params)
        rows.extend(payload.get("items", []))
        cursor = payload.get("next_cursor")
        if not cursor:
            return rows


def fetch_route(route_id: str, start: datetime, end: datetime) -> None:
    """노선의 각 날짜를 개별 파일로 저장한다. 이미 있는 날짜는 건너뛴다."""
    import pandas as pd

    day = start
    while day < end:
        # 오늘(진행 중인 날)은 계속 늘어나므로 매번 다시 받는다.
        is_today = day.date() == datetime.now(KST).date()
        part = PART_DIR / f"{route_id}_{day.date()}.csv"
        if part.exists() and not is_today:
            day += timedelta(days=1)
            continue
        rows = fetch_day(route_id, day)
        if rows:
            pd.DataFrame(rows).to_csv(part, index=False)
            print(f"  [{route_id}] {day.date()} {len(rows):,}행 저장", flush=True)
        else:
            part.write_text("")  # 빈 날도 표시해 재요청을 막는다
        day += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--routes", default="")
    args = ap.parse_args()

    if not API_KEY:
        raise SystemExit("환경변수 GBIS_API_KEY 를 설정하세요 (팀 내부 API 키).")

    import pandas as pd

    routes = args.routes.split(",") if args.routes else active_routes()
    print(f"대상 노선 {len(routes)}개: {routes}")

    end = datetime.now(KST)
    start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)

    st_path = DATA_DIR / "team_stations.csv"
    if not st_path.exists():
        station_rows = []
        for rid in routes:
            items = fetch_stations(rid)
            station_rows.extend(items)
            print(f"[stations] {rid}: {len(items)}개")
        pd.DataFrame(station_rows).to_csv(st_path, index=False)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_route, rid, start, end): rid for rid in routes}
        for fut in cf.as_completed(futures):
            rid = futures[fut]
            try:
                fut.result()
                print(f"[history] {rid} 완료")
            except Exception as exc:
                print(f"[history] {rid} 실패: {exc}")

    # 조각 파일 병합
    frames = []
    for p in sorted(PART_DIR.glob("*.csv")):
        if p.stat().st_size == 0:
            continue
        frames.append(pd.read_csv(p, dtype={"route_id": str, "vehicle_id": str,
                                            "station_id": str, "plate_no": str}))
    df = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["route_id", "vehicle_id", "observed_at"])
    out = DATA_DIR / "team_history.csv"
    df.to_csv(out, index=False)
    print(f"\nsaved -> {out}  ({len(df):,}행, {df['route_id'].nunique()}노선)")
    print(df["observed_at"].min(), "~", df["observed_at"].max())


if __name__ == "__main__":
    main()
