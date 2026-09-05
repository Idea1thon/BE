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
                └─ LLM retrieval_requests 검증 → allowlisted read-only DB tool 실행
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

`source=db`는 PostgreSQL/PostGIS가 이미 실행 중이고 기본 스키마·핵심 데이터
이식이 완료된 환경을 전제로 한다. Compose와 기본 스키마 migration은 DB 인프라
저장소의 배포 절차로 준비하고, 이 서비스에는 서빙 누락 테이블 보완 DDL·스크립트를
포함한다. DB를 사용할 때는 FastAPI 호스트에 `psql` 클라이언트도 필요하다.

누락된 서빙 테이블과 최신 파생 데이터를 보완할 때는
`scripts/supplement_serving_tables.py`를 실행한다. 이 스크립트는
`location.area_store_totals`를 `data/점포/2026년`의 전 업종 행에서 집계하고,
`context.commercial_building`에 `data/건축물대장/상가건물_서울.csv`를 upsert한다.

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

중간 백엔드가 호출하는 핵심 파이프라인 REST API다. `/api/recommendations`는 기존 호출 호환용 alias다. FastAPI는 `region`, `industry_code`, `special_condition_text`를 자체 해석하지 않고 기존 파이프라인에 전달한다. `limit`은 선택 요청 필드이며 `1~50` 범위에서만 허용한다. 생략하면 `RECOMMENDATION_DEFAULT_LIMIT`을 사용하고, 응답의 `request.limit`과 `summary.applied_limit`에 실제 적용값을 기록한다. `quarter`, `source`, `include_poi`, `include_poi_context`, `include_news`, `llm_mode`는 호출자가 보내지 않으며 FastAPI 배포 환경변수로 관리한다.

요청 예시:

```json
{
  "request_id": "backend-request-123",
  "region": {"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동"},
  "industry_code": "CS100010",
  "special_condition_text": "월세 300만원 이하",
  "limit": 3
}
```

`limit`이 `0` 이하 또는 `51` 이상이면 요청 계약 위반으로 422를 반환한다.

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

특별조건의 현재 판정 경계도 명시한다. 개별 매물 데이터가 없는 월세·보증금·면적·주차 조건은 `unsupported_conditions`와 `missing_features`에 남기고, 후보 `fit_tier`는 `추천`으로 올리지 않는다. 자연어 요구는 `conditions`에 억지로 매핑하지 않고 `input_interpretation.preferences`의 별도 계약으로 보존한다. 예를 들어 “지하철역에서 장사하고 싶음”은 `location_preferences[{type: "near_anchor", anchor_type: "station"}]`이 된다. 현재는 명시적 위치 선호를 Evidence 등급 변경 없이 같은 등급 안에서 우선 노출하며, 고객층·영업시간·영업 방식·경쟁 회피는 해석·설명용으로 보존한다. 이 계약들을 FC 기반 판정에 반영하는 것은 각 데이터 연결을 확인한 뒤 별도 정책으로 추가한다.

LLM은 임의 SQL을 생성하거나 실행하지 않는다. 입력 해석 결과의 `retrieval_requests`에는
`search_region_evidence`와 `sales/stores/flow/change` dimension만 요청할 수 있다.
서버가 지역·업종·분기를 요청 DTO에서 바인딩하고, 허용된 테이블·조인·행 수로 SQL을
생성한다. 실행 결과는 `summary.retrieval`과 `input_interpretation.retrieval`에
출처가 붙은 보조 컨텍스트로 남으며, 후보 Evidence·등급·정렬을 직접 덮어쓰지 않는다.
LLM 설명 단계는 이 컨텍스트를 참고할 수 있지만 관측 설명에는 후보 Evidence만 사용할
수 있다. `source=files` 또는 오프라인 모드에서는 도구 호출을 실행하지 않고 skip으로
기록한다.

실행 정책은 FastAPI 서버 환경변수로 설정한다.

```text
RECOMMENDATION_QUARTER=20261
RECOMMENDATION_SOURCE=db
RECOMMENDATION_LLM_MODE=auto
RECOMMENDATION_SEED_MODE=buildings
RECOMMENDATION_DEFAULT_LIMIT=5
RECOMMENDATION_REQUEST_TIMEOUT_SECONDS=180
RECOMMENDATION_READINESS_TIMEOUT_SECONDS=3
RECOMMENDATION_MAX_CONCURRENT=4
RECOMMENDATION_CAPACITY_RETRY_AFTER_SECONDS=10
```

`/internal/recommendations`와 호환 alias, 실행 결과 조회, `/api/industries`, `/api/regions`는
내부 백엔드 전용이다. 실제 HTTP 호출에서는 `INTERNAL_API_TOKEN`을 서버에
설정하고 같은 값을 `X-Internal-Token` 헤더로 전달해야 한다. 다섯 엔드포인트는
공통 FastAPI dependency에서 토큰을 검증하므로, 호출 컨텍스트가 바뀌거나
라우트 함수가 직접 호출되어도 실제 HTTP 경로의 인증이 생략되지 않는다.
토큰이 없거나 일치하지 않으면 파이프라인을 실행하지 않는다. `healthz`와
`readyz`만 프로세스·의존성 상태 확인을 위해 공개한다.

개별 요청은 `RECOMMENDATION_REQUEST_TIMEOUT_SECONDS`를 넘으면 504로 종료된다.
이때 응답의 `run_id`와 `status_url`로 같은 실행을 조회할 수 있다. 동시 실행
수가 `RECOMMENDATION_MAX_CONCURRENT`를 초과하면 429를 반환하며, 클라이언트가
재시도 간격을 정할 수 있도록 `Retry-After`를 함께 보낸다. 429의 값은
`RECOMMENDATION_CAPACITY_RETRY_AFTER_SECONDS`(기본 10초)로 설정한다. 타임아웃
이후에도 실행 중인 워커가 슬롯을 점유할 수 있으므로 즉시 재시도하지 않아야 한다.

### `GET /internal/recommendations/{run_id}`

타임아웃된 요청의 동일 실행 결과를 조회하는 내부 백엔드용 endpoint다. POST가
504를 반환해도 파이프라인 워커는 계속 실행하고 결과를 해당 `run_id` 디렉터리에
보존한다. 조회 결과는 다음과 같다.

- 완료 전: HTTP 202와 `status=running`
- 완료 후: HTTP 200과 원래 추천 응답과 동일한 `RecommendationApiResponse`
- 실행 실패: HTTP 422/503/500과 저장된 `detail` 오류. 해당 실행은 종료됐으므로 폴링을 중단한다.
- 실행을 찾을 수 없음: HTTP 404

조회에도 `X-Internal-Token`이 필요하다. 504의 `detail.status_url`을 **10초 간격**으로
GET 조회하고, 202이면 같은 URL을 다시 조회한다. 504에는 `Retry-After`를 보내지
않으며 동일 요청을 POST로 다시 실행하지 않는다. 429의 `Retry-After`는 접수되지
않은 POST의 재시도 간격으로, 진행 중 실행의 조회 간격과는 별개다.

완료 응답은 `api-response.json`에서 읽고 동일한 응답 모델로 다시 검증한다.
실행 상태와 API 응답 파일은 원자적으로 기록해 폴링 중 부분 파일을 읽지 않도록 한다.
완료 결과가 저장돼 있으면 상태 기록이 `running`으로 남아도 완료 결과를 반환한다.
스레드풀에서 실행을 기다리는 작업도 HTTP 타임아웃 이후 유지하며, 작업이 끝날 때
동시성 슬롯을 반환한다.

180초는 HTTP 응답 대기 제한의 기본 설정이며 실측 실행 시간이나 워커 종료 기한이
아니다. auto/offline별 실제 실행 시간은 DB·LLM 환경에서 별도 측정해야 한다.
중간 백엔드는 사용자 대기 상한을 별도로 정해야 하며, 이 API는 실행 취소를 지원하지
않는다. 최초 POST는 완료 또는 504까지 기다리므로 즉시 run_id를 반환하는 작업
접수 API는 아니다.

실행 파일은 `RECOMMENDATION_API_OUT_ROOT`에 저장된다. 여러 인스턴스로 분산하면
같은 저장소를 공유하거나 같은 인스턴스로 조회를 라우팅해야 한다. 완료 결과는 파일이
보존되는 동안 재시작 후에도 조회할 수 있지만, 재시작으로 중단된 작업은 자동 재개되거나
실패로 전환되지 않아 `running`으로 남을 수 있다.

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

`confirmation_required`의 처리 주체는 중간 백엔드와 UI다. 중간 백엔드는
`questions[]`를 사용자에게 보여주고 답을 반영한 새 요청을 보내며, 이 API는
질문을 일반 검증 오류로 눌러서 바꾸거나 임의로 입력을 제한하지 않는다.

분석 결과는 `output/recommendation_api_runs/<run_id>/`에 JSON 아티팩트로도 보존된다. API 응답에는 서버의 절대 경로를 포함하지 않는다.

## 운영 경계

- 지역 선택값은 UI 입력을 그대로 사용하며 LLM이 지역을 바꾸도록 허용하지 않는다.
- LLM은 업종·조건·자연어 preference·읽기 전용 검색 계획의 제안자일 뿐이며, 허용 목록 검증 후에만 파이프라인을 진행한다. preference는 `source_text`가 없거나 허용되지 않은 anchor를 사용하면 폐기한다. retrieval 요청은 임의 SQL·테이블·조인을 받지 않는다.
- planner의 `clarification_questions`와 `unsupported_conditions`는 결정론적 파서가 생성한 값만 사용한다. 원격 LLM의 동일 필드는 확인 중단이나 후보 등급에 영향을 주지 않는다.
- `source=db`의 데이터 조회와 후보/Evidence 생성은 `services/recommendation-api/recommendation/pipeline.py`가 수행한다. `services/recommendation-api/scripts/recommendation_pipeline.py`는 CLI 호출을 위한 forwarding entrypoint다. 기본 seed는 `context.commercial_building`의 건축물대장 건물 centroid이며, `RECOMMENDATION_SEED_MODE=anchors`는 기존 역·아파트·POI·생성점 seed로, `hybrid`는 두 모집단을 함께 사용한다. 건축물대장 건물은 실제 임대 매물·공실·호실이 아니므로 `상가건물_인근` 후보는 조건부 검토 상한을 가진다.
- 후보가 만들어진 뒤 설명 LLM의 `reasons`, `counter_evidence`, `context_notes`, `missing_features`는 후보의 결정론적 원문 항목만 복사할 수 있고, `summary`도 후보 등급 기반 canonical 문장만 허용한다. 추가 주소·수치·분기·매물·공실률·성공확률·수익률·인과관계는 `inference_hypotheses`에 `status=unverified`로만 담을 수 있으며, 후보 등급·정렬·하드 조건과 관측 Evidence에는 사용하지 않는다.
- `CORS_ALLOW_ORIGINS`를 콤마로 설정한 경우에만 CORS 미들웨어를 활성화한다.
