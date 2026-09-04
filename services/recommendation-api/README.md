# 추천 API 서비스 코드 구조

## 구조

~~~text
services/recommendation-api/
├── api/                 # FastAPI HTTP 경계
├── artifacts/           # 서비스가 검증에 사용하는 Evidence 계약
├── docs/                # 서비스 계약·운영 문서
├── scripts/             # CLI forwarding·DB 보완 적재 entrypoint
├── db/                  # 서비스가 요구하는 누락 테이블 보완 DDL
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

서빙 DB의 누락 테이블을 만들고 원천 스냅샷을 보완 적재할 때는 다음을 실행한다.
`area_store_totals`는 `data/점포/2026년`의 전 업종 행을 합산하고,
`commercial_building`은 `data/건축물대장/상가건물_서울.csv`를 upsert한다.

~~~bash
cd services/recommendation-api
../../.venv/bin/python scripts/supplement_serving_tables.py
~~~

기본 seed는 건축물대장 주용도 상업용 건물의 footprint centroid다. 이 데이터는
실제 임대 매물·공실·전유부 호실이 아니므로 후보 유형은 `상가건물_인근`이며
`추천` 등급으로 승격되지 않는다. `RECOMMENDATION_SEED_MODE=anchors`는 기존
역·아파트·POI·생성점 seed로 되돌리고, `hybrid`는 두 모집단을 함께 사용한다.
이 설정은 배포 정책이므로 중간 백엔드의 HTTP 요청/응답 계약에는 노출하지 않는다.
