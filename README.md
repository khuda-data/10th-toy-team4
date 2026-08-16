# 10th-toy-team4
<<<<<<< HEAD
4조 화이팅
=======
4조 화이팅

# 광역버스 도착 잔여좌석 예측 프로젝트

출퇴근 시간대 광역버스가 목표 정류장에 도착할 때, 승객을 태우기 전 잔여좌석 수를 예측하는
프로젝트입니다.
현재 저장소에는 연구에 필요한 경기도 광역버스 차량 위치·잔여좌석 데이터 수집기가 구현되어
있습니다.

## 문제 정의

광역·직행좌석버스는 입석을 허용하지 않으므로 좌석이 모두 차면 정류장에 대기 승객이 있어도
추가로 태우지 않습니다. 기존 경로 안내는 주로 버스 도착시간과 예상 소요시간을 제공하지만,
도착한 버스에 타지 못해 몇 대를 더 기다릴 수 있는지는 충분히 설명하지 않습니다. 이 때문에
이용자는 전날 확인한 예상시간보다 훨씬 늦게 목적지에 도착할 수 있습니다.

이 프로젝트는 다음 두 문제를 구분해 다룹니다.

1. **도착 잔여좌석 예측**: 특정 노선의 차량이 목표 정류장에서 승객을 태우기 직전의
   `arrival_seats`와 예측구간을 회귀로 예측합니다.
2. **승객 승차 예측**: 차량의 빈자리뿐 아니라 정류장 대기열까지 고려해 이용자가 첫 번째,
   두 번째 또는 그 이후 차량에 탈 확률과 추가 대기시간을 예측합니다.

`버스가 만차일 확률`과 `내가 탈 수 있을 확률`은 같지 않습니다. 잔여좌석이 있어도 앞에
기다리는 승객이 더 많으면 탈 수 없고, 반대로 만차 버스가 정류장을 통과했더라도 대기 승객이
없었다면 승차 실패가 발생한 것은 아닙니다. 따라서 최종 모델에는 차량별 잔여좌석 예측과
정류장별 대기수요 추정이 모두 필요합니다.

## 연구 주제

> **경기도 광역좌석버스의 정류장별 승차 실패확률과 탑승대기 차량 수 예측**

주요 연구 질문은 다음과 같습니다.

1. 시간대, 노선, 정류장, 차량 종류, 날씨와 운행정보로 목표 정류장의 도착 잔여좌석을 얼마나
   정확하게 예측할 수 있는가?
2. 정류장 대기열과 차량별 잔여좌석을 결합하면 탑승까지 보내야 할 차량 수를 확률분포로
   예측할 수 있는가?
3. 전날 계획용 예측, 출발 직전 예측, 실시간 예측은 정확도와 활용성에서 어떤 차이가 있는가?

예상 결과는 하나의 숫자보다 다음처럼 불확실성을 포함해 제공하는 것을 목표로 합니다.

```text
07:40 정류장 도착 기준
- 첫 번째 버스 탑승 가능: 38%
- 두 번째 버스까지 탑승 가능: 79%
- 세 번째 버스까지 탑승 가능: 94%
- 예상 추가 대기시간: 11분
- 90% 확률로 22분 이내 탑승
```

### 예측 특성 후보

- 시간: 시각, 요일, 공휴일, 출퇴근 시간, 학기·방학
- 노선·정류장: 노선 ID, 진행 방향, 정류장 ID와 순번, 기점으로부터 거리, 환승 거점 여부
- 차량·운행: 차량 종류와 좌석 수, 현재 잔여좌석, 앞차와의 간격, 도착 지연, 상류
  정류장의 승하차 변화
- 외부 요인: 기온, 강수, 적설, 풍속, 도로 정체와 행사
- 대기수요: 직전 차량에서 남겨진 승객과 다음 차량 도착 전 새로 유입되는 승객

## 선행 연구

선행 연구는 차량 혼잡도 예측, 만차로 인해 남겨진 승객 추정, 혼잡정보에 따른 승객 행동의
세 갈래로 나뉩니다.

| 연구 | 데이터·방법 | 프로젝트에 주는 시사점 |
| --- | --- | --- |
| 이승찬·최지혁(2017), [버스 차내 혼잡도 예측치 제공 방법론에 관한 연구](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE07626466) | 경기도 BIS의 정류장·시간대별 승하차 자료와 몬테카를로 시뮬레이션으로 경기도 1000번 버스의 재차인원 범위 예측 | 연구 대상과 문제의식이 가장 가깝지만 개별 승객의 승차 성공확률과 보내야 할 차량 수까지는 직접 예측하지 않음 |
| 정양록·배상훈(2015), [부산광역시 교통카드데이터를 활용한 버스 내 재차인원 추정 및 혼잡도 표출](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11686519) | 승차 데이터와 현장조사로 얻은 하차 비율을 결합해 재차인원과 3단계 혼잡도 추정 | 하차 데이터나 자동승객계수 장비가 부족할 때 재차인원을 복원하는 방법을 제시 |
| Wood, Yu & Gayah(2023), [Development and evaluation of frameworks for real-time bus passenger occupancy prediction](https://doaj.org/article/4b280ad3c60e4e7fa38fe2b9d75fa4cc) | APC·운행·날씨 자료에 선형회귀와 Random Forest 적용 | 현재 위치와 미래 정류장 쌍을 함께 모델링하면 하류 정류장 예측이 개선됨 |
| Jenelius(2019), [Data-Driven Bus Crowding Prediction Based on Real-Time Passenger Counts and Vehicle Locations](https://www.diva-portal.org/smash/get/diva2%3A1786851/FULLTEXT01.pdf) | 스톡홀름 AVL·APC 자료에 Lasso 회귀 적용 | 과거 평균만 사용할 때보다 실시간 차량 위치와 승객 수를 사용할 때 예측력이 향상됨 |
| Wang et al.(2021), [A two-stage method for bus passenger load prediction using automatic passenger counting data](https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/itr2.12018) | Kalman Filter로 정류장 승하차 흐름을 예측한 뒤 SVR로 재차인원 예측 | 승하차 수요와 차량 재차인원을 분리한 2단계 모델 구조를 참고할 수 있음 |
| Gallo, Corman & Sacco(2022), [Real-time occupancy predictions of public transport vehicles](https://www.research-collection.ethz.ch/entities/publication/9ee774f9-9c41-4fca-a7df-214c5317c999) | 취리히 전 노선의 날씨·지연·환승·노선 중첩 자료에 LightGBM 적용 | 실시간 재차인원이 최대 8~9개 정류장 앞 예측을 개선했으며 환승 거점 이후 불확실성이 커짐 |
| Talusan et al.(2022), [On Designing Day Ahead and Same Day Ridership Level Prediction Models](https://arxiv.org/abs/2210.04989) | 교통·날씨·달력 자료 1,700만 건에 XGBoost와 LSTM 적용 | 전날 계획용 예측과 당일 실시간 예측을 구분해 비교할 근거를 제공 |
| Ma et al.(2022), [Excess demand in public transportation systems](https://arxiv.org/abs/2208.06372) | 만차 구간의 잘린 수요를 식별한 뒤 포아송 회귀로 잠재수요 추정 | 만차 버스에서 승차자가 0명인 기록을 수요 0명으로 학습하면 출퇴근 수요가 과소추정되는 검열 문제를 지적 |
| Zhu, Koutsopoulos & Wilson(2017), [Inferring Left Behind Passengers in Congested Metro Systems from Automated Data](https://www.sciencedirect.com/science/article/pii/S2352146517302995) | MLE와 베이지안 추론으로 승객이 열차를 보내는 횟수의 확률질량함수 추정 | 보내야 하는 차량 수를 단일 예측값이 아니라 확률분포로 표현하는 방법을 제시 |
| Miller, Sánchez-Martínez & Nassir(2018), [Estimation of Passengers Left Behind by Trains](https://journals.sagepub.com/doi/abs/10.1177/0361198118794291) | 승객 유입량과 출발시각으로 누적 수송력 부족량을 만들고 남겨진 승객 수 추정 | 앞 차량의 좌석 부족이 다음 차량 대기열로 누적되는 현상을 모델링 |
| Kim, Lee & Oh(2009), [Passenger Choice Models for Analysis of Impacts of Real-Time Bus Information on Crowdedness](https://journals.sagepub.com/doi/10.3141/2112-15) | 서울 수도권 이용자 설문을 이항 로짓으로 분석 | 좌석 유무·혼잡도·대기시간 정보가 첫차와 다음 차 사이의 선택에 영향을 줌 |

### 기존 서비스와 차별점

국민대학교의 2023년 캡스톤 프로젝트 [자리있어?](https://kookmin-sw.github.io/capstone-2023-29/)는
경기도 버스 API를 수집해 광역버스 잔여좌석을 예측했습니다. 이 프로젝트는 도착과 출발 좌석을
구분하고, 잔여좌석 회귀를 승객 관점의 승차 가능성으로 확장하는 것을 연구 기여로 삼습니다.

- 승객 탑승 전 `arrival_seats`와 예측구간 제공
- 잔여좌석과 정류장 대기열을 결합한 **승객 관점의 승차 실패확률** 제공
- 첫 번째·두 번째·세 번째 차량별 탑승확률과 누적 탑승확률 제공
- 만차 이후 다음 차량으로 이월되는 정류장 대기열 모델링
- 예상 대기 차량 수와 추가 대기시간의 예측구간 제공
- 전날 예측과 실시간 예측의 성능 및 활용성 비교

## 저장소 구성

- `collector/`: 데이터 수집기, 읽기 전용 API, 설정, 운영 스크립트와 테스트
- `analysis/`: 모델 가능성 분석과 결과
- `data/`: 수집기와 분석 코드가 공유하는 로컬 데이터
- `docs/`: 모델·분석 보고서

수집기의 상세 실행 방법은 [수집기 README](collector/README.md)를 참고하세요.

## 현재 구현: 잔여좌석 데이터 수집기

경기도 버스정보 Open API의 실시간 차량 위치와 잔여좌석을 SQLite에 누적합니다. 노선별 위치
API를 사용하므로 한 번의 요청으로 해당 노선에서 운행 중인 모든 차량을 수집합니다.

## 수집 항목

- 수집 시각과 API 제공 시각
- 노선·차량 ID와 차량번호
- 현재 정류장 ID·정류장 순번
- 잔여좌석 수와 혼잡도
- 일반·저상·2층·전세·예약버스 구분
- 정류장 도착·출발 상태
- 압축한 원본 API 응답

## 1. 초기 설정

공공데이터포털에서 다음 API의 활용신청을 하고 인증키를 발급받습니다.

- 경기도 버스위치정보 조회
- 경기도 버스노선 조회

설정 파일을 만듭니다.

```sh
cp collector/.env.example .env
cp collector/config/routes.txt.example collector/config/routes.txt
```

`.env`의 `GBIS_SERVICE_KEYS`에 인증키를 쉼표로 구분해 넣습니다. 일반 인증키(Decoding)와
URL 인코딩 인증키(Encoding)를 모두 사용할 수 있습니다.

```dotenv
GBIS_SERVICE_KEYS=첫번째키,두번째키,세번째키
```

인증키가 하나뿐이면 기존 `GBIS_SERVICE_KEY=인증키` 형식도 계속 사용할 수 있습니다. 여러
키를 설정하면 위치 API 요청과 정류장 메타데이터 요청을 키마다 균등하게 분배합니다. 키 원문은
DB나 로그에 저장하지 않으며, `stats`에는 키를 구분하기 위한 해시 식별자만 표시합니다.

## 2. 수집할 노선 ID 찾기

화면에 표시되는 노선 번호와 API의 `routeId`는 다릅니다. 다음 명령으로 찾습니다.

```sh
cd collector
../venv/bin/python -m gbis_collector search-route 1000
```

검색 결과에서 필요한 `routeId`를 `collector/config/routes.txt`에 우선순위가 높은 순서로 한 줄에 하나씩
입력합니다. 이 파일은 전부 수집하는 목록이 아니라 후보 풀입니다. 수집기는 현재 키 개수와
키당 안전 한도로 감당할 수 있는 만큼만 위에서부터 자동 활성화합니다.

## 3. 수동 검증

```sh
cd collector
../venv/bin/python -m gbis_collector doctor
../venv/bin/python -m gbis_collector sync-metadata
../venv/bin/python -m gbis_collector collect
../venv/bin/python -m gbis_collector stats
```

`doctor`는 `GBIS_DAILY_REQUEST_LIMIT`을 키 하나당 한도로 해석하고, 현재 cron의 노선당 예상
816회를 기준으로 활성 노선 수를 자동 계산합니다. 기본 안전 한도 1,000회에서는 키 1개면 후보
1개, 키 2개면 후보 2개가 활성화됩니다. 키를 추가하거나 제거하면 다음 실행부터 활성 목록도
자동으로 바뀝니다. 같은 공공데이터포털 계정에서 발급된 키들이 실제 트래픽 한도를 공유하는지는
각 활용신청 상세 화면에서 확인해야 합니다.

데이터는 기본적으로 `data/gbis.sqlite3`, 실행 로그는 `data/logs/collector.log`에 저장됩니다.

## 4. 자동 수집 설정

cron을 이용한 주기적 수집 방법과 호출량 계산은 [cron 운영 가이드](collector/docs/cron.md)를
참고하세요.

## 데이터 확인

```sh
cd collector
../venv/bin/python -m gbis_collector stats
sqlite3 ../data/gbis.sqlite3 \
  "SELECT observed_at_kst, route_id, vehicle_id, station_seq, remaining_seats FROM bus_locations ORDER BY observed_at_kst DESC LIMIT 20;"
```

주요 테이블은 다음과 같습니다.

- `collection_runs`: API 요청별 실행 상태와 압축 원본 응답
- `bus_locations`: 차량별 정류장 위치와 잔여좌석 관측값
- `route_stations`: 노선별 정류장 순서·이름·좌표

## 읽기 전용 API

Oracle VM 등 수집 서버의 SQLite 데이터를 팀원이 조회할 수 있도록 Bearer API 키 인증을 사용하는
읽기 전용 HTTP API를 제공합니다.

```sh
venv/bin/python -m pip install -r collector/requirements.txt
venv/bin/python -c "import secrets; print('gbis_' + secrets.token_urlsafe(32))"
```

출력된 키를 `.env`의 `GBIS_API_KEYS`에 입력합니다. 팀원별 키가 여러 개면 쉼표로 구분합니다.

```dotenv
GBIS_API_KEYS=첫번째팀원키,두번째팀원키
GBIS_API_HOST=127.0.0.1
GBIS_API_PORT=8000
```

API 서버를 실행합니다. 기본값인 `127.0.0.1`은 Nginx 같은 HTTPS 프록시 뒤에서 실행하기 위한
안전한 설정입니다.

```sh
cd collector
../venv/bin/python -m gbis_collector serve-api
```

로컬에서는 다음처럼 확인할 수 있습니다.

```sh
GBIS_TEAM_API_KEY=발급받은팀원키
curl http://127.0.0.1:8000/healthz
curl -H "Authorization: Bearer $GBIS_TEAM_API_KEY" \
  http://127.0.0.1:8000/v1/routes
curl -H "Authorization: Bearer $GBIS_TEAM_API_KEY" \
  "http://127.0.0.1:8000/v1/locations/latest?route_id=233000031"
```

주요 엔드포인트는 다음과 같습니다.

- `GET /healthz`: 공개 생존 확인
- `GET /v1/health`: 마지막 수집 시각과 누적 수집 현황
- `GET /v1/routes`: 수집 노선 목록
- `GET /v1/routes/{route_id}/stations`: 노선 정류장 목록
- `GET /v1/locations/latest`: 노선별 최신 운행 차량
- `GET /v1/locations`: 기간별 관측 이력과 cursor 페이지네이션

Swagger 문서는 로컬 서버의 `http://127.0.0.1:8000/docs`에서 확인할 수 있습니다. 운영 환경에서는
API 서버 포트를 직접 공개하지 말고 HTTPS 프록시를 통해서만 접근시켜야 합니다.

### 로컬 API 캐시와 DataFrame

팀원 PC에서는 `gbis_client.GBISApiCache`로 서버 API를 호출하고, 응답을 별도 SQLite 파일에
캐시한 뒤 pandas DataFrame으로 읽을 수 있습니다. API 키 원문은 캐시 DB에 저장하지 않습니다.

```sh
python3 -m pip install -r requirements-client.txt
```

저장소 루트의 `.env`에 서버 주소와 팀원 개인 키를 추가합니다. `.env`는 Git에서 제외되어
있으므로 키가 저장소에 커밋되지 않습니다.

```dotenv
GBIS_API_BASE_URL=https://161.33.212.6
GBIS_API_KEY=발급받은팀원키
GBIS_API_CACHE_PATH=data/gbis_api_cache.sqlite3
```

이미 셸 환경변수로 같은 값을 설정했다면 환경변수가 `.env`보다 우선합니다. 다른 설정 파일을
쓰려면 `GBISApiCache.from_env(env_file="경로/.env")`처럼 지정할 수 있습니다.

```python
from gbis_client import GBISApiCache

with GBISApiCache.from_env() as cache:
    # 노선·정류장 메타데이터와 현재 운행 차량을 최신 상태로 교체합니다.
    print(cache.refresh_all())
    latest = cache.latest_locations_df()

    # 첫 호출은 최근 1일, 이후 호출은 캐시의 마지막 시각부터 증분 갱신합니다.
    cache.refresh_history("233000031")
    history = cache.history_df("233000031")
```

기본 캐시 경로는 `data/gbis_api_cache.sqlite3`이며 `GBIS_API_CACHE_PATH`로 변경할 수 있습니다.
주요 함수는 다음과 같습니다.

- `refresh_routes()`, `routes_df()`
- `refresh_stations(route_id)`, `stations_df(route_id)`
- `refresh_latest(route_id=None)`, `latest_locations_df(route_id=None)`
- `refresh_history(route_id, from_at=None, to_at=None)`, `history_df(...)`
- `refresh_full_history(route_id)`: 최초에는 전체 이력을 30일 단위로, 이후에는 증분 갱신
- `refresh_all()`: 노선·정류장·최신 위치를 한 번에 갱신

#### 비개발자 팀원용 Google Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khuda-data/10th-toy-team4/blob/main/colab/gbis_data_quickstart.ipynb)

비개발자 팀원은 [Colab 사용 안내](colab/README.md)에 따라 개인 API 키를 Colab Secrets에 한 번
등록하고 **런타임 → 모두 실행**만 누르면 됩니다. 노트북은 Google Drive 캐시를 `/content`로
복원하고, 최초에는 전체 이력을, 이후에는 마지막 성공 시각 이후의 데이터만 동기화한 다음
`routes_df`, `stations_df`, `latest_df`, `history_df`를 만들어 줍니다.

#### `routes_df()` 컬럼

| 컬럼 | 설명 |
| --- | --- |
| `route_id` | GBIS 내부 노선 ID입니다. 화면에 표시되는 노선 번호와 다를 수 있습니다. |
| `station_count` | 캐시된 해당 노선의 정류장 수입니다. |
| `observation_count` | 서버 DB에 누적된 해당 노선의 차량 위치 관측 건수입니다. |
| `first_collected_at` | 서버가 해당 노선을 처음 수집한 시각입니다. 최초 전체 복사의 시작점입니다. |
| `last_collected_at` | 서버가 해당 노선을 마지막으로 수집한 시각입니다. |
| `cached_at_utc` | 이 행을 로컬 API 캐시에 저장한 UTC 시각입니다. |

#### `stations_df()` 컬럼

| 컬럼 | 설명 |
| --- | --- |
| `route_id` | GBIS 내부 노선 ID입니다. |
| `station_id` | GBIS 내부 정류장 ID입니다. |
| `station_seq` | 노선 내 정류장 순번입니다. 진행 방향을 구분할 때 사용합니다. |
| `station_name` | 정류장 이름입니다. |
| `mobile_no` | 정류장 표지판에 표시되는 단축 번호입니다. |
| `region_name` | 정류장이 속한 지역 이름입니다. |
| `x`, `y` | 정류장 경도와 위도입니다. |
| `center_yn` | GBIS 원본의 센터 관리 여부 값(`Y`/`N`)입니다. |
| `synced_at_kst` | 서버가 정류장 메타데이터를 동기화한 한국 시각입니다. |
| `cached_at_utc` | 이 행을 로컬 API 캐시에 저장한 UTC 시각입니다. |

#### `latest_locations_df()`와 `history_df()` 컬럼

두 DataFrame은 같은 관측 컬럼을 사용합니다. `latest_locations_df()`는 차량별 최신 스냅샷이고,
`history_df()`는 시간에 따라 누적된 관측 이력입니다.

| 컬럼 | 설명 |
| --- | --- |
| `observed_at` | 수집기가 차량 위치를 관측한 한국 시각입니다. |
| `query_time` | 경기도 버스 API가 응답에 포함한 조회 시각입니다. |
| `route_id` | GBIS 내부 노선 ID입니다. |
| `vehicle_id` | 차량을 구분하는 GBIS 내부 ID입니다. |
| `plate_no` | 차량 번호판 문자열입니다. |
| `route_type_code` | 노선 종류를 나타내는 GBIS 원본 코드입니다. |
| `station_id` | 관측 시점 차량이 위치한 정류장 ID입니다. |
| `station_seq` | 관측 시점 차량이 위치한 노선 내 정류장 순번입니다. |
| `station_name` | `station_seq`에 대응하는 정류장 이름입니다. |
| `remaining_seats` | API가 제공한 차량의 잔여좌석 수입니다. |
| `crowded` | 혼잡 상태를 나타내는 GBIS 원본 코드입니다. |
| `low_plate` | 저상버스 여부를 나타내는 GBIS 원본 코드입니다. |
| `state_code` | 차량 운행·정차 상태를 나타내는 GBIS 원본 코드입니다. |
| `tagless_code` | 태그리스 관련 상태를 나타내는 GBIS 원본 코드입니다. |
| `cached_at_utc` | 이 관측을 로컬 API 캐시에 저장한 UTC 시각입니다. |

코드형 컬럼은 의미를 임의로 변환하지 않고 원본 정수값을 보존합니다. 시간 컬럼은 SQLite에서
문자열로 읽히므로 datetime 연산이 필요하면 `pd.to_datetime(df["observed_at"])`로 변환합니다.

#### `cache_status_df()` 컬럼

| 컬럼 | 설명 |
| --- | --- |
| `resource` | 마지막 갱신 대상을 나타냅니다. 예: `routes`, `latest:all`, `history:233000031`. |
| `refreshed_at_utc` | 해당 대상의 마지막 성공적인 갱신 완료 UTC 시각입니다. |

## 도착 잔여좌석 회귀 분석

현재 수집분에서 승객 탑승 전 `arrival_seats` 라벨을 재구성하고 시간 외 검증한 결과와 한계는
[도착 좌석 회귀 보고서](docs/arrival_seat_regression_report.md)에 정리되어 있습니다.
분위수·좌석 구간 확률과 동적 파생 피처 실험은
[확률분포 모델 보고서](docs/probabilistic_seat_model_report.md)에서 확인할 수 있습니다.
날짜 순서 교차검증을 사용한 모델·파라미터·앙상블 탐색과 이전 버스 출발좌석 ablation은
[모델 개선 실험 보고서](docs/model_improvement_experiments.md)에 정리되어 있습니다.

이전 운영 모델 탐색은 2026-08-10까지를 모델 선택에 사용하고 2026-08-11 오전을 잠금 확인셋으로
평가했습니다. 스냅샷보다 앞선 동일 목표 정류장·방향의 최신 관측 출발 잔여좌석은 엄격한 as-of
규칙으로 로컬 캐시에 보존합니다. 이는 물리적으로 바로 앞선 한 대로 고정되는 값이 아니며,
예측 중 새 출발이 관측되면 참조가 갱신됩니다. 적재율·정원 일치·freshness·최근 3대 통계까지
정규화해 비교한 HGB ablation의 rolling OOF 사건 균형 MAE는 기본 2.170254석에 비해 절대
이전 출발좌석 2.196186석, 정규화 좌석 2.227396석, 전체 파생 피처 2.230241석으로 모두
악화되었습니다. 따라서 이전 버스 출발좌석은 로컬 캐시와 진단 열에는 유지하지만 현재 점
모델에는 넣지 않습니다.

기존 1000번 단일 노선 점 모델 **`arrival-seat-1000/v1.0.0`**은 과거 이력 모델입니다. 기존 31개 피처에 과거 정류장·시간대
저잔여율과 현재 구간의 좌석 변화 프로파일 9개를 더한 40피처 모델이며, HGB 40% + 48-tree
ExtraTrees 40% + sqrt-gap LightGBM 20% 앙상블입니다. 최신 완전 수집일인 8월 11·12일에서
전체 MAE는 2.254625석, 저잔여≤10 MAE는 3.433123석이고 만차 Accuracy/Recall/Precision/F1은
각각 0.985294/0.297269/0.638527/0.405675입니다. 기존 31피처 모델과 비교하면 전체 MAE는
통계적 동률이지만 저잔여 MAE는 0.348622석, 만차 F1은 0.027693 개선됐습니다.

현재 프로젝트 주 모델은 **`arrival-seat-pooled/v1.0.0`**입니다. 1000·1100·1200·1500·2000·
3000번 전체 활성 노선을 통합하고, 37개 core 피처와 범주형 `route_code`를 사용합니다. 좌표
`x`, `y`는 유지하지만 1000번에서 만든 44/70석 출력 보정과 `observed_ceiling_*` 3개 피처,
학습된 출력 offset은 사용하지 않습니다. 명목 정원 범위 제한만 예측값의 물리적 support로
적용합니다.

원천 데이터는 2026-08-13 20:26:03+09:00까지 동기화했고, 부분일인 8월 13일을 제외한 마지막
완전일 8월 12일까지 587,600행·32,002 도착 사건으로 최종 학습했습니다. 완전일 rolling-origin
선택 지표는 전체 MAE 2.689719석, 저잔여≤10 MAE 4.039419석이며 만차 Accuracy/Recall/
Precision/F1은 0.991786/0.340160/0.696591/0.457106입니다. 발행 artifact의 8월 13일 20:26
부분일 smoke 지표는 전체 MAE 2.551832석, 저잔여≤10 MAE 3.669368석, 만차 Accuracy/Recall/
Precision/F1 0.992082/0.506701/0.649002/0.569091입니다.

Primary alias와 immutable artifact는 `analysis/model_registry/arrival-seat-pooled/`에 저장합니다.
선택·아티팩트 승격은 완료했지만 최종 artifact가 2,160,124 tree node로 100만 node 운영 상한을
넘고 통합 예측구간 정책도 재보정 전입니다. 따라서 배포 상태는
`blocked_node_budget_and_distribution_recalibration`이며 기존 서비스 artifact는 자동으로
교체하지 않습니다. 버전 규칙과 선택·배포 상태의 구분은
[주 모델 버전 관리 문서](docs/model_versioning.md), 피처 선택 근거는
[전체 통합 상한·좌표 피처 제거 보고서](docs/pooled_main_model_overfit_ablation_report.md)를
참고하세요.

기존 운영 점 모델에 보정된 계층 노선 분포는 점 예측 위에 90% 예측구간과
`P(arrival_seats≤5)`를 산출합니다. 재생성된 `route_scale_q950` + one-sided q95 하방 보정은
순차 OOF에서 coverage 93.287%, 평균 폭 10.420석, weighted interval score 14.003을
기록했고, 저잔여 coverage는 85.531%입니다. 8월 11일 오전 확인셋에서는 전체 coverage
90.983%, 저잔여 coverage 81.566%, weighted interval score 17.467이었습니다. 하방 보정은
점·상한·확률을 바꾸지 않으며 원래 하한도 감사 열로 함께 제공합니다. 저잔여 확률의 온도보정은
log loss를 줄였지만 Brier 개선은 아직 불확실해 원시 확률을 기본값으로 유지하고 보정 확률은
`shadow` 열로 함께 출력합니다.

엄격한 과거 OOF로 학습한 `meta_hgb_regularized_disagreement_residual`은 개발 OOF MAE
2.116161석과 service score 2.336841로 점 모델보다 개선되었습니다. 그러나 미개봉이 아닌
8월 11일 확인셋에서 전체 MAE 개선의 trip bootstrap 구간이 0을 포함했고
저잔여·emerging-low·p90·within-3 보호지표도 악화되어 확인 gate를 통과하지 못했습니다.
따라서 기본 서비스로 승격하지 않고
`analysis/strict_meta_stack_results/`의 고정 artifact를 **향후 미개봉 날짜의 shadow**로만
평가합니다.

기존 운영 점 모델 summary는 결과 디렉터리의 정확한 artifact 파일 집합과 각 joblib·metadata의 SHA-256,
훈련·추론 source SHA-256을 기록합니다. 분포 정책과 strict meta도 해당 점 summary, 입력 캐시,
component metadata, 자체 source와 artifact SHA-256에 결합됩니다. 서비스는 점 모델·분포
계약을, prospective 평가는 여기에 선택한 meta shadow 계약까지 검증하며 하나라도 달라지면
fail-closed됩니다.
기존 운영 모델과 입력 CSV의 예측 재현성은
`seat_service_model.py --verify-cache`로 확인할 수 있습니다.

```sh
venv/bin/python -m pip install -r requirements-analysis.txt
venv/bin/python analysis/remaining_seats_eda.py
venv/bin/python analysis/tminus_seat_regression.py --horizons 5
venv/bin/python analysis/all_prearrival_seat_regression.py
venv/bin/python analysis/probabilistic_seat_regression.py
venv/bin/python analysis/hypothesis_model_search.py
venv/bin/python analysis/route_distribution_search.py
venv/bin/python analysis/previous_bus_feature_audit.py
venv/bin/python analysis/strict_meta_stack_experiment.py

# 현재 primary alias의 전체 활성 노선 통합 점 모델
PYTHONPATH=analysis venv/bin/python analysis/pooled_main_model_registry.py \
  --registry analysis/model_registry/arrival-seat-pooled/registry.json \
  --input-csv input_snapshots.csv \
  --output-csv seat_predictions.csv

# 기존 운영 모델의 artifact·분포 정책 재현성 확인
venv/bin/python analysis/seat_service_model.py \
  --verify-cache data/analysis_cache/all_prearrival_A.pkl

# 선택 사항: 점예측에 90% 구간과 저잔여 확률을 함께 출력
venv/bin/python analysis/seat_service_model.py \
  --input-csv input_snapshots.csv \
  --output-csv seat_predictions.csv \
  --distribution-flow-cache analysis/route_distribution_results/frozen_distribution_flows.pkl \
  --interval-policy analysis/route_distribution_results/interval_policy.json

# 새 수집일이 생긴 뒤: 모델·정책은 고정하고 mutable 분석 캐시만 갱신
venv/bin/python analysis/refresh_prospective_cache.py

# 기존 운영 point와 고정 meta shadow의 prospective 평가
venv/bin/python analysis/prospective_model_evaluation.py \
  --interval-policy analysis/route_distribution_results/interval_policy.json \
  --meta-shadow-dir analysis/strict_meta_stack_results
```

결과 CSV·JSON·그래프는 `analysis/remaining_seats_eda_results/`와
`analysis/arrival_seat_regression_results/`, `analysis/all_prearrival_seat_results/`,
`analysis/probabilistic_seat_results/`에 생성됩니다.
가설 탐색 결과와 기존 운영 서비스 모델은 `analysis/model_search_results/`에 생성되며, 재사용 가능한
분석 DataFrame은 `data/analysis_cache/`에 로컬 캐싱됩니다. 계층 노선 분포의 선택 정책,
순차 OOF 예측구간, 저잔여 확률 평가는 `analysis/route_distribution_results/`에 생성됩니다.
정책에 사용한 stop-flow는 같은 디렉터리의 `frozen_distribution_flows.pkl`로 고정되며,
strict residual meta의 결과와 고정 shadow artifact는 `analysis/strict_meta_stack_results/`에
생성됩니다. prospective 명령에서 `--meta-shadow-dir`를 생략하면 primary 점 모델과 분포만
평가합니다.
prospective 평가는 기존 결과를 덮어쓰지 않고 `analysis/prospective_evaluation_results/` 아래
새 run 디렉터리에 저장됩니다.

Prospective CLI는 모델·정책·flow를 재학습하지 않고, 8월 11일 이후의 종료된 미사용 날짜
전체만 평가합니다. 오전 06~10시와 오후 16~21시 각 시간에 실제 도착 사건이 없는 부분
수집일도 거부합니다. 평가지표를 열기 전에 날짜를 프로젝트 전역 registry에 원자적으로 claim하므로
출력 폴더나 artifact를 바꿔 같은 날짜를 다시 미개봉 테스트로 사용할 수 없습니다. 새 날짜가
수집되면 먼저 cache refresh를 실행해 snapshot·mutable stop-flow·SHA metadata bundle만
갱신해야 합니다. 현재 cache에는 8월 11일 이후 날짜가 없어 평가 명령은 결과를 만들지 않고
fail-closed됩니다.

## 테스트

수집기와 API 테스트는 수집기 디렉터리에서 실행합니다.

```sh
venv/bin/python -m pip install -r collector/requirements.txt
cd collector
../venv/bin/python -m unittest discover -s tests -v
```
>>>>>>> 2d4afec (feat: .gitignore 및 README.md 업데이트)
