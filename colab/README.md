# 비개발자 팀원용 Google Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khuda-data/10th-toy-team4/blob/main/colab/gbis_data_quickstart.ipynb)

`gbis_data_quickstart.ipynb`는 개발 환경 설치 없이 GBIS API 데이터를 pandas DataFrame으로
불러오는 노트북입니다.

## 팀원이 처음 할 일

1. 위의 **Open in Colab** 버튼을 누릅니다.
2. Colab 왼쪽의 열쇠 아이콘에서 `GBIS_API_KEY` Secret을 추가합니다.
3. 전달받은 개인 API 키를 입력하고 Notebook access를 켭니다.
4. **런타임 → 모두 실행**을 누릅니다.
5. Google Drive 접근 요청을 승인합니다.

최초 실행에는 서버의 전체 이력을 최대 30일 단위로 나누어 내려받습니다. 이후 실행부터는
Google Drive에 보관한 캐시를 Colab 로컬 디스크로 복원한 뒤 마지막 성공 시각 이후의 데이터만
받습니다. SQLite를 Drive에서 직접 열지 않으므로 Drive의 느린 소규모 I/O와 파일 잠금 문제를
피할 수 있습니다.

노트북이나 출력에는 API 키가 저장되지 않습니다. 팀원이 바뀌거나 키가 유출되면 서버의
`GBIS_API_KEYS`에서 해당 키만 제거하고 API 서비스를 재시작하면 됩니다.
