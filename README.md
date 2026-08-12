# 10th-toy-team4

경기도 버스정보(GBIS)의 차량 위치와 잔여좌석 데이터를 수집하고 팀원에게 제공하는
프로젝트입니다.

## 팀원이 수집 데이터 받기

루트의 `.env`에 `GBIS_API_BASE_URL`과 `GBIS_API_KEY`를 설정하고 의존성을 설치합니다.

```sh
python3 -m pip install -r requirements-client.txt
```

노선 목록, 정류장, 최신 차량 위치를 내려받습니다.

```sh
python get_data.py
```

출력된 노선 ID를 사용하면 해당 노선의 전체 과거 이력도 내려받습니다.

```sh
python get_data.py 233000031
```

결과 CSV는 기본적으로 `data/csv/`에 저장되고, API 응답 캐시는
`data/gbis_api_cache.sqlite3`에 저장됩니다. 두 번째 실행부터는 마지막 성공 시점 이후의
이력만 증분으로 내려받습니다.

서버·수집기 운영 방법은 `collector/README.md`, 클라이언트의 자세한 사용법은
`gbis_client/README.md`를 참고하세요.

## 다음 정류장 도착 잔여좌석 분석과 모델 학습

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-model.txt
.venv/bin/python get_data.py 219000013
.venv/bin/python analyze_and_train.py
```

동일 차량의 연속 관측을 연결하여 다음 정류장에 도착했을 때의 잔여좌석을 예측합니다. 현재
잔여좌석, 정류장 이동, 최근 좌석 변화, 시간대, 차량 상태와 `weather_log.csv`의 기온·강수량·
풍속을 사용합니다. Ridge 회귀와 Random Forest 회귀를 시간순 80:20 분할로 비교합니다.

실행 화면에 모델 파라미터와 MAE, MSE, RMSE, R², explained variance, median/max error,
MAPE, ±3석·±5석 적중률이 표시됩니다. 모델, 평가 보고서, 예측 CSV와 비교 그래프는
`data/analysis/models/`에 저장됩니다.
