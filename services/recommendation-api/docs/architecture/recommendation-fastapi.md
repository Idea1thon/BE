# FastAPI 백엔드 연결 계약

현재 브랜치의 FastAPI는 UI가 직접 호출하는 입력 처리 서버가 아니라, 중간 백엔드가 호출하는 추천 파이프라인 REST 경계다.

```text
UI
        │ 사용자 요청 전달
        ▼
중간 백엔드
        │ POST /internal/recommendations
        ▼
FastAPI 파이프라인 경계
        └─ 중간 백엔드 요청을 그대로 recommendation_pipeline에 전달
                └─ 입력 해석·결정론적 계약 검증
                └─ 읽기 전용 데이터 분석
                └─ 후보·Evidence 생성 및 검증
                └─ LLM 설명(검증 실패 시 안전한 템플릿 폴백)
        ▲
        └─ request_id + run_id + 후보/Evidence/설명 반환

위험 사이렌 파이프라인은 아직 구현하지 않았으므로 이번 서비스에는 별도 REST 호출이나 엔드포인트를 두지 않는다.
```

## 실행

서비스 디렉터리를 기준으로 API 의존성을 설치하고 실행한다.

```bash
cd services/recommendation-api
../../.venv/bin/pip install -r requirements-api.txt
../../.venv/bin/uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

`source=db`는 PostgreSQL/PostGIS가 이미 실행 중이고 이 저장소의 스키마·데이터
이식이 완료된 환경을 전제로 한다. Compose와 migration 파일은 이 서비스 커밋에
포함하지 않으므로 DB 인프라 저장소의 배포 절차로 준비해야 한다. DB를 사용할 때는
FastAPI 호스트에 `psql` 클라이언트도 필요하다.

```bash
docker compose version
psql --version
cd services/recommendation-api
../../.venv/bin/pip install -r requirements-api.txt
../../.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
```

macOS는 `brew install libpq` 후 `psql`이 PATH에 있는지 확인하고, Debian/Ubuntu는
`sudo apt-get install postgresql-client`로 클라이언트를 설치한다. DB 인프라가
없다면 Compose/migration을 임의로 실행하는 대신 `RECOMMENDATION_SOURCE=files`로
원천 파일 모드를 사용한다.

중간 백엔드는 EC2의 FastAPI 주소로
`POST /internal/recommendations`를 호출한다. 현재 중간 백엔드와 위험 사이렌은
이 저장소에 구현되어 있지 않다.

기본 추천 소스는 PostgreSQL(`source=db`)이며, 데이터 소스와 LLM 실행 모드는 FastAPI 서버 환경변수로만 설정한다. 배포 전 `services/recommendation-api/.env.example`을 같은 디렉터리의 `.env`로 복사한다. 추천 API는 루트 `.env`를 자동으로 읽지 않으므로 중간 백엔드의 `DATABASE_URL`과 섞이지 않는다. LLM은 기존 환경변수(`LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`)를 사용하고, 개발·테스트에서는 `RECOMMENDATION_LLM_MODE=offline`으로 외부 호출 없이 결정론적 폴백을 실행할 수 있다.

대용량 원천 데이터는 서비스 커밋에 포함하지 않고 외부 볼륨으로 제공한다. 볼륨의 절대 경로를 `RECOMMENDATION_DATA_ROOT`에 지정하며, 비워 두면 로컬 개발에서는 `data/` 디렉터리가 있는 저장소 경로를 자동 탐색한다.

## 주요 엔드포인트

### `GET /api/industries` · `GET /api/regions?sigungu=송파구`

와이어프레임의 업종·지역 선택지를 제공한다. `/api/regions`는 `sigungu`를 생략하면 자치구 목록을, 전달하면 해당 자치구의 행정동 목록을 `sigungu`·`dong` 배열로 반환한다.

### `POST /internal/recommendations`

중간 백엔드가 호출하는 핵심 파이프라인 REST API다. `/api/recommendations`는 기존 호출 호환용 alias다. FastAPI는 `region`, `industry_code`, `special_condition_text`를 자체 해석하지 않고 기존 파이프라인에 전달한다. `quarter`, `source`, `limit`, `include_poi`, `include_poi_context`, `include_news`, `llm_mode`는 호출자가 보내지 않으며 FastAPI 배포 환경변수로 관리한다. `limit`은 현재 서비스 기본값을 고정하는 정책이며, 화면별 N이 필요해지는 시점에 서버 상한을 둔 선택 필드로 별도 계약을 추가한다.

```json
{
  "request_id": "backend-request-123",
  "region": {
    "sido": "서울특별시",
    "sigungu": "송파구",
    "dong": "잠실동"
  },
  "industry_code": "CS100010",
  "special_condition_text": "아침 손님이 많은 커피 매장, 월세 300만원 이하, 20평 이상, 주차 가능"
}
```

응답은 `request_id`, `run_id`, `status`, `summary`, `candidates`, `explanations`, `input_interpretation`을 반환한다. LLM이 생성한 추가 추론은 `inference_hypotheses`로 분리되어 `status=unverified`로 표시된다.

특별조건의 현재 판정 경계도 명시한다. 개별 매물 데이터가 없는 월세·보증금·면적·주차 조건은 `unsupported_conditions`와 `missing_features`에 남기고, 후보 `fit_tier`는 `추천`으로 올리지 않는다. 고객층·영업시간·영업 방식은 현재 입력 해석 결과에는 보존되지만 후보 필터·등급에는 연결하지 않으므로, 화면에서는 “해석·설명 전용”으로 표시한다. 이 세 조건을 FC-03·04·05 기반 판정에 반영하는 것은 별도 계약 변경이다.

실행 정책은 FastAPI 서버 환경변수로 설정한다.

```text
RECOMMENDATION_QUARTER=20261
RECOMMENDATION_SOURCE=db
RECOMMENDATION_LLM_MODE=auto
RECOMMENDATION_DEFAULT_LIMIT=5
RECOMMENDATION_REQUEST_TIMEOUT_SECONDS=180
RECOMMENDATION_READINESS_TIMEOUT_SECONDS=3
RECOMMENDATION_MAX_CONCURRENT=4
```

`/internal/recommendations`와 호환 alias는 내부 백엔드 전용이다. 실제 HTTP
호출에서는 `INTERNAL_API_TOKEN`을 서버에 설정하고 같은 값을
`X-Internal-Token` 헤더로 전달해야 한다. 토큰이 없거나 일치하지 않으면
파이프라인을 실행하지 않는다. 개별 요청은 `RECOMMENDATION_REQUEST_TIMEOUT_SECONDS`
를 넘으면 504로 종료된다.
동시 실행 수가 `RECOMMENDATION_MAX_CONCURRENT`를 초과하면 429를 반환한다.

`GET /healthz`는 프로세스 생존만 확인하고, `GET /readyz` 성공 응답은
`{"ok": true}`만 반환한다. DB readiness probe는 동기 `psql` 호출을 threadpool에서
짧은 전용 timeout으로 실행하며 DB 이름·주소를 응답에 넣지 않는다.

파이프라인 오류의 HTTP 계약은 다음과 같다.

| 유형 | 상태 | 응답 code | 예시 |
| --- | ---: | --- | --- |
| 입력·지역·업종·확인 질문 오류 | 422 | `invalid_request` 또는 `confirmation_required` | 업종 미선택 |
| DB·필수 LLM·원천 데이터 장애 | 503 | `dependency_unavailable` | PostgreSQL 조회 실패 |
| Evidence 내부 검증 실패 | 500 | `internal_validation_error` | RAG schema 검증 실패 |

상세 예외 원인, DB 접속 대상, 파일 경로는 서버 로그에만 남기고 HTTP 응답에는
일반화된 메시지만 반환한다.

`confirmation_required=true`가 되면 HTTP 422와 함께 다음 형태의 `detail`을 반환한다.

```json
{
  "detail": {
    "code": "confirmation_required",
    "message": "입력 확인이 필요합니다: ...",
    "request_id": "backend-request-123",
    "questions": ["창업하려는 업종을 10개 업종 중 하나로 선택해 주세요."]
  }
}
```

분석 결과는 `output/recommendation_api_runs/<run_id>/`에 JSON 아티팩트로도 보존된다. API 응답에는 서버의 절대 경로를 포함하지 않는다.

## 운영 경계

- 지역 선택값은 UI 입력을 그대로 사용하며 LLM이 지역을 바꾸도록 허용하지 않는다.
- LLM은 업종·조건·읽기 전용 분석 계획의 제안자일 뿐이며, 허용 목록 검증 후에만 파이프라인을 진행한다.
- planner의 `clarification_questions`와 `unsupported_conditions`는 결정론적 파서가 생성한 값만 사용한다. 원격 LLM의 동일 필드는 확인 중단이나 후보 등급에 영향을 주지 않는다.
- `source=db`의 데이터 조회와 후보/Evidence 생성은 `services/recommendation-api/recommendation/pipeline.py`가 수행한다. `services/recommendation-api/scripts/recommendation_pipeline.py`는 CLI 호출을 위한 forwarding entrypoint다.
- 후보가 만들어진 뒤 설명 LLM의 `reasons`, `counter_evidence`, `context_notes`, `missing_features`는 후보의 결정론적 원문 항목만 복사할 수 있고, `summary`도 후보 등급 기반 canonical 문장만 허용한다. 추가 주소·수치·분기·매물·공실률·성공확률·수익률·인과관계는 `inference_hypotheses`에 `status=unverified`로만 담을 수 있으며, 후보 등급·정렬·하드 조건과 관측 Evidence에는 사용하지 않는다.
- `CORS_ALLOW_ORIGINS`를 콤마로 설정한 경우에만 CORS 미들웨어를 활성화한다.
