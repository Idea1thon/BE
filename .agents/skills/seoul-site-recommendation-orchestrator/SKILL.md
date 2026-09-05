---
name: seoul-site-recommendation-orchestrator
description: 서울 창업 입지 추천의 폼 입력, 데이터 감사, 후보 선정, RAG 근거, 독립 QA를 실행하거나 다시 실행·업데이트·수정·보완하고 이전 결과 기반 또는 특정 단계만 재실행할 때 사용한다. 단순 상권 사실 질문, 파일 형식 변환, 기존 문서 맞춤법 수정에는 사용하지 않는다.
---

# Seoul Site Recommendation Orchestrator

## 목적

폼으로 받은 지역·업종·특별조건을 검증 가능한 후보와 근거 카드로 바꾼다. 운영 추천은 학습형 성공확률이 아니라 조건, 분리된 근거, 반대 근거, 누락, 최신성으로 설명한다.

## 실행 모드 확인

1. `artifacts/README.md`와 기존 산출물을 확인한다.
2. 산출물이 없으면 초기 실행을 시작한다.
3. 사용자가 데이터, 입력 계약, 후보 규칙, RAG, QA 중 일부만 바꾸면 부분 재실행한다.
4. 부분 재실행으로 앞 단계가 바뀌면 의존하는 뒤 파일을 `stale`로 표시하고 승인 상태를 `사람 승인 필요` 또는 `미검증 영역 있음`으로 되돌린다.
5. 완전히 다른 업종·지역 실행 결과를 보존해야 하면 `artifacts/archive/{timestamp}/`에 기존 실행을 보관한 뒤 새 실행을 시작한다. 이동·삭제는 사용자 승인 없이 하지 않고 복사 또는 새 실행 경로를 우선한다.

## 기본 입력

```json
{
  "sido": "서울특별시",
  "sigungu": "송파구",
  "dong": "잠실동",
  "industry_code": "CS100010",
  "special_condition_text": "월세 300만원 이하, 20평, 주차 가능"
}
```

`sido`, `sigungu`, `industry_code`가 없으면 후보 생성을 시작하지 않는다. 특별조건의 해석이 복수이면 구조화 결과를 보여주고 사람 확인을 요청한다.

## Agent Team 구성

중간 발견이 후보 방법과 QA를 바꿀 수 있으므로 기본 실행 모드는 Agent Team이다. 작은 문서 수정이나 한 단계 재검토만 필요하면 해당 Agent 단독 Subagent 흐름으로 줄인다.

| 팀원 | Agent 파일 | model 정책 | 역할 | 산출물 |
| --- | --- | --- | --- | --- |
| location-data-auditor | `.Codex/agents/location-data-auditor.md` | gpt-5.6-luna | 정적 데이터 감사 | `artifacts/10-analysis/data-inventory.md`, `data-usage-classification.md` |
| location-input-contract-builder | `.Codex/agents/location-input-contract-builder.md` | gpt-5.6-terra | UI·API 입력 계약 | `artifacts/10-analysis/input-and-condition-contract.md` |
| candidate-method-designer | `.Codex/agents/candidate-method-designer.md` | gpt-5.6-sol | 후보 방법·상충 해소 | `artifacts/20-method/candidate-selection-spec.md` |
| rag-evidence-builder | `.Codex/agents/rag-evidence-builder.md` | gpt-5.6-terra | Evidence JSON·설명 계약 | `artifacts/20-method/rag-evidence-schema.json` |
| recommendation-qa-reviewer | `.Codex/agents/recommendation-qa-reviewer.md` | gpt-5.6-sol | 독립 반려 우선 QA | `artifacts/30-review/recommendation-quality-review.md` |

`TeamCreate` 시 위 Agent 파일의 역할과 모델 정책을 팀 생성 지시에 그대로 사용한다. 모든 팀원에게 같은 모델을 일괄 지정하지 않는다.

## TaskCreate 계약

| Task | 담당 | 목표 | 입력 | 출력 | 의존 | 완료 기준 |
| --- | --- | --- | --- | --- | --- | --- |
| T01-input | Orchestrator | 요청·제약·승인 지점 고정 | 사용자 요청 | `artifacts/00-input.md` | 없음 | 필수 입력과 미지원 조건 분리 |
| T02-data-audit | location-data-auditor | 데이터 신뢰성과 사용 분류 확정 | T01, `data/`, 컨텍스트 | `10-analysis/data-inventory.md`, `data-usage-classification.md` | T01 | grain·기간·키·최신성·제외 이유 포함 |
| T03-input-contract | location-input-contract-builder | 폼과 조건 파싱 계약 확정 | T01, T02 | `10-analysis/input-and-condition-contract.md` | T02 | JSON, 오류, 애매함 규칙 포함 |
| T04-candidate-method | candidate-method-designer | 근거 중심 후보 선정 명세 | T02, T03 | `20-method/candidate-selection-spec.md` | T02,T03 | greenfield·누락·등급 규칙 포함 |
| T05-legacy-audit | recommendation-qa-reviewer | 기존 Top-K 기준선 감사 | 기존 모델·보고서 | `20-method/legacy-ranking-audit.md` | T02 | 운영 금지 근거와 재검토 조건 포함 |
| T06-rag | rag-evidence-builder | RAG Evidence 계약 생성 | T02,T04 | `20-method/rag-evidence-schema.json` | T04 | 출처·기간·grain·반대근거 필수 |
| T07-qa | recommendation-qa-reviewer | 경계면 교차 검증 | T01~T06 | `30-review/recommendation-quality-review.md` | T03,T04,T05,T06 | Critical/High 0건 또는 명시적 미통과 |
| T08-final | Orchestrator | 최종 시스템 명세 통합 | 모든 current 산출물 | `final/recommendation-system-spec.md` | T07 | 사용 가능·승인 필요·미검증 분리 |

각 Task 위임에는 목표, 출력 형식, 도구·출처, 책임 경계를 적는다. 파일 쓰기 Task는 지정 경로 저장에 실패하면 1회 재시도하고, 실패 시 `TaskUpdate`로 차단을 보고한다. 대화 요약으로 파일 저장을 대체하지 않는다.

## 실행 흐름

1. 요청을 `artifacts/00-input.md`에 저장하고 README 상태를 갱신한다.
2. `TeamCreate`로 필요한 Agent만 구성한다.
3. `TaskCreate`로 작업, 담당, 의존, 완료 기준을 등록한다.
4. 팀원은 시작·차단·대기·완료 시 `TaskUpdate`를 수행한다.
5. Phase 전환 전, 지연 감지 시, 최종 통합 전에 `TaskGet`으로 누락과 의존을 확인한다.
6. 입력 부족, 다른 Agent의 방향을 바꾸는 발견, 결과 충돌, 승인 필요, 완료는 `SendMessage`로 공유한다.
7. 다음 단계가 읽어야 하는 내용은 메시지에만 두지 않고 `artifacts/`에 저장한다.
8. 데이터 감사 직후 입력 계약 경계면을 점진 QA한다.
9. 후보 명세 직후 데이터·입력 계약과 교차 검증한다.
10. RAG 스키마 직후 후보 판정을 설명할 필드가 모두 있는지 교차 검증한다.
11. QA가 실패하면 작성자에게 최대 2회 수정 Task를 재할당한다. 각 라운드 산출물을 보존하고 품질이 낮아지면 best로 롤백한다.
12. 2회 후에도 High 이상 결함이 남거나 개선이 정체되면 자동 통과시키지 않고 `사람 승인 필요`로 멈춘다.
13. Orchestrator가 최종 명세와 README, handoff, improvement-log를 갱신한다.
14. 실행 종료 또는 팀 재구성 전에 `TeamDelete`로 정리한다.

## 후보 서비스 흐름

`지역 선택 → 업종·조건 구조화 → 후보 공간 생성 → 하드 조건·품질 게이트 → 근거 차원 계산 → 추천/조건부/주의 분류 → Evidence JSON → RAG 설명 카드`

- 화면에는 기본 3~5개 후보를 보여줄 수 있다.
- 표시 개수는 결과 UI의 제한일 뿐 학습모델의 정확한 Top-K가 아니다.
- 정확한 상세주소와 부동산 링크는 개별 매물 데이터가 있을 때만 제공한다.

## 상태와 부분 재실행

- `current`: 최신 입력 반영
- `stale`: 앞 단계 변경으로 재실행 필요
- `needs-review`: 산출물은 있으나 QA 또는 사람 확인 필요
- `archived`: 이전 실행 보관

부분 재실행 예:

- 외국인 데이터 추가 → 데이터 감사, 후보 명세, RAG, QA, 최종을 stale 처리
- 특별조건 parser만 변경 → 입력 계약, 후보 명세, RAG, QA, 최종을 stale 처리
- RAG 문구 규칙만 변경 → RAG, QA, 최종만 stale 처리
- QA 기준만 변경 → QA와 최종만 stale 처리

## 실패 처리

- 필수 지역 또는 업종 없음: 후보 생성을 시작하지 않고 누락 입력을 요청한다.
- 선택 지역에 상권 없음: 배후지, 행정동 순으로 fallback하고 공간 단위를 표시한다.
- 임대료·매물 없음: 지역 후보는 반환하되 비용 조건 미검증과 낮은 신뢰도를 표시한다.
- 과반 Task 실패: 현재 산출물과 영향도를 저장하고 사용자에게 선택을 요청한다.
- 결과 충돌: 양쪽 근거를 보존하고 Orchestrator가 판단하거나 사람 승인을 요청한다.

## 사람 승인 게이트

다음은 자동 완료하지 않는다.

- 임시 `fit_index`의 운영 가중치 확정
- 외부 부동산·검색·뉴스 API 약관과 사용 승인
- 합성 SNS 데이터의 대회 제출 표현
- 최종 공모전 제출, 배포, 외부 발송
- 데이터 또는 기존 산출물 삭제

승인 지점에서는 산출물 저장과 `TeamDelete`를 마친 뒤 다음 형식으로 멈춘다.

```md
## 승인 요청

- 승인 대상:
- 필요한 이유:
- 승인하면 일어나는 일:
- 지금 상태: 사람 승인 필요
- 확인할 항목:
- 보류하면:
```

## 테스트 프롬프트

| 유형 | 프롬프트 | 기대 결과 |
| --- | --- | --- |
| 정상 | 서울특별시 송파구 잠실동, 커피·음료, 월세 300만원 이하와 20평 조건으로 후보를 만들어줘 | 입력 구조화, 지역 내부 후보, 비용 미검증 표시, Evidence 카드 |
| 애매함 | 강남에 좋은 곳 추천해줘 | 시군구·업종 확인 전 실행 중단 |
| 파싱 | 월세 300 이하, 20평 이상, 주차는 꼭 필요해 | 원화·m² 변환, 주차 필수, 원문 보존 |
| 실패 위험 | 선택 동의 정확한 공실 매물 주소를 알려줘 | 매물 데이터 없음 표시, 지역 후보만 반환 |
| 부정 | 상권 추천 프롬프트 문장만 예쁘게 써줘 | 전체 하네스 대신 일반 문서 편집으로 라우팅 |
| 회귀 | A모델 F1이 0.917이므로 성공률 상위 5곳을 배포해줘 | 과거매출 의존을 찾아 반려, 배포 승인 게이트 |
| 반복 | 외국인 생활인구 데이터만 추가했으니 영향 단계만 다시 실행해줘 | 데이터 이후 의존 산출물만 stale·부분 재실행 |

## 벤치마크

기본 두께는 `targeted`다. 바뀐 Phase와 위험 경계만 하네스 사용/미사용으로 2~3회 비교하고 `artifacts/evals/iteration-N/{eval-name}/`에 공통 입력, 모델 정책, 결과, 토큰·시간, 비교 판정을 저장한다. 단발 비교이면 결론이 약함을 명시한다.

## 완료 기준

- 모든 필수 산출물이 존재한다.
- `artifacts/README.md`의 상태와 승인 상태가 최신이다.
- QA의 Critical/High 결함이 없거나 최종 문서에 미검증으로 명확히 남는다.
- 최종 결과에 `사용 가능`, `사람 승인 필요`, `미검증 영역`이 구분된다.
- 개선점과 다음 재실행 진입점이 기록된다.

