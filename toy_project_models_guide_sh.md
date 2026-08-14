# 광역버스 잔여좌석 모델 실행 가이드 및 분석 결과

## 빠른 시작 가이드

이 프로젝트는 다음 순서로 실행한다.

```text
환경 준비
  ↓
전체 노선 데이터 수집
  ↓
Ridge·Random Forest 학습 및 평가
  ↓
결과 파일 또는 Notebook 확인
```

### 1. 환경 준비

프로젝트 루트에서 가상환경을 만들고 데이터 수집·모델링·Notebook 실행에 필요한 패키지를
설치한다. 기존 `.venv`가 있다면 첫 번째 명령은 생략할 수 있다.

```bash
cd /home/shkim/Py/10th-toy-team4
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-model.txt
```

프로젝트 루트의 `.env`에는 팀에서 받은 API 접속 정보를 설정한다.

```dotenv
GBIS_API_BASE_URL=https://팀서버주소
GBIS_API_KEY=개인_API_KEY
GBIS_API_CACHE_PATH=data/gbis_api_cache.sqlite3
```

### 2. 전체 노선 데이터 받기

```bash
.venv/bin/python get_data.py --all
```

`get_data.py`가 노선·정류장·최신 위치·누적 이력을 내려받는다. 모델의 기본 입력 파일은 전체
노선을 합친 `data/csv/history_all.csv`이고 날씨 입력은 `data/csv/weather_log.csv`이다.

특정 노선만 받으려면 노선 ID를 전달한다.

```bash
.venv/bin/python get_data.py 219000013
```

### 3. `.py` 방식으로 모델 학습·평가하기

자동화와 재현을 위한 기준 실행 방법이다.

```bash
.venv/bin/python analyze_and_train.py
```

이 명령 하나로 전처리, 특징 생성, 시간순 학습·테스트 분할, Ridge와 Random Forest 학습,
전체 테스트 평가와 실제 잔여좌석 0~10석 구간 평가, 모델 및 결과 저장을 수행한다.

### 4. `.ipynb` 방식으로 분석 결과 확인하기

VS Code 또는 Jupyter에서 `toy_project_models.ipynb`를 열고 프로젝트의 `.venv` 커널을 선택한
뒤 위에서부터 셀을 실행한다. Notebook은 기본적으로 `.py`가 생성한 모델 보고서와 예측 결과를
읽어 표와 그래프로 보여준다.

최초 Notebook 실행 전에는 다음 두 작업이 완료되어 있어야 한다.

```bash
.venv/bin/python get_data.py --all
.venv/bin/python analyze_and_train.py
```

Notebook 안에서 모델까지 다시 학습하려면 마지막 셀의 주석을 제거한다.

```python
%run analyze_and_train.py
```

### 5. 결과 확인

모든 모델과 평가 결과는 기본적으로 `data/analysis/models/`에 저장된다.

| 파일 | 내용 |
|---|---|
| `ridge_model.joblib` | 전처리를 포함한 Ridge 모델 |
| `random_forest_model.joblib` | 전처리를 포함한 Random Forest 모델 |
| `ridge_model.pkl` | Ridge 모델의 pickle 형식 |
| `random_forest_model.pkl` | Random Forest 모델의 pickle 형식 |
| `model_report.json` | 데이터 분할, 파라미터, 전체·10석 이하 평가 지표 |
| `predictions.csv` | 전체 테스트셋 실제값과 예측값 |
| `predictions_within_10.csv` | 실제 잔여좌석 0~10석 테스트 결과 |
| `model_comparison.png` | 실제값과 두 모델 예측값 비교 그래프 |

### 6. 데이터가 추가된 뒤 반복할 작업

```bash
cd /home/shkim/Py/10th-toy-team4
.venv/bin/python get_data.py --all
.venv/bin/python analyze_and_train.py
```

두 명령 실행 후 `toy_project_models.ipynb`를 다시 실행하면 최신 결과를 확인할 수 있다. 여러
파라미터 실험을 비교할 때는 `--output-dir`을 다르게 지정하여 기존 결과가 덮어써지지 않게 한다.

---

# 모델 구성 및 분석 결과

## 1. 모델링 목적

현재 차량의 위치와 잔여좌석 정보를 이용해 **같은 차량이 다음 정류장에 도착했을 때의
잔여좌석 수**를 예측하는 회귀 문제로 정의했다. 베이스 모델로 Ridge 회귀와 Random Forest
회귀를 학습하고 동일한 테스트 데이터에서 성능을 비교했다.

- 입력 시점: 차량이 현재 정류장에서 관측된 시점
- 목표 시점: 해당 차량이 다음 정류장에서 관측된 시점
- 목표변수: `arrival_remaining_seats`(다음 정류장 도착 잔여좌석)
- 사용 노선: 수집된 전체 17개 노선

## 2. 데이터 구성

전체 버스 관측 468,982건에서 동일 차량이 같은 정류장에 머무는 동안 반복 수집된 행을 하나의
방문으로 축약했다. 이후 차량별 관측을 시간순으로 정렬하고 현재 정류장 방문과 다음 정류장
방문을 연결해 학습 샘플을 만들었다.

다음 조건을 만족하는 이동만 사용했다.

- 현재 및 다음 관측의 잔여좌석이 0 이상일 것
- 다음 관측까지의 시간이 0.1분 이상 30분 이하일 것
- 정류장 순번 차이가 1개 이상 5개 이하일 것
- 다음 정류장의 잔여좌석이 존재할 것

최종적으로 생성된 학습용 정류장 이동 데이터는 206,874건이다.

| 구분 | 데이터 수 |
|---|---:|
| 원본 버스 관측 | 468,982건 |
| 학습 가능한 정류장 이동 | 206,874건 |
| 학습 데이터 | 157,465건 |
| 테스트 데이터 | 49,409건 |

## 3. 입력 변수

### 숫자형 변수

- `remaining_seats`: 현재 잔여좌석
- `station_seq`, `next_station_seq`: 현재·다음 정류장 순번
- `stations_ahead`: 정류장 순번 차이
- `recent_seat_change`: 직전 정류장 대비 현재 좌석 변화량
- `minutes_since_previous_station`: 직전 정류장 관측 이후 경과시간
- `hour_sin`, `hour_cos`: 하루의 주기성을 반영한 시각 변수
- `day_of_week`, `is_weekend`, `is_rush_hour`: 요일·주말·출퇴근 시간 변수
- `temperature`, `precipitation`, `wind_speed`: 기온·강수량·풍속
- `weather_available`: 해당 시각에 날씨가 결합되었는지 나타내는 값

### 범주형 변수

- `route_id`, `vehicle_id`
- `route_type_code`
- `crowded`, `low_plate`, `state_code`, `tagless_code`

범주형 변수는 One-Hot Encoding으로 변환했다. 테스트 데이터에 학습 시 보지 못한 범주가
등장하면 `handle_unknown="ignore"`를 적용해 오류 없이 예측하도록 했다.

## 4. 결측치 처리

관측 시각, 차량 ID, 정류장 순번 또는 현재 잔여좌석처럼 학습 샘플을 구성하는 데 반드시 필요한
값이 없는 행은 제거했다. 잔여좌석 `-1`은 실제 좌석 수가 아니라 정보 미제공 값이므로 제외했다.
다음 정류장의 잔여좌석이 없으면 정답을 만들 수 없으므로 해당 샘플도 학습에서 제외했다.

모델 입력에 남아 있는 결측치는 scikit-learn `Pipeline` 내부에서 처리했다.

- 숫자형 변수: 학습 데이터의 중앙값으로 대체
- 범주형 변수: `unknown` 또는 학습 데이터의 최빈값으로 대체
- 날씨: 버스 관측 시각에서 31분 이내인 가장 가까운 시간의 날씨만 결합
- 날씨가 없는 시점: 날씨 숫자값은 중앙값으로 대체하고 `weather_available=0`으로 표시

날씨 데이터는 8월 7일까지 존재해 전체 학습용 데이터의 날씨 결합률은 19.78%였다. 결측치
대체 기준은 학습 데이터에서만 계산되므로 테스트 데이터 정보가 학습 과정에 유입되지 않는다.

현재 학습 코드에는 서비스 운영 시 동일 차량의 최근 정상값을 가져오는 기능은 포함되어 있지
않다. 이는 추후 실시간 예측 API를 구현할 때 데이터 경과시간과 함께 추가할 예정이다.

## 5. 학습 및 평가 방법

일반적인 무작위 `train_test_split`은 사용하지 않았다. 버스 관측을 시간순으로 정렬한 뒤 앞쪽
약 80%를 학습, 뒤쪽 약 20%를 테스트 데이터로 사용했다. 동일 관측 시각의 행이 양쪽에 나뉘지
않도록 고유 시각을 기준으로 경계를 정했다.

- 학습 데이터 종료: 2026-08-13 06:44:02 KST
- 테스트 데이터 시작: 2026-08-13 06:44:03 KST

이 방식은 과거 데이터로 모델을 학습한 뒤 미래 시점의 관측을 평가하므로 무작위 분할보다 실제
서비스 상황에 가깝고 시간 누수 위험이 작다. 현재 평가는 한 번의 시간순 holdout 평가이며,
하이퍼파라미터 탐색을 위한 `TimeSeriesSplit` 교차검증은 아직 적용하지 않았다.

## 6. Ridge 회귀

Ridge는 선형회귀의 손실함수에 L2 규제를 추가한 모델이다. 입력 변수가 많거나 서로 상관관계가
있을 때 회귀계수가 지나치게 커지는 것을 억제한다. 현재 잔여좌석과 다음 정류장 잔여좌석 사이의
강한 선형 관계를 베이스라인으로 확인하기 위해 사용했다.

### 주요 파라미터

| 파라미터 | 값 | 의미 |
|---|---:|---|
| `alpha` | 10.0 | L2 규제 강도 |
| `fit_intercept` | `True` | 절편 학습 여부 |
| `solver` | `auto` | 데이터에 따라 solver 자동 선택 |
| `tol` | 0.0001 | 최적화 종료 허용오차 |
| `positive` | `False` | 회귀계수를 양수로 제한하지 않음 |

Ridge 입력에는 숫자형 변수의 중앙값 대체와 표준화, 범주형 변수의 One-Hot Encoding을 먼저
적용했다.

## 7. Random Forest 회귀

Random Forest는 여러 결정트리를 서로 다른 표본과 변수 조합으로 학습한 뒤 결과를 평균내는
앙상블 모델이다. 선형 모델이 표현하기 어려운 시간대·정류장·차량 상태 사이의 비선형 관계와
상호작용을 학습하기 위해 사용했다.

### 주요 파라미터

| 파라미터 | 값 | 의미 |
|---|---:|---|
| `n_estimators` | 300 | 생성할 결정트리 수 |
| `max_depth` | 18 | 각 트리의 최대 깊이 |
| `min_samples_leaf` | 2 | 리프 노드가 가져야 할 최소 샘플 수 |
| `max_features` | `sqrt` | 분할마다 전체 변수 수의 제곱근만 후보로 사용 |
| `criterion` | `squared_error` | 분할 품질 평가 기준 |
| `bootstrap` | `True` | 부트스트랩 표본 사용 |
| `n_jobs` | -1 | 사용 가능한 CPU 코어를 모두 사용 |
| `random_state` | 42 | 재현성을 위한 난수 고정 |

## 8. 평가 지표

- **MAE**: 실제값과 예측값 차이의 절댓값 평균. 좌석 수 단위로 해석할 수 있다.
- **MSE/RMSE**: 큰 오차에 더 큰 패널티를 부여한다. RMSE는 좌석 수 단위다.
- **R²**: 목표변수의 변동을 모델이 설명하는 비율이다.
- **Explained variance**: 예측이 실제값의 분산을 얼마나 설명하는지 나타낸다.
- **Median absolute error**: 절대오차의 중앙값으로 이상치의 영향을 덜 받는다.
- **Max error**: 테스트 데이터에서 발생한 가장 큰 절대오차다.
- **MAPE**: 실제 잔여좌석이 0이 아닌 행만 대상으로 계산한 평균 절대 백분율 오차다.
- **±3석·±5석 적중률**: 예측값이 실제값에서 각각 3석, 5석 이내인 비율이다.

## 9. 전체 노선 테스트 결과

| 평가 지표 | Ridge | Random Forest |
|---|---:|---:|
| MAE | **1.3412석** | 2.7121석 |
| MSE | **6.2030** | 13.0884 |
| RMSE | **2.4906석** | 3.6178석 |
| R² | **0.9487** | 0.8918 |
| Explained variance | **0.9487** | 0.8923 |
| Median absolute error | **0.7209석** | 2.2019석 |
| Max error | 43.6605석 | **38.5181석** |
| ±3석 적중률 | **89.49%** | 65.26% |
| ±5석 적중률 | **95.33%** | 88.32% |
| MAPE(0석 제외) | **6.24%** | 13.53% |

## 10. 실제 잔여좌석 10석 이하 구간 평가

모델은 전체 잔여좌석 구간의 학습 데이터로 학습한 상태를 유지하고, 전체 시간순 테스트셋에서
실제 다음 정류장 도착 잔여좌석이 0~10석인 행만 추려 추가 평가했다. 따라서 10석 이하 데이터로
모델을 다시 학습한 결과가 아니다.

- 전체 테스트 데이터: 49,409건
- 실제 잔여좌석 0~10석 테스트 데이터: 2,433건
- 전체 테스트에서 차지하는 비율: 4.92%

| 평가 지표 | Ridge | Random Forest |
|---|---:|---:|
| MAE | **2.2893석** | 5.3830석 |
| MSE | **18.4130** | 43.5437 |
| RMSE | **4.2910석** | 6.5988석 |
| R² | **-0.4132** | -2.3421 |
| Explained variance | -0.1846 | **-0.1734** |
| Median absolute error | **1.1590석** | 4.6790석 |
| Max error | 43.6600석 | **38.5180석** |
| ±3석 적중률 | **82.00%** | 18.13% |
| ±5석 적중률 | **90.26%** | 56.56% |
| MAPE(0석 제외) | **56.56%** | 145.45% |

저잔여석 구간에서는 두 모델 모두 전체 테스트 결과보다 오차가 커졌다. 특히 Random Forest는
MAE가 5.38석이고 ±3석 적중률이 18.13%로 낮았다. Ridge는 상대적으로 안정적이지만 R²가
음수이므로 저잔여석 구간의 세부 변동을 충분히 설명한다고 보기는 어렵다. 이 구간에서 R²가
음수라는 것은 해당 부분집합의 평균값을 사용하는 단순 기준보다 제곱오차 관점에서 성능이 낮다는
뜻이다.

MAPE는 실제값이 작은 구간에서 작은 절대오차도 매우 큰 백분율로 계산되며 0석 행은 계산에서
제외되므로, 이 문제에서는 MAE와 ±3석·±5석 적중률을 중심으로 해석하는 것이 적절하다.

## 11. 결과 해석

Ridge는 최대오차를 제외한 대부분의 지표에서 Random Forest보다 좋은 결과를 보였다. 특히
Ridge의 MAE는 약 1.34석이고 전체 테스트 데이터의 95.33%가 실제값의 ±5석 이내였다. 이는
다음 정류장 잔여좌석이 현재 잔여좌석과 강한 선형 관계를 가지며, 한 정류장 이동 동안의 좌석
변화가 대체로 크지 않기 때문으로 해석할 수 있다.

Random Forest는 비선형 관계를 학습할 수 있지만 현재 설정에서는 Ridge보다 MAE가 약 1.37석
크고 ±3석 적중률도 낮았다. 가능한 원인으로는 전체 노선·차량을 One-Hot Encoding한 고차원
입력, 시간순 테스트 구간에서의 분포 변화, `max_features="sqrt"`에 따른 개별 트리의 정보 제한이
있다. 추후 `TimeSeriesSplit`으로 `max_depth`, `max_features`, `min_samples_leaf` 등을 탐색할
필요가 있다.

전체 지표가 높더라도 만차 또는 5석 미만 구간의 성능을 보장하지는 않는다. 저잔여석 데이터가
전체에서 차지하는 비율이 작으므로, 실제 서비스 목적에 맞게 테스트셋의 0~4석 구간에 대한 MAE,
±3석 적중률, 만차 위험 분류 성능을 별도로 평가해야 한다.

## 12. 상세 파일 및 옵션 참고자료

### 12.1 작업 순서 요약

프로젝트 작업은 다음 순서로 진행한다.

```text
1. .env에 팀 GBIS API 접속 정보 설정
2. requirements-model.txt로 실행 환경 준비
3. get_data.py로 전체 노선 데이터 수신
4. weather_log.csv 확인
5. analyze_and_train.py로 데이터 가공·모델 학습·평가
6. 생성된 CSV·JSON·PNG에서 결과 확인
7. toy_project_models.ipynb에서 표와 그래프로 결과 탐색
8. .joblib 또는 .pkl 모델을 추론 코드에서 사용
```

### 12.2 주요 파일과 역할

| 파일 | 역할 | 직접 실행 여부 |
|---|---|---|
| `.env` | GBIS 서버 주소, 개인 API 키, 로컬 캐시 경로 설정 | 실행하지 않음 |
| `requirements-model.txt` | 데이터 수집·분석·Notebook 실행에 필요한 패키지 목록 | `pip install`에 사용 |
| `get_data.py` | 팀 서버에서 노선·정류장·최신 위치·누적 이력 수신 | 실행 |
| `data/csv/weather_log.csv` | 시간별 기온·강수량·풍속 입력 데이터 | 실행하지 않음 |
| `analyze_and_train.py` | 전처리, 특징 생성, Ridge/RF 학습, 전체·10석 이하 평가 | 실행 |
| `toy_project_models.ipynb` | 데이터와 저장된 평가 결과를 셀·표·그래프로 확인 | Jupyter/VS Code에서 실행 |
| `toy_project_models_guide_sh.md` | 실행 가이드, 모델 구성, 평가 방법과 결과 문서 | 읽기용 |
| `data/analysis/models/*.joblib` | `joblib` 형식의 학습 완료 Pipeline | 추론 코드에서 로드 |
| `data/analysis/models/*.pkl` | 표준 `pickle` 형식의 학습 완료 Pipeline | 추론 코드에서 로드 |

## 13. 최초 환경 준비

프로젝트 루트에서 가상환경을 만들고 필요한 패키지를 설치한다. 이미 `.venv`가 준비되어 있다면
가상환경 생성 명령은 생략할 수 있다.

```bash
cd /home/shkim/Py/10th-toy-team4
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-model.txt
```

프로젝트 루트의 `.env`에는 팀원에게 받은 접속 정보를 설정한다. 키 원문은 코드나 Notebook에
직접 입력하지 않는다.

```dotenv
GBIS_API_BASE_URL=https://팀서버주소
GBIS_API_KEY=개인_API_KEY
GBIS_API_CACHE_PATH=data/gbis_api_cache.sqlite3
```

## 14. 데이터 받기: `get_data.py`

### 전체 노선 받기

모델을 다시 학습할 때 사용하는 기본 명령이다.

```bash
.venv/bin/python get_data.py --all
```

이 명령은 다음 파일을 생성하거나 갱신한다.

| 결과 | 내용 |
|---|---|
| `data/csv/routes.csv` | 수집 가능한 노선 목록과 관측 건수 |
| `data/csv/stations.csv` | 노선별 정류장 정보 |
| `data/csv/latest_locations.csv` | 차량별 최신 위치·잔여좌석 |
| `data/csv/history_<노선ID>.csv` | 노선별 누적 관측 이력 |
| `data/csv/history_all.csv` | 전체 노선 이력을 합친 모델 입력 파일 |
| `data/gbis_api_cache.sqlite3` | API 응답을 보관하는 로컬 SQLite 캐시 |

첫 실행은 전체 이력을 받으므로 시간이 걸릴 수 있다. 이후에는 로컬 캐시의 마지막 성공 시각
이후 데이터만 증분으로 받는다. 수집이 중단되어도 이미 저장된 페이지는 캐시에 남는다.

### 목록과 최신 상태만 받기

```bash
.venv/bin/python get_data.py
```

### 특정 노선 이력만 받기

```bash
.venv/bin/python get_data.py 219000013
```

이 경우 `data/csv/history_219000013.csv`처럼 해당 노선의 CSV만 갱신한다. 전체 통합 모델을
재학습하려면 최종적으로 `--all`을 실행해 `history_all.csv`도 갱신해야 한다.

## 15. `.py`를 이용한 모델 학습·평가

자동화하거나 최신 데이터로 모델을 다시 만들 때 사용하는 기준 방식이다.

```bash
.venv/bin/python analyze_and_train.py
```

`analyze_and_train.py`는 다음 작업을 한 번에 수행한다.

1. `history_all.csv`와 `weather_log.csv` 로드
2. 같은 차량·정류장의 반복 스냅샷을 하나의 방문으로 축약
3. 현재 방문과 다음 정류장 방문을 연결해 목표변수 생성
4. 시간·요일·최근 좌석 변화·날씨 등의 입력 변수 생성
5. 데이터를 시간순 약 80:20으로 학습·테스트 분리
6. Ridge와 Random Forest 학습
7. 전체 테스트셋과 실제 잔여좌석 0~10석 테스트셋 평가
8. 모델과 평가 결과 저장

실행 중 터미널에는 데이터 수, 분할 시각, 날씨 결합률, 입력 변수, 모델 파라미터와 모든 평가
지표가 출력된다. 다른 파일을 지정해 실험하려면 다음처럼 실행할 수 있다.

```bash
.venv/bin/python analyze_and_train.py \
  --history data/csv/history_219000013.csv \
  --weather data/csv/weather_log.csv \
  --output-dir data/analysis/route_219000013
```

주요 파라미터도 명령행에서 변경할 수 있다.

```bash
.venv/bin/python analyze_and_train.py \
  --ridge-alpha 5 \
  --rf-estimators 500 \
  --rf-max-depth 24 \
  --test-ratio 0.2
```

파라미터를 바꿔 실행하면 기본 출력 폴더의 기존 모델과 결과를 덮어쓰므로, 여러 실험을 비교할
때는 `--output-dir`을 실험별로 다르게 지정한다.

## 16. `.ipynb`를 이용한 분석

`toy_project_models.ipynb`는 발표·팀 공유·대화형 분석을 위한 Notebook이다. VS Code에서 파일을
열고 커널로 프로젝트의 `.venv` Python을 선택한 뒤 위에서부터 순서대로 실행한다.

Notebook의 기본 역할은 다음과 같다.

- 전체 노선과 데이터 건수 확인
- 저장된 `model_report.json`을 표로 표시
- 전체 테스트셋의 Ridge/RF 성능 비교
- 실제 잔여좌석 0~10석 구간의 성능 비교
- 실제값과 두 모델 예측값 시각화
- `.pkl` 모델 로딩 확인

Notebook은 기본적으로 이미 생성된 결과 파일을 읽는다. 따라서 최초 실행 전에는 다음 두 명령을
먼저 실행해야 한다.

```bash
.venv/bin/python get_data.py --all
.venv/bin/python analyze_and_train.py
```

Notebook 안에서 모델을 다시 학습하려면 마지막 셀의 주석을 제거한다.

```python
%run analyze_and_train.py
```

`.py` 버전이 전처리와 모델링의 기준 코드이고 Notebook은 그 결과를 설명하고 탐색하는 용도다.
팀 작업에서는 핵심 로직을 Notebook에 중복 구현하지 않고 `.py`를 실행하도록 유지하는 것이
결과 재현과 코드 관리에 유리하다.

## 17. 생성되는 모델 및 결과 파일

`analyze_and_train.py` 실행 결과는 기본적으로 `data/analysis/models/`에 저장된다.

| 파일 | 내용 |
|---|---|
| `ridge_model.joblib` | 전처리를 포함한 Ridge Pipeline의 joblib 파일 |
| `random_forest_model.joblib` | 전처리를 포함한 Random Forest Pipeline의 joblib 파일 |
| `ridge_model.pkl` | 같은 Ridge Pipeline의 pickle 파일 |
| `random_forest_model.pkl` | 같은 Random Forest Pipeline의 pickle 파일 |
| `model_report.json` | 데이터 분할, 파라미터, 전체·10석 이하 평가 지표 |
| `predictions.csv` | 전체 테스트셋의 실제값과 두 모델 예측값 |
| `predictions_within_10.csv` | 실제 도착 잔여좌석이 0~10석인 테스트 행 |
| `model_comparison.png` | 테스트셋의 실제값과 예측값 비교 그래프 |

`.joblib`과 `.pkl`은 확장자만 다른 파일이 아니라 각각의 라이브러리로 별도 저장한 동일 학습
Pipeline이다. 둘 중 하나만 사용하면 된다. scikit-learn 모델에서는 `.joblib`을 기본으로 권장하고,
제출 형식이나 연동 코드가 pickle을 요구할 때 `.pkl`을 사용한다.

```python
import joblib

ridge_model = joblib.load("data/analysis/models/ridge_model.joblib")
prediction = ridge_model.predict(input_dataframe)
```

```python
import pickle

with open("data/analysis/models/ridge_model.pkl", "rb") as file:
    ridge_model = pickle.load(file)

prediction = ridge_model.predict(input_dataframe)
```

두 모델 파일에는 숫자형 결측치 대체, 표준화, 범주형 One-Hot Encoding과 회귀 모델이 하나의
Pipeline으로 함께 저장되어 있다. 단, 예측용 DataFrame에는 학습에 사용한 22개 변수명과 동일한
컬럼이 필요하다. 또한 `.joblib`과 `.pkl`은 신뢰할 수 있는 프로젝트 파일만 로드해야 한다.

## 18. 권장 반복 작업 흐름

새로운 버스 데이터가 쌓인 뒤 모델과 보고서를 갱신할 때는 다음 순서만 반복하면 된다.

```bash
cd /home/shkim/Py/10th-toy-team4
.venv/bin/python get_data.py --all
.venv/bin/python analyze_and_train.py
```

그다음 `toy_project_models.ipynb`를 위에서부터 실행해 표와 그래프를 확인하고,
`model_report.json`의 전체 성능과 0~10석 성능을 함께 기록한다.
