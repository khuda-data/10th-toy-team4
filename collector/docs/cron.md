# cron 운영 가이드

광역버스 잔여좌석 수집기를 정해진 주기로 자동 실행하는 방법을 설명합니다.

## 등록

프로젝트 루트에서 다음 스크립트를 실행합니다.

```sh
./collector/scripts/install_cron.sh
```

스크립트는 등록 전에 API 설정을 검사하고 노선 정류장 메타데이터를 한 번 동기화합니다.
검사가 실패하면 cron을 등록하지 않습니다.

## 수집 주기

등록되는 실행 주기는 다음과 같습니다.

- 06~10시, 16~21시: 1분 간격
- 나머지 시간: 5분 간격
- 매일 03:10: 노선 정류장 메타데이터 동기화

실제 등록 내용은 다음과 같습니다.

```cron
# BEGIN 10th-toy-team4-gbis-collector
# 출퇴근(06-10시, 16-21시): 1분 간격
* 6-10,16-21 * * * /absolute/path/to/collector/scripts/run_collector.sh
# 그 외 시간: 5분 간격
*/5 0-5,11-15,22-23 * * * /absolute/path/to/collector/scripts/run_collector.sh
# 노선 정류장 메타데이터: 매일 03:10 동기화
10 3 * * * /absolute/path/to/collector/scripts/run_collector.sh sync-metadata
# END 10th-toy-team4-gbis-collector
```

`/absolute/path/to` 부분에는 설치 스크립트가 확인한 현재 프로젝트의 절대경로가 자동으로
들어갑니다.

## API 호출량

이 주기는 노선당 하루 약 816회의 위치 API 호출을 사용합니다. 코드의 기본 일일 안전 한도는
인증키 하나당 1,000회입니다. `collector/config/routes.txt`는 후보 풀로 사용되며 활성 노선 수는 다음처럼
자동 계산됩니다.

```text
활성 노선 수 = min(후보 수, (인증키 수 × 키당 일일 안전 한도) ÷ 816)
```

따라서 기본 설정에서는 키 하나면 후보 1개, 키 두 개면 후보 2개가 위에서부터 활성화됩니다.
키를 추가하거나 제거하면 다음 실행부터 활성 노선 수도 자동 변경됩니다. 수집기는 호출 횟수가
가장 적은 키를 골라 요청을 균등하게 분배하고, 키별 사용량이 안전 한도에 도달하면 남은 키만
사용합니다.

노선 수가 더 많다면 다음 중 하나가 필요합니다.

- `collector/scripts/install_cron.sh`의 수집 간격 늘리기
- `.env`의 `GBIS_SERVICE_KEYS`에 별도 활용 한도를 가진 인증키 추가하기
- `GBIS_DAILY_REQUEST_LIMIT`과 실제 발급 계정 한도 함께 검토하기
- 공공데이터포털에서 운영계정 트래픽 증설 신청하기

동일 계정이나 동일 활용신청에서 나온 여러 키는 실제 트래픽 한도를 공유할 수 있습니다. 키
개수만큼 한도가 늘어난다고 가정하지 말고 공공데이터포털의 각 활용신청 상세 화면에서 일일
트래픽 한도를 확인해야 합니다. 코드의 안전 한도만 높이면 실제 API 요청은 실패할 수 있습니다.

## 기존 cron과의 관계

기존 crontab 내용은 유지됩니다. 설치 스크립트는 아래 표식 사이에서 이 프로젝트가 만든
항목만 추가하거나 교체합니다.

```text
# BEGIN 10th-toy-team4-gbis-collector
...
# END 10th-toy-team4-gbis-collector
```

따라서 설정을 바꾼 뒤 설치 스크립트를 다시 실행해도 동일한 작업이 중복 등록되지 않습니다.

## 실행 확인

등록된 작업과 수집 로그를 확인합니다.

```sh
crontab -l
tail -f data/logs/collector.log
```

수집 결과는 명령행에서도 확인할 수 있습니다.

```sh
cd collector
../venv/bin/python -m gbis_collector stats
```
