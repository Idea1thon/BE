---
name: 수행 작업 (Task)
about: 추천 파이프라인 DB 접속 Azure 전환 및 OpenAI(gpt-5.6-luna) 연결
title: "[TASK] 추천 파이프라인 DB 접속 Azure 전환 및 OpenAI GPT API 연결"
labels: ''
assignees: ''
type: Task
---

이슈 #25. `recommendation_pipeline.py`(`recommendation/pipeline.py`)의 DB 소스를 팀 공유
Azure PostgreSQL 로 고정하고, RAG 근거·입력 해석 단계에 호스티드 LLM(gpt-5.6-luna)을 연결한다.
LLM 은 설명·해석 보조만 담당하고 점수·판정·정렬·하드조건은 결정론적 계약을 그대로 유지한다.

## 1. DB 접속 — Azure 전환 (구현 완료, 설정만 필요)

`--source db`(기본)는 `.env` 의 `DATABASE_URL` 을 그대로 사용한다. Azure 전용 코드 경로는
없다 — 접속 문자열만 Azure 를 가리키면 된다.

- `services/recommendation-api/.env.example` 의 `DATABASE_URL` 이 Azure(`pipeline_app`,
  `sslmode=require`)를 가리킨다. 로컬 Docker(55432)는 주석의 폴백 문자열로 교체.
- `serving_db._dsn_args()` 가 URL 에서 host·port·user·dbname 을 뽑고,
  `_subprocess_env()` 가 `?sslmode=require` → `PGSSLMODE`, 비밀번호 → `PGPASSWORD` 로 넘긴다.
  psql 드라이버 설치 불필요(기존 `COPY ... TO STDOUT` 패턴 유지).
- 적재 범위: 서울 전체 25개 구(2026-09-06 이관). `DbSource.layers()` 는 적재된
  `location.area` 행에서만 `sigungu_by_prefix` 를 만들므로, 미적재/미지원 시군구를
  요청하면 `resolve_region()` 이 `PipelineInputError("시군구를 확인할 수 없습니다: …")`
  → API 422(`invalid_request`) 로 명확히 거절한다(크래시 아님). 서울 외 `sido` 도 동일.
  (`PipelineDependencyError` "행정동 데이터에서 시군구가 비어 있음" 경로는 시군구는
  이관됐는데 행정동 행만 0 인 부분 이관 상태에서만 발동 — 전 25구 이관돼 현재는 미발동.)
- 행정동 필터는 `ADSTRD_CD` 앞 5자리(= `SIGNGU_CD`) 기준이다
  (`resolve_region`: `sigungu_by_prefix.get(r.code[:5]) == request.sigungu`).
  FileSource·DbSource 가 동일 규칙을 쓰며 `PipelineInvarianceTests`·
  `DbSourcePopulationParityTests` 가 송파구/잠실동 요청으로 이 경로를 커버한다.

### 세부 작업
- [x] `--source db` 커넥션이 Azure 접속 정보(sslmode 포함)를 쓰도록, Docker 는 폴백 유지
- [x] 접속 정보·시크릿을 `.env` 로 분리(`.env.example` 갱신), 커밋 금지 확인
      (`.gitignore`: `.env`, `.env.*`, `!.env.example`, `.api_budget.json`)
- [x] Azure 적재 범위 확인 → 미적재 시군구 요청 시 `PipelineInputError`(422)로 거절
- [x] 행정동 필터가 코드 앞 5자리 기준으로 동작하는지 검증(기존 통합 테스트가 커버)

## 2. LLM 연결 — gpt-5.6-luna (구현 완료)

`llm_runtime.OpenAICompatibleJsonClient` 는 provider-neutral OpenAI 호환
chat-completions 클라이언트다(stdlib 만 사용). 새 provider 모듈을 추가하지 않고
이 클라이언트에 자격증명을 넣어 연결한다.

`gpt-5.6-luna` 같은 최신 OpenAI 모델은 `max_tokens` 를 거부하고(`max_completion_tokens`
필요) `temperature` 는 기본값만 허용한다. `generate_json` 은 최신 파라미터 형식으로
먼저 호출하고, 파라미터 관련 400 이면 구형 형식(`max_tokens`+`temperature=0`)으로
한 번 재시도한다 — 최신 모델·구형 OpenAI 호환 서버 양쪽에서 동작한다.

또 추론 모델은 `max_completion_tokens` 안에서 추론 토큰을 먼저 소비하므로 한도가
낮으면 content 없이 잘린다. `LLM_MAX_OUTPUT_TOKENS` 기본값을 6000 으로 올렸고
(설명 카드 JSON ~2k + 추론 여유), `finish_reason=length` + 빈 content 는 "JSON 아님"
대신 잘림 오류로 명확히 보고한다(→ 호출부 폴백).

`validate_card` 의 배열 길이 상한은 후보 배열 자체 크기에 맞춘다(예전 고정 12·500).
#28·#29 로 `context_notes` 가 12개를 넘게 돼(인구 FC-03~06·도시계획 FC-51/52),
LLM 이 지시대로 verbatim 복사해도 12 상한에 걸려 항상 폴백되던 문제.

- `.env`:
  - `LLM_API_URL=https://api.openai.com/v1` (프록시/게이트웨이면 그 URL)
  - `LLM_API_KEY=<키>` — 이 변수에 실제 키를 넣는 것으로만 활성화된다.
    셸에 흔히 떠 있는 `OPENAI_API_KEY` 는 인식하지 않는다(offline 로 알던 환경에서
    실호출·과금이 켜지는 것을 막기 위함).
  - `LLM_MODEL=gpt-5.6-luna`
  - `RECOMMENDATION_LLM_MODE=auto` — 셋이 모두 있으면 LLM, 하나라도 없으면 폴백.
- 연결 지점(기존):
  - `plan_input()` — 자연어 업종·특별조건 구조화 + 읽기전용 분석계획 초안.
    숫자 조건(월세·보증금·면적)은 LLM 이 정규화한 값을 신뢰하고 `source_text`
    인용·라벨·단위·방향·범위만 검증(`_verified_numeric_condition`, 안 B).
    결정론적 파서는 LLM 비가용 시 폴백. `unsupported_conditions`·
    `confirmation_required` 는 LLM 이 못 건드린다.
  - `explain_candidates()` — 후보별 Evidence 설명 카드. `validate_card` 통과 실패 시
    해당 후보만 `template_card` 로 폴백.
- 실패·타임아웃·비용 한도 → 폴백:
  - HTTP/네트워크/타임아웃/JSON 파싱 실패 → `LLMRuntimeError` → 호출부가 template/
    deterministic 폴백(`llm_mode="required"` 일 때만 예외 전파).
  - 비용 한도: `LLM_MAX_CALLS_PER_RUN`(기본 `0` = 무제한, opt-in). `run_pipeline` 이
    요청마다 `reset_call_budget()` 로 스레드별 카운터를 0 으로 만들고, 캡을 넘으면
    `generate_json` 이 `LLMRuntimeError` 를 던져 이후 호출이 폴백된다. 요청당 호출 수는
    `1(planner) + 후보 수(explanation)` 이므로 캡은 그보다 크게 잡는다. `required`
    모드에서는 캡을 무시한다(자체 비용캡을 503 으로 보고하지 않도록).
- LLM 미설정·`--source files` 환경에서도 파이프라인은 그대로 동작한다
  (`test_llm_pipeline.py` offline 경로, `--source files` 회귀).

### 세부 작업
- [x] OpenAI 호환 클라이언트에 gpt-5.6-luna 연결 (`LLM_API_URL`/`LLM_API_KEY`/`LLM_MODEL`)
- [x] RAG Evidence 설명 카드 생성부에 LLM 호출 연결 (결정론적 계약 유지)
- [x] 호출 실패·타임아웃·비용 한도(`LLM_MAX_CALLS_PER_RUN`) 시 폴백
- [x] `--source files` 및 LLM 미설정 환경 회귀 확인

## 테스트

`services/recommendation-api` 에서:

- `tests/test_llm_pipeline.py::LLMRuntimeConfigTests` (신규) — `LLM_API_KEY` 만 인식(셸
  `OPENAI_API_KEY` 무시), `LLM_MAX_CALLS_PER_RUN` 초과 시 예산 예외 + `reset_call_budget`
  복구, `0` = 무제한, `required` 모드는 캡 무시, 예산 소진 시 `explain_candidates` 가
  template 로 degrade(예외 아님).
- 기존 offline/`required`/폴백 경로 전부 유지.

## 참고 사항

- LLM 은 입력 해석·리뷰 분류 보조·Evidence 설명만. 점수·등급·정렬·하드조건은 결정론적.
- pgvector 미설치 → 임베딩 기반 RAG 검색은 별도 과제(현재 `rag_tools` 는 SQL 화이트리스트 검색).
- 관련 파일: `recommendation/{llm_runtime,llm_explanation,llm_input_planner,pipeline,serving_db}.py`,
  `.env.example`
- 관련 메모: `serving_pipeline_db_source`, `azure_team_db_ideaton`
