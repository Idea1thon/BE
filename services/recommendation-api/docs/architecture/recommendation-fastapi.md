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

EC2에서는 PostgreSQL만 Docker Compose로 실행하고, FastAPI는 저장소의 가상환경에서
직접 실행한다. FastAPI 프로세스가 파이프라인을 같은 프로세스 안에서 호출하고,
DB에는 Docker가 공개한 `127.0.0.1:55432`로 접근한다.

```bash
docker compose up -d db
cd services/recommendation-api
../../.venv/bin/pip install -r requirements-api.txt
../../.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
docker compose ps
```

중간 백엔드는 EC2의 FastAPI 주소로
`POST /internal/recommendations`를 호출한다. 현재 중간 백엔드와 위험 사이렌은
이 저장소에 구현되어 있지 않다.

기본 추천 소스는 PostgreSQL(`source=db`)이며, 데이터 소스와 LLM 실행 모드는 FastAPI 서버 환경변수로만 설정한다. 배포 전 `services/recommendation-api/.env.example`을 같은 디렉터리의 `.env`로 복사한다. 추천 API는 루트 `.env`를 자동으로 읽지 않으므로 중간 백엔드의 `DATABASE_URL`과 섞이지 않는다. LLM은 기존 환경변수(`LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`)를 사용하고, 개발·테스트에서는 `RECOMMENDATION_LLM_MODE=offline`으로 외부 호출 없이 결정론적 폴백을 실행할 수 있다.

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
RECOMMENDATION_MAX_CONCURRENT=4
```

`/internal/recommendations`와 호환 alias는 내부 백엔드 전용이다. 실제 HTTP
호출에서는 `INTERNAL_API_TOKEN`을 서버에 설정하고 같은 값을
`X-Internal-Token` 헤더로 전달해야 한다. 토큰이 없거나 일치하지 않으면
파이프라인을 실행하지 않는다. 개별 요청은 `RECOMMENDATION_REQUEST_TIMEOUT_SECONDS`
를 넘으면 504로 종료된다.
동시 실행 수가 `RECOMMENDATION_MAX_CONCURRENT`를 초과하면 429를 반환한다.

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
- `source=db`의 데이터 조회와 후보/Evidence 생성은 `services/recommendation-api/service/recommendation/pipeline.py`가 수행한다. `services/recommendation-api/scripts/recommendation_pipeline.py`는 CLI 호출을 위한 forwarding entrypoint다.
- 후보가 만들어진 뒤 설명 LLM은 관측 설명 필드와 `inference_hypotheses`를 분리한다. 추가 주소·수치·분기·매물·공실률·성공확률·수익률·인과관계는 후자에 `status=unverified`로만 담을 수 있으며, 후보 등급·정렬·하드 조건과 관측 Evidence에는 사용하지 않는다.
- `CORS_ALLOW_ORIGINS`를 콤마로 설정한 경우에만 CORS 미들웨어를 활성화한다.
