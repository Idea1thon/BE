# 추천 API 서비스 코드 구조

## 구조

~~~text
services/recommendation-api/
├── api/                 # FastAPI HTTP 경계
├── artifacts/           # 서비스가 검증에 사용하는 Evidence 계약
├── docs/                # 서비스 계약·운영 문서
├── scripts/             # 이 서비스의 CLI forwarding entrypoint
├── seoul_gu_dong_list.csv
└── recommendation/
    ├── pipeline.py
    ├── llm_input_planner.py
    ├── llm_explanation.py
    ├── llm_runtime.py
    ├── serving_db.py
    ├── env.py
    └── paths.py
~~~

FastAPI, 추천 로직, 서비스 전용 CLI, Evidence 계약, 지역 카탈로그는 모두
`services/recommendation-api/` 아래에 둔다. 대용량 원천 `data/`만 배포 시
외부 볼륨으로 제공한다. 폐업 위험 사이렌은 별도 작업공간
`/Users/parkjunwoo/Documents/siren/`에서 관리한다.

원천 데이터 경로는 `RECOMMENDATION_DATA_ROOT`로 지정할 수 있다. 비워 두면
로컬 개발에서는 `data/` 디렉터리가 있는 저장소 상위 경로를 자동으로 찾는다.
운영 배포에서는 데이터 볼륨의 절대 경로를 명시하는 것을 권장한다.

서비스 전용 `.env`는 `services/recommendation-api/.env`에 둔다. 루트 `.env`는
분석 저장소의 다른 스크립트가 사용할 수 있으므로 추천 API가 자동으로 읽지 않는다.

실행:

~~~bash
cd services/recommendation-api
../../.venv/bin/python -m recommendation.pipeline --sido 서울특별시 --sigungu 송파구 --dong 잠실동 --industry-code CS100010 --llm-mode offline --source files
~~~
