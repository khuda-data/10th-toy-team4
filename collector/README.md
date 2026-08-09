# 데이터 수집기

경기도 버스정보 Open API의 차량 위치와 잔여좌석을 수집하고, SQLite 데이터를 팀에 읽기 전용
API로 제공하는 모듈입니다. 저장소 루트의 `.env`, `venv/`, `data/gbis.sqlite3`를 공유하므로
모델·전처리 코드가 기존 데이터 경로를 그대로 사용할 수 있습니다.

## 구성

- `gbis_collector/`: 수집 CLI, 저장소, 읽기 전용 API
- `config/`: 수집 노선 목록과 예시
- `scripts/`: 수집 실행 및 cron 등록 스크립트
- `docs/`: 운영 문서
- `tests/`: 수집기와 API 테스트
- `requirements.txt`: 수집기 API 실행 의존성

## 준비

저장소 루트에서 실행합니다.

```sh
cp collector/.env.example .env
cp collector/config/routes.txt.example collector/config/routes.txt
venv/bin/python -m pip install -r collector/requirements.txt
```

## 실행

```sh
cd collector
../venv/bin/python -m gbis_collector doctor
../venv/bin/python -m gbis_collector collect
../venv/bin/python -m gbis_collector stats
../venv/bin/python -m gbis_collector serve-api
```

자동 수집 설정은 [cron 운영 가이드](docs/cron.md)를 참고합니다.

## 테스트

```sh
cd collector
../venv/bin/python -m unittest discover -s tests -v
```
