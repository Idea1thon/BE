# 출처 기반 입력 해석과 추천 설명

추천 결과의 관측 문장을 그대로 복사해야 했던 제약을 완화한다. LLM이 문장을 재서술하거나 조회한 지역 통계를 배경 설명에 활용할 때 출처를 인용하고 검증한다.

## 실행 흐름

1. 입력 planner는 월세·임대료·보증금·면적 조건을 `{value, source_text}`로 제안한다. **value(LLM이 정규화한 숫자)는 신뢰하고**, 그 조건이 사용자 입력에 실제로 있었는지만 검증한다(안 B): `source_text`가 원문에 그대로 존재하고, 금액은 라벨(월세·보증금 등)+`원`, 면적은 단위(평·㎡)를 포함하며, 부정 표현이 없고, 상하한 방향이 key와 맞고, 값이 상식 범위 안일 것. 한국어 숫자 문법을 서버가 다시 파싱하지 않으므로 `임대료 삼백만 원 이하면`·`보증금 3천만원 이하` 같은 표현도 처리된다. 인용이 없는 bare 숫자는 조건이 되지 못한다. 결정론 파서는 LLM 비가용 시 폴백일 뿐, LLM 경로에서는 숫자 baseline을 쓰지 않는다. UI 업종 우선권과 "실제 매물 데이터 없음" 경고는 유지한다.
2. 질문의 명시된 지표를 공통 질문 계약으로 만들고, 기존 엔진이 선택한 최종 후보 상권을 읽기 전용 검색 대상으로 지정한다. 지역·업종·기간 정책은 서버가 결정한다. 결과에서 출처·지역·기간·단위가 확인되는 행만 `retrieval_evidence`로 정규화한다. 결측값을 0으로 바꾸지 않는다. 행정동 검색에는 선택 시군구 조건도 적용한다.
3. 후보 등급·정렬은 기존 엔진에서 계산한다. 설명 모델은 서버의 `explanation_sources`를 사용하며 재서술 문장마다 `citations`를 반환한다.
4. 서버는 각 재서술 문장이 허용된 bucket의 출처를 인용했는지, 인용 출처에 수치가 있는지 확인한다. 인용 규칙은 (a) 같은 bucket, (b) 후보 자체 지표인 `candidate-evidence:*`는 모든 관측 bucket에서, (c) `summary`는 카드의 다른 관측 bucket에서도 허용하며, `retrieval-*` 지역 검색 근거는 `context_notes`에서만 허용한다. 이어 별도 모델 호출이 단위·기간·지역·수치 대상·부정·불확실성·추가 주장의 일치 여부를 문장별로 검토하고, `supported: true` 응답이 있는 문장만 채택한다.
5. 생성 카드의 기본 형식(후보 ID, 요약, 관측 문자열 배열, 가설 형식)을 문장 검토 전에 검사한다. 형식 오류는 `auto`에서 템플릿으로 복귀하고 `required`에서 실패한다. 검토를 통과하지 못한 재서술 문장은 제거하고, 재서술 summary는 템플릿 문장으로 되돌린다. 정리 후 관측 목록이 모두 비면 같은 방식으로 복귀/실패한다. 일부 목록이 남으면 서버의 반대근거·결측 원문을 보존하고 복구 여부를 표시한다. 검토 호출 자체가 실패하거나 예산을 초과해도 `auto`는 템플릿으로 돌아가고 `required`는 실패를 반환한다. 원문을 그대로 사용하는 카드는 추가 검토 호출이 없다.

## 응답과 참조

기존 `cards`의 문자열 배열은 유지한다. 다음 필드는 추가된다.

- 카드 `citations`: 출력 위치(`summary`, `reasons:0`, `context_notes:0` 등) → 출처 ID 배열.
- `sources_by_candidate`: 후보 ID → 출처 ID → `{bucket, text}`. 출처를 확인하는 데 사용한다.
- `retrieval_evidence`: 안정적인 근거 ID와 값·단위·분기·공간 단위·업종·원천 테이블.

`retrieval-*` 지역 검색 근거는 `context_notes`에서만 인용한다. 후보 건물의 매출·성공 가능성으로 승격하거나 긍정 근거로 이동시키지 않는다. `reasons`/`counter_evidence`/`missing_features` 재서술은 같은 bucket 출처 또는 후보 자체 지표(`candidate-evidence:*`)만 인용한다.

## 범위와 한계

의미 검토도 LLM이므로 사실성을 보장하는 증명이 아니다. 서버의 수치·출처 제한과 실패 시 템플릿 복귀를 함께 사용한다. 금액을 만원으로 바꾸는 등 임의의 단위 환산은 지원하지 않으며, 천 단위 쉼표 표기는 허용한다.

입력 조건의 숫자 계약과 업종 사전 제한은 유지한다. SQL은 sales/stores/flow/change/rent/vacancy/workplace_population 7종을 지원한다. 뉴스·도시계획의 신규 검색과 반복 검색 루프는 포함하지 않는다. 질문별 검색·비교의 기간 정책과 한계는 아래를 따른다.

설명 단계는 세 종류의 호출로 나뉜다. ① source rerank는 최대 8개 후보 단위 batch → `ceil(후보 수/8)`. ② 카드 생성은 후보당 1회 → `후보 수`. ③ 재서술 문장의 의미 검증은 **모든 후보의 claim을 한 번에 묶어 1회** 호출한다(결정론 인용·기간·수치 게이트를 통과한 claim이 하나도 없으면 이 호출도 생략). 따라서 질문이 있는 요청의 설명 단계 통상 호출 수는 `ceil(후보 수/8) + 후보 수 + (검증할 재서술이 있으면 1)`이다. planner와 후보 선택 rerank는 각각 별도 1회이며 공급자 호환 재시도는 별도일 수 있다. `LLM_MAX_CALLS_PER_RUN`과 API 시간 제한을 운영 환경에 맞게 설정해야 한다. `calls_succeeded`는 종전처럼 채택된 설명 카드 수이며 실제 HTTP 호출 횟수가 아니다. 실제 호출별 지연·토큰·재시도는 `run-manifest.json`의 `llm_telemetry`(단계별 집계·p50/p95)에 남는다.

## 검증

`test_input_source_grounding.py`, `test_rag_grounding.py`, `test_grounded_explanation.py`는 입력 인용·숫자 조작·검색 근거 정규화·재서술 승인·실패 복귀를 외부 호출 없이 검증한다. 실제 Azure DB와 배포 LLM에 대한 종단 품질·지연 검증은 별도로 필요하다.

## 질문 관련도 재정렬

원문과 공백·유니코드만 정리한 질문, 확정 지역·업종·조건, 원문 인용이 있는 선호를 `query_context`로 전달한다. 부정 조건을 삭제하거나 질문을 임의로 확장하지 않는다. 4000자를 넘는 정규화 입력은 잘라서 사용하지 않으며 관련도 정렬은 기존 순서로 복귀한다.

기존 후보 Evidence의 모든 지표도 `candidate-evidence:<index>`로 검색 근거 목록에 포함한다. 따라서 기존 파이프라인에서 읽은 인구·임대·계획·뉴스 집계 등을 질문 관련도 정렬과 배경 설명에 사용할 수 있다. 이는 모든 DB 테이블에 신규 검색 도구가 생겼다는 뜻은 아니다. 전체 데이터 계열과 미연결 경로는 `docs/rag-data-coverage.md`를 참조한다.

단어·한글 2글자 토큰 겹침으로 최대48개 근거를 추리고, 요청 내 짧은 ID(`E01` 등)로 매핑한다. LLM에는 상위 최대32개 ID만 관련도 순서로 선택하도록 요청한다. 서버는 유효한 ID의 순서를 보존하고 중복은 첫 항목만 사용하며, 알 수 없는 ID와 문자열이 아닌 값은 제외한다. 부족한 순위는 기존 토큰 관련도 순서로 보충한다. 유효한 ID가 하나도 없거나 응답 형식/호출이 실패한 경우에만 전체 토큰 순서로 복귀한다. 짧은 ID는 요청 내부에서만 사용하며 반환 출처 ID와 인용 계약은 바뀌지 않는다. 어휘 관련도와 주제 가산점으로 선택한 일반 출처 최대32개와 필수 반대근거·결측·등급 요약만 설명에 전달한다. 후보 지표와 SQL 근거도 일반 출처 상한에 포함하며 무조건 재포함하지 않는다. 필수 경고가 많으면 전체 전달 수는32개보다 많을 수 있다. 전체 출처는 응답에 보존하고 선택 목록·정렬 방식은 `relevance_by_candidate`에 기록한다. 최종 문장도 같은 종류 안에서 관련 근거 순서로 정렬하고 인용 위치를 함께 갱신한다.

후보 점수·등급·후보 간 순위는 변경하지 않는다. 토큰 검색은 의미가 비슷한 다른 표현을 놓칠 수 있고, LLM 재정렬의 실제 품질은 질문별 평가가 필요하다. 벡터 검색/전용 cross-encoder는 추가하지 않았다. source rerank는 후보 최대8개씩 batch 호출하므로 질문이 있는 설명 단계의 source-rerank 호출 수는 `ceil(후보 수/8)`이다. 카드 생성은 후보 단위로 유지하고, 재서술 문장의 의미 검증은 모든 후보의 claim을 한 요청으로 묶어 1회만 호출한다(claim_id는 `<candidate_id>␟<bucket:index>` 로 네임스페이스; 후보별 슬라이스를 종전과 같은 결정론 pruning·진단에 적용). 검증 호출이 실패하면 기여 후보 전원이 종전처럼 폴백한다(`required` 모드는 예외 전파).

### 리랭킹 진단

`relevance_by_candidate`의 `mode`는 보정 없이 충분한 순위를 받은 `llm`, 유효한 일부 순위를 활용한 `llm_partial`, 전체 토큰 복귀인 `lexical`, 질문 없는 `unchanged`로 구분한다. 소비자는 이 값을 설명 카드의 `explanation_mode`와 구분해야 한다.

`diagnostics`는 요청한 상위 개수(`requested_count`), 실제 응답 배열 길이(`returned_count`), 중복 제거 후 유효 ID 수(`accepted_count`), 중복/미등록/타입 오류 수, 사전선별48개 중 반환되지 않은 수(`omitted_count`), 상위 개수를 채우기 위해 보충한 수(`backfilled_count`)를 제공한다. 상위32개만 요청하므로 omitted_count가 양수여도 정상이다. `failure_reason`은 `invalid_format`, `no_valid_ids`, `runtime_error` 또는 null이다. 모델 호출을 하지 않은 lexical/unchanged 경로는 실패를 뜻하지 않는다. 원문 모델 응답이나 사용자 자격증명은 진단에 기록하지 않는다.

### 설명 검증 전후 진단

`explanations.verification_by_candidate[candidate_id]`는 생성 초안과 최종 설명의 차이를 반환한다. 문장 단위 손실과 복구를 이 진단으로 확인한다. 일부 재서술이 삭제돼도 나머지 목록이 유효하고 원문 경고 복구가 필요 없으면 `explanation_mode=llm`, 오류 목록 `[]`일 수 있다. 관측 목록 전체 소실은 `empty_explanation`으로 복귀하며, 원문 경고·결측을 보충한 카드는 `mixed`로 표시한다. 추가 LLM 호출은 없다.

진단의 `content_status=unverified_draft`는 발췌문에 거부된 주장도 포함됨을 나타낸다. 진단을 사용자용 추천 문장, 관측 Evidence, 후속 검색 원천으로 사용하지 않는다. `supported`는 기존 검토기가 통과시켰다는 뜻이며 독립적인 사실 검증을 의미하지 않는다. 원시 응답 전체, 가설 배열, 프롬프트, 인증 정보를 별도로 수집하지 않는다.

| 필드 | 의미 |
| --- | --- |
| `generation_status` | `not_attempted`, `generated`, `invalid_card`, `runtime_error` |
| `final_mode`, `fallback_reason` | 최종 카드 모드와 전체 카드 복귀 사유. 사유는 `no_client`, `generation_error`, `draft_schema_invalid`, `verification_error`, `empty_explanation`, `card_validation_failed` 또는 null |
| `summary_reverted` | 생성된 문자열 요약이 템플릿과 달랐으나 최종적으로 템플릿 요약으로 돌아갔는지 여부. 처음부터 템플릿 요약을 생성한 경우 false |
| `draft_claim_count`, `rewritten_claim_count`, `verified_claim_count` | 초안 관측 항목 수(요약 포함), 문자열 재서술 수, 의미 검토까지 통과한 재서술 수. 검토 통과 후 전체 카드 fallback이 발생할 수도 있음 |
| `removed_claim_count` | 부분 정리로 제거된 목록 항목 수. 요약 복귀 및 전체 카드 fallback 수와 구분 |
| `verification_counts`, `disposition_counts` | 전체 초안의 검사 결과·최종 처리별 개수. 문장 상세가 잘려도 전체 집계는 유지 |
| `draft_citation_count`, `final_citation_count` | 초안·최종 인용 배열의 문자열 출처 참조 발생 횟수. 고유 출처 수나 인용 문장 수가 아님 |
| `draft_retrieval_citation_count`, `final_retrieval_citation_count` | 위 참조 중 `retrieval-` 접두사가 있는 횟수. 초안 값에는 미등록 ID나 잘못된 위치의 인용도 포함되므로 출처 유효성을 뜻하지 않음 |
| `claims`, `claims_truncated_count` | 초안 순서의 항목 상세 최대100개와 생략 항목 수 |

각 `claims` 항목은 다음 필드를 갖는다.

- `draft_position`: 생성 당시 위치. 예: `context_notes:2`.
- `final_position`: 부분 삭제와 관련도 재정렬을 모두 거친 최종 카드 위치. 유지되지 않았으면 null. 같은 문장이 반복되더라도 검사 결과·인용·발생 순서로 대응한다.
- `verification`: `verbatim`(원문 복사), `supported`, `missing_citations`, `invalid_citations`, `unknown_source`, `disallowed_source_bucket`, `unsupported_number`, `unsupported_period`, `semantic_rejected`, `missing_verdict`, `invalid_verdict`, `verifier_runtime_error`, `invalid_claim_type`, `not_checked` 중 하나. 로컬 검사에서는 첫 실패 사유를 기록한다. `semantic_rejected`는 명시적 false, `missing_verdict`는 항목 누락, `invalid_verdict`는 형식 오류이며 셋을 동일한 의미 거부로 해석하지 않는다.
- `disposition`: `kept`, `removed`, `summary_reverted`, `card_fallback`. 의미 검토에서 통과했어도 최종 카드의 금지 확정 표현 등으로 전체 카드가 복귀하면 `card_fallback`이다.
- `text_excerpt`, `text_truncated`, `text_sha256`: 원문 최대500자, 절단 여부, 전체 원문 SHA-256. 비문자열 항목은 빈 발췌·null 해시로 보고한다.
- `source_ids`, `source_ids_truncated`: 초안의 문자열 출처 ID 최대8개, ID당 최대128자와 절단 여부. 전체 인용이 필요하면 최종 `cards.citations` 및 `sources_by_candidate`를 함께 확인한다. 절단된 ID는 조회 키로 사용하지 않는다.

발췌와 출처 ID의 잘못된 Unicode surrogate는 대체 문자로 치환해 UTF-8 응답·파일 저장 실패를 막는다. 해시는 치환 전 문자열을 UTF-8 `surrogatepass`로 인코딩한 값이다.

SQL 직접 인용이 최종0건이면 초안 인용과 문장별 처리를 확인한다. 초안에도0건이면 검증에서 지운 인용은 없다. 초안에 있으면 로컬 거부·의미 거부·검토 오류·카드 fallback을 구분한다. 원문 복사(`verbatim`)는 기존 정책에 따라 인용을 제거해도 문장을 유지하므로 인용 감소를 의미 검토 거부로 간주하지 않는다. 이 진단만으로 모델이 특정 출처를 선택하지 않은 이유나 검토기의 오탐 여부까지 판정할 수는 없으며, 발췌문과 출처를 대조해야 한다. 비정상 인용 위치처럼 실제 초안 항목이 없는 인용은 개수에는 포함되지만 항목 상세에는 나오지 않는다.

`required`에서 검토 호출 또는 카드 검증이 실패하면 기존처럼 예외를 반환하므로 성공 응답의 후보별 진단도 반환되지 않는다. 장애 진단을 확인하려면 `auto` fallback 응답을 사용한다. 단계별 지연 계측은 포함하지 않는다. 질문별 검색 확장과 근거 축소는 아래 계약을 따른다. 기간 동등 표기와 설명 복구의 변경 계약은 아래를 따른다.

검증: `test_verification_diagnostics.py`는 모의 모델 응답으로 생성 없음/수용/삭제, 원문 인용 제거, 요약 복귀, 재정렬·중복 문장, 검토 장애, 전체 카드 fallback, 진단 상한을 확인한다. Azure의 실제 검증 오탐과 SQL 인용 미사용 원인은 배포 후 새 요청으로 확인해야 한다.


### 기간 동등 표기와 설명 복구

숫자 검사에서는 인용 출처 JSON 최상위 `period`/`observed_end_period`의 유효한 `YYYYQ` 또는 `YYYYMM`에 대응하는 자연어 기간을 코드로 정규화한다. 예: 출처 `20262`일 때 `2026년 2분기`, 출처 `202608`일 때 `2026년 8월`. 연도·월·분기 숫자를 일반 허용 숫자 집합에 추가하지 않는다. 원래 설명과 의미 검토 입력은 그대로 유지하므로 지표·공간·단위·한계 검사는 계속 수행한다.

- 다른 연도·분기·월은 `unsupported_period`로 거부한다. 다른 출처 수치에 같은 숫자가 있어도 기간 불일치를 통과시키지 않는다.
- 파일 경로, 일반 지표값, 중첩 JSON은 기간 코드의 근거로 사용하지 않는다. 평문 출처에 이미 같은 자연어 기간이 있으면 기존 숫자 표기를 유지한다.
- 금액·비율은 기존 숫자 검사를 계속 통과해야 한다. 유효한 최상위 ISO 기간 `YYYY-MM`/`YYYY-MM-DD`의 자연어 월은 숫자 검사에서 원천의 `YYYY-MM` 부분으로만 치환한다. 숫자 파서의 부호 처리를 바꾸지 않으며 다른 월·잘못된 날짜는 허용하지 않는다. 자연어 일자·연간 기간의 추가 변환은 구현하지 않았다.
- 길이가 과도한 분기·월 숫자는 정수 변환 전에 거부한다.

생성 요청은 `output_contract`로 요약 문자열, 관측 문자열 배열, 최상위 인용 사전의 구조를 함께 전달한다. 호환 서버의 JSON Schema 지원을 가정하지 않으며 기존 `json_object` 호출 형식을 유지한다. 사전 검사는 비문자열·빈 문자열 배열 항목, 잘못된 후보 ID·요약·가설·claim_type을 문장 삭제 전에 감지한다. 추가 생성 재시도나 LLM 호출은 없다.

검증 후 네 관측 목록(`reasons`, `counter_evidence`, `context_notes`, `missing_features`)이 모두 비면 `auto`는 신뢰 가능한 `template_card(candidate)` 전체로 돌아가고, `required`는 기존 실패 계약대로 예외를 반환한다. 요약이나 분석 가설만 남았다고 LLM 설명 성공으로 처리하지 않는다. 전체 복귀는 `final_mode=template`, `fallback_reason=empty_explanation`, `calls_succeeded=0`(해당 후보만 있는 경우), `degraded=true`로 드러난다. 템플릿 자체에 원천 근거가 없으면 새 사실을 만들지 않는다.

일부 설명이 유효해도 서버가 가진 `counter_evidence`·`missing_features` 원문은 반드시 보존한다. 인용된 문장이 지지된다는 검토 결과는 원문 경고가 빠짐없이 유지됐다는 뜻이 아니므로, 원문과 다른 재서술이 있어도 정확한 원문이 없으면 보충한다. 이미 있는 원문은 중복 추가하지 않으며, 모델의 재서술과 원문이 함께 보일 수 있다. 원래 서버에 없는 미확인 항목은 새로 생성하지 않는다.

부분 보충한 카드는 `explanation_mode=mixed`, 응답도 `mixed/degraded=true`로 표시한다. 유효한 모델 카드가 채택됐으므로 `calls_succeeded`에는 포함되지만 실제 HTTP 호출 수나 완전한 답변 품질을 뜻하지 않는다. 진단의 `restored_claim_count`는 부분 복구한 원문 수, `restored_claims`는 그 원문 `source_id`와 재정렬 후 `final_position`이다. 상세 최대100개와 `restored_claims_truncated_count`를 제공하며, 복구된 원문을 모델이 생성·검토했다고 표시하지 않는다. 전체 카드 fallback은 이 부분 복구 수에 포함하지 않는다.

`test_grounding_periods.py`, `test_explanation_recovery.py`는 기간 동등 표기·잘못된 기간/값·빈 설명·사전 형식 검사·경고 전체 보존과 모드/진단을 검증한다. 저장된 Azure 응답으로 기간 문장3건과 실제 후보 템플릿을 로컬 재생했으며 의미 판단은 mock이다. 실제 생성 형식 안정성과 의미 검토 수용률은 재배포 후 확인해야 한다.


## 질문별 검색·직접 인용·후보 비교

`question_contract`는 원문에 명시된 임대료·공실·직장인구·경쟁·매출·유동 주제와 원문 구절을 연결한다. 인접한 “말고/제외/비교하지”는 주제 제외로 처리하되 “월세는 추정하지 말라”는 추정 금지로 보존한다. 제한된 어휘 규칙이므로 임의의 자연어 의도 전체를 이해한다고 보장하지 않는다. 실제 점심 방문량과 개별 매물 월세는 지원하지 않는 항목으로 명시한다.

검색은 후보 정렬·limit 적용 뒤 수행한다. `target_areas=[]`는 연결된 후보 상권이 없다는 뜻이다. 선택 행정동 코드가 있으면 행정동 통계는 독립적으로 조회한다. 상권 최대50개와 resolve_region이 확정한 행정동 코드 목록을 각각 조회한다. 잠실동처럼 여러 행정동으로 해소되는 별칭도 admin_dong_codes 전체를 보존한다(최대50개). 중간 API가 지역 DB의 5자리 시군구 코드와 8자리 행정동 코드를 추천 요청까지 보존한다. 코드가 있는 요청은 공간 선택·SQL 필터에서 이름이나 경계 교차에 의존하지 않는다. 최종 후보의 연결 상권 코드는 이미 확정된 검색 대상이므로 SQL에서 이름·구 메타데이터·crosswalk로 재탈락시키지 않는다. 행정동 조회는 확정 spatial_unit_code IN 조건만 사용한다. 이름만 받는 기존 요청은 공간 레이어에서 한 번 코드로 해소하며, 코드가 잘못됐으면 이름으로 우회하지 않는다. SQL에 미해결 이름만 오거나 명시적 빈 코드 목록만 있으면 region_unresolved로 처리하며 이름 기반 EXISTS나 전체 지역 조회로 우회하지 않는다. sales/stores/flow/change는 요청 분기, 직장인구는 요청 분기를 넘지 않는 최신 유효 관측, 임대·공실은 기존 후보 엔진처럼 최신 유효 소규모상가 분기를 사용한다. 따라서 출처마다 기간이 다를 수 있다.

임대·공실은 `join_eligible`인 유일한 R-ONE 대리 권역만 조회한다. 복수 권역 매핑은 임의 선택하지 않으며 서울 전체 값으로 대체하지 않는다. `results[].availability`는 차원·공간단위별 `available/missing/error`와 `rows_returned/no_rows/no_target_areas/table_unavailable/query_failed`를 구분한다. 행 존재와 개별 후보의 정상 관측 여부는 다를 수 있으므로 최종 비교표의 cell 상태도 확인해야 한다.

설명 후처리는 선택된 출처에 연결되지 않은 선택적 이유·배경 문장을 제거한다. 선택된 출처로 검증된 문장은 주제 분류에 없다는 이유로 다시 삭제하지 않는다. 템플릿 복귀에도 같은 정책을 적용하며 필수 경고·결측은 유지한다. 검증된 SQL 행은 후보의 상권 코드 또는 행정동 코드와 업종이 일치하는지 확인한 뒤 주제·공간단위별로 배경 문장을 직접 표시한다. 행정동 코드 연결은 후보 location.overlapping_units.admin_dong을 사용하며 이름은 표시 용도다. 서로 다른 상권·행정동 값은 충돌로 처리하거나 합산하지 않고 각각 인용한다. 출처·기간·단위·실제 조사권역·대리지표 한계를 포함하며 `retrieval-*`를 인용한다. 같은 주제에 복수 값/기간/권역이 있으면 단일 값을 임의 선택하지 않고 미확인 사유를 표시한다. 이 서버 렌더링은 모델의 재서술 의미 검토와 별개이며 추가 LLM 호출이 없다.

`question_comparison`은 선택 후보의 지표값과 출처 ID, 결측/모호성, 기간·단위·지표·공간 범위를 비교한다. 같은 상권 또는 같은 행정동의 배경값은 후보 차이로 해석하지 않는다. 후보 자체 지표가 없으면 같은 의미의 상권 관측을 우선 보완하고, 상권 관측도 없으면 행정동 관측을 명시된 행정동 단위로 제공한다. 서로 다른 공간 단위 사이의 차이는 계산하지 않는다. 임대지수는 권역 간 절대 월세 비교에 사용하지 않는다. 원천 기간이 서로 다르면 차이를 계산하지 않는다. SQL 총매출/총유동은 후보의 점포당 매출/유동밀도와 섞지 않는다. 비교 가능한 쌍은 카드에도 주제당 첫1쌍씩 관측값 차이를 설명하고, 양측 원 관측을 담은 `comparison:*` 출처를 인용한다. 비교 불가 또는 limit1은 카드의 미확인 사유로 표시한다. 후보 등급·선택·순위는 그대로 유지한다.

`question_grounding_by_candidate`는 선택적 문장 제거 수, SQL 서버 렌더링의 출처·최종 위치, 복수 권역 모호성, 비교 설명의 최종 위치와 방법을 기록한다. `verification_by_candidate`의 초안 인용0→최종 인용증가는 서버 렌더링일 수 있으므로 이 진단과 함께 읽는다. 서버가 설명을 보충하거나 제거한 LLM 카드는 mixed로 표시하며 이를 모델 단독 검증 성공으로 집계하지 않는다. 관측 설명이 없으면 미확인 안내를 보존한다.

회귀 검증: `test_question_contract`, `test_targeted_rag`, `test_evidence_reranker`, `test_question_pipeline`, `test_candidate_comparison`, `test_question_explanation`, `test_comparison_explanation`. 실제 DB 적재·권한과 Azure 배포 후 생성 품질·지연은 별도 검증이 필요하다.

빈 배열: 정상 conditions/preferences/retrieval_requests 및 하위 배열은 빈 상태로 처리한다. 문자열 필드(tool/strength/mode/anchor_type/industry_code/claim_type/confidence)에 배열·객체가 들어와도 TypeError로 중단하지 않고 해당 값을 폐기하거나 기존 기본값을 사용한다. 후보가 없으면 LLM을 호출하지 않고 cards=[]/empty_reason=no_candidates를 반환한다. 전체 LLM 응답이 객체가 아닌 경우의 형식 오류와 required 모드의 실제 생성·검증 실패 정책은 유지한다.


### 주제 차단 완화

주제 일치는 점수 가산으로만 사용한다. 미분류·다른 주제여도 질문 어휘와 맞는 출처는 리랭킹 대상이며, 명시적으로 제외된 주제만 선택에서 제외한다(필수 경고는 유지). 구조화 출처의 어휘 관련도는 지표명·차원·해석·라벨·설명으로 계산하며 임의 note의 우연한 단어 일치로 승격하지 않는다.

최대48개 사전 후보와 최종 일반 근거32개 안에서 요청 주제와 질문 핵심어의 대표 근거를 함께 보존한다. 알려진 주제는 지표명 문자열이 달라도 source_topic_ids로 대표 근거를 보호한다. 예를 들어 직장인 근거가60개 있어도 질문에 명시된 지하철 근거가 밀리지 않도록 한다. 모델 사용 시 어휘 일치가 없는 출처도 최대8개 탐색 후보로 제공하며, 모델이 선택하지 않은 탐색 근거를 단순 빈자리 보충으로 추가하지 않는다. 탐색 수는 exploration_count, 핵심어 대표 근거는 coverage_ids로 확인한다. 어휘 기반 대표 선택이 모든 자연어 의미를 보장하지는 않는다.

후보별 SQL 범위 제한은 알려진 주제 유무와 관계없이 항상 적용한다. 전체 출처는 감사용으로 남지만 다른 후보의 지역·업종 근거는 생성·의미검토 입력에서 제외한다. 출처·수치·기간·인용 bucket·복수 관측 모호성 검증은 유지한다. `test_soft_question_grounding`과 `test_evidence_reranker`에서 복합 질문 근거 보존, 검증된 미분류 설명 유지, 잘못된 수치 거부, 후보별 범위 제한을 확인한다. 실제 배포 답변 품질과 토큰·지연 변화는 별도 검증 대상이다.
