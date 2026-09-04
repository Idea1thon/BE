# 추천 API 서비스 코드 구조

## 구조

~~~text
services/recommendation-api/
├── api/                 # FastAPI HTTP 경계
├── scripts/             # 이 서비스의 CLI forwarding entrypoint
└── recommendation/
    ├── pipeline.py
    ├── llm_input_planner.py
    ├── llm_explanation.py
    ├── llm_runtime.py
    ├── serving_db.py
    ├── env.py
    └── paths.py
~~~

FastAPI와 추천 로직은 `services/recommendation-api/` 아래에 둔다. 원천
`data/`, 공유 Evidence 계약 `artifacts/`, 분석용 스크립트는 저장소 루트에
남겨 중간 백엔드와 이름 충돌을 피한다. 폐업 위험 사이렌은 별도 작업공간
`/Users/parkjunwoo/Documents/siren/`에서 관리한다.

서비스 전용 `.env`는 `services/recommendation-api/.env`에 둔다. 루트 `.env`는
분석 저장소의 다른 스크립트가 사용할 수 있으므로 추천 API가 자동으로 읽지 않는다.

실행:

~~~bash
cd services/recommendation-api
../../.venv/bin/python -m recommendation.pipeline --sido 서울특별시 --sigungu 송파구 --dong 잠실동 --industry-code CS100010 --llm-mode offline --source files
~~~
