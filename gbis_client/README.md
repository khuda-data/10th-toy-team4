# GBIS API 캐시 클라이언트

Oracle 서버의 읽기 전용 GBIS API를 호출하고, 응답을 로컬 SQLite에 캐시한 뒤 pandas
DataFrame으로 읽는 클라이언트입니다. API 키 원문은 캐시 DB에 저장하지 않습니다.

## 설치와 설정

저장소 루트에서 의존성을 설치합니다.

```sh
python3 -m pip install -r requirements-client.txt
```

저장소 루트의 `.env`에 접속 정보를 넣습니다.

```dotenv
GBIS_API_BASE_URL=https://161.33.212.6
GBIS_API_KEY=발급받은팀원키
GBIS_API_CACHE_PATH=data/gbis_api_cache.sqlite3
```

```python
from gbis_client import GBISApiCache

with GBISApiCache.from_env() as cache:
    cache.refresh_all()
    cache.refresh_full_history("233000031")

    routes_df = cache.routes_df()
    stations_df = cache.stations_df("233000031")
    latest_df = cache.latest_locations_df("233000031")
    history_df = cache.history_df("233000031")
```

`refresh_full_history()`는 최초 호출 시 서버의 전체 이력을 최대 30일 단위로 나눠 복사하고,
이후에는 캐시에 기록된 마지막 성공 시각부터 증분 갱신합니다.

## 주요 함수

- `refresh_routes()`, `routes_df()`
- `refresh_stations(route_id)`, `stations_df(route_id)`
- `refresh_latest(route_id=None)`, `latest_locations_df(route_id=None)`
- `refresh_history(route_id, from_at=None, to_at=None)`, `history_df(...)`
- `refresh_full_history(route_id)`: 최초 전체 복사, 이후 증분 갱신
- `refresh_all()`: 노선, 정류장, 최신 위치 갱신
- `cache_status_df()`: 캐시 항목별 마지막 갱신 완료 시각

## DataFrame 컬럼

### `routes_df`

| 컬럼 | 설명 |
| --- | --- |
| `route_id` | GBIS 내부 노선 ID |
| `station_count` | 해당 노선의 정류장 수 |
| `observation_count` | 서버에 누적된 차량 위치 관측 건수 |
| `first_collected_at` | 서버가 해당 노선을 처음 수집한 시각 |
| `last_collected_at` | 서버가 해당 노선을 마지막으로 수집한 시각 |
| `cached_at_utc` | 이 행을 로컬 캐시에 저장한 UTC 시각 |

### `stations_df`

| 컬럼 | 설명 |
| --- | --- |
| `route_id` | GBIS 내부 노선 ID |
| `station_id` | GBIS 내부 정류장 ID |
| `station_seq` | 노선 내 정류장 순번 |
| `station_name` | 정류장 이름 |
| `mobile_no` | 정류장 표지판의 단축 번호 |
| `region_name` | 정류장이 속한 지역 이름 |
| `x`, `y` | 정류장 경도와 위도 |
| `center_yn` | GBIS 원본의 센터 관리 여부 값 |
| `synced_at_kst` | 서버가 정류장 정보를 동기화한 한국 시각 |
| `cached_at_utc` | 이 행을 로컬 캐시에 저장한 UTC 시각 |

### `latest_locations_df`, `history_df`

두 DataFrame은 같은 관측 컬럼을 사용합니다. `latest_locations_df`는 차량별 최신 스냅샷이고,
`history_df`는 시간에 따라 누적된 관측 이력입니다.

| 컬럼 | 설명 |
| --- | --- |
| `observed_at` | 수집기가 차량 위치를 관측한 시각 |
| `query_time` | 경기도 버스 API 응답의 조회 시각 |
| `route_id` | GBIS 내부 노선 ID |
| `vehicle_id` | 차량을 구분하는 GBIS 내부 ID |
| `plate_no` | 차량 번호판 문자열 |
| `route_type_code` | 노선 종류를 나타내는 GBIS 원본 코드 |
| `station_id` | 관측 시점 차량이 위치한 정류장 ID |
| `station_seq` | 관측 시점 차량이 위치한 정류장 순번 |
| `station_name` | 정류장 이름 |
| `remaining_seats` | 차량의 잔여좌석 수 |
| `crowded` | 혼잡 상태를 나타내는 GBIS 원본 코드 |
| `low_plate` | 저상버스 여부를 나타내는 GBIS 원본 코드 |
| `state_code` | 차량 운행·정차 상태를 나타내는 GBIS 원본 코드 |
| `tagless_code` | 태그리스 관련 상태를 나타내는 GBIS 원본 코드 |
| `cached_at_utc` | 이 관측을 로컬 캐시에 저장한 UTC 시각 |

시간 컬럼은 SQLite에서 문자열로 읽힙니다. datetime 연산이 필요하면
`pd.to_datetime(df["observed_at"])`로 변환합니다.
