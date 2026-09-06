# 사이렌 Backend 연동 계약 보완

계약 표면 버전은 `risk-siren-contract-v1.1`이며, 점수 산식 버전인
`risk-siren-v1.2`와 별도로 관리한다. 운영 대상 구현과 테스트 기준 경로는
`services/siren`이다.

## 책임

middle-backend는 인증·테넌트 경계를 확인한 뒤 점포·보고서 ID만 사이렌에 전달한다.
사이렌 orchestrator는 FMP 보고서 저장소와 IDEATON 위치/상권 저장소를 읽어 canonical
요청으로 매핑하고, 결정론적 pipeline을 실행한다. middle-backend는 결과 상태·결과 저장을
담당한다. 실제 알림 발송은 계속 disabled다.

ID-only 호출 경로는 다음과 같다.

```text
POST /internal/risk-sirens/analyze-trigger
{
  "request_id": "uuid",
  "report_id": "100",
  "franchise_id": "1",
  "branch_id": "10",
  "as_of": "2026-09-30",
  "options": {"llm_mode": "explanation_only", "send_notifications": false}
}
```

사이렌 프로세스에는 `FMP_DATABASE_URL`과 `IDEATON_DATABASE_URL`(또는
`SIREN_FMP_DATABASE_URL`/`SIREN_IDEATON_DATABASE_URL`)을 읽기 전용으로 설정한다.
본사 연간 폐업 집계가 승인된 FMP 테이블·뷰에 있을 때만
`SIREN_FRANCHISE_CLOSURE_TABLE=public.franchise_closure_year`처럼
단순한 스키마·테이블명을 추가한다. 테이블이 없거나 설정하지 않으면 해당 신호는
`missing`이며 폐업 0건으로 대체하지 않는다. 현재 저장소의 FMP 기본 스키마에는 이
집계 테이블이 없으므로, 별도 migration 없이 운영 원천이 제공될 때만 계산된다.
운영보고서는 `operation_report.status='COMPLETED'`인 행만 분석 대상으로 삼으며,
작성 중·분석 중·실패 행은 매출 시계열에 포함하지 않는다.
기존 full canonical payload의 `/internal/risk-sirens/analyze` 경로는 호환용으로 유지한다.

middle-backend의 `SIREN_ANALYSIS_ENABLED` 기본값은 `false`다. 현재 `report_analysis`가
`risk_score`·`risk_level` NOT NULL이고 `rule_version` VARCHAR(20)이어서 partial 결과를
안전하게 저장할 수 없기 때문이다. 스키마 변경 및 운영 데이터베이스 연결을 승인한 뒤
이 플래그를 활성화해야 한다. partial 결과는 점수/등급을 지어내지 않고 FAILED와
`PARTIAL_ANALYSIS_NOT_STORED` 사유로 남긴다.

본사 요약은 요청 franchise_id와 각 결과의 branch.franchise_id가 일치해야 한다. branch_id가 없거나 동일 점포 결과가 중복되면 422로 거부한다. 이 입력 검증은 Backend의 인증·권한 검사를 대체하지 않는다.

본사 요약에는 `risk_level_distribution`과 `alert_candidate_count`를 추가한다.
`alert_candidate_count`는 이번 요청의 `branch_results` 중
`alert.should_fire=true`인 개수이며 미읽음 개수가 아니다.
`unread_alert_count`는 읽음 상태를 보유하지 않는 사이렌이 계산하지 않으므로
호환성을 위해 항상 `null`로 반환한다. 실제 읽음 수는 Backend Notification 저장소가
계산한다. 데모 hq_summary.json은 여러 본사를 섞은 한 결과 대신 `summaries` 배열에
본사별 응답을 저장한다.

## 연간 가맹점 폐업 통계

분석 요청에 선택 객체 `franchise_closure`를 추가한다.

```json
{
  "franchise_id": "fr-001",
  "year": 2025,
  "previous_year_end_count": 100,
  "new_openings": 20,
  "closures": 12,
  "source": "본사 연간 실폐업 집계",
  "synthetic": false
}
```

- 분자 C = 해당 연도 실제 폐업 가맹점 수. 계약 종료·해지 건수는 실폐업 확인 없이 대체하지 않는다.
- B = 전년도 말 계약 유지 가맹점 수, N = 해당 연도 신규 개점 수.
- operating_base_rate_pct = C / (B + N) × 100. 예시 10%.
- previous_year_base_rate_pct = C / B × 100. 예시 12%.
- 분모가 0이면 해당 비율은 null이다. 전년도 기준 비율은 100%를 초과할 수 있으며 상한으로 자르지 않는다.
- 동일 브랜드·집계 기준을 사용하고 중복 개점/명의변경을 신규점으로 세지 않는다. C > B+N, 음수·소수·불리언 건수는 거부한다.
- 이 버전은 as_of 연도 이전에 완료된 연간 통계만 받는다. 당해 연도 누계(YTD)는 지원하지 않으며 연간 값으로 가장해 받지 않는다.
- 입력이 없으면 결과는 missing이며, 계산 불가를 0%로 표시하지 않는다.
- 응답에 분자·분모·출처·합성 여부와 formula_version을 포함한다.
- 공식기관 출처가 확인되지 않았으므로 formula_authority는 project_defined다. 국가 공인 공식이나 개별 점포 폐업 확률로 표시하지 않는다.
- 두 비율은 브랜드 배경 지표다. 기존 지역·업종별 분기 상권 통계와 합치지 않으며, 위험 점수·등급·경고에 중복 가산하지 않는다.

## 검증

저장소 루트에서 `python -m unittest discover -s services/siren/tests -v`.
데모 재생성: `python -m services.siren.demo.build --reuse` (저장된 공개 지표 기준선과 합성 점포 데이터 사용).

## 확인된 점포 위험 경고와 정적 리뷰 보완

- 시장 자료가 없거나 일부 지표가 미완성일 때 종합 점수·등급은 계속 null이다. 다만 `calculated`인 점포 층 또는 수익성 지표가 기존 위험 하한(`caution_upper_bound`) 이상이고 해당 지표의 evidence가 있으면 `alert.should_fire=true`로 반환한다.
- `alert.trigger`는 basis(`composite`, `branch_risk`, `profitability`), 해당 점수, 기준값, 근거 ID를 제공한다. 점포 경고를 종합 위험 등급으로 표시하지 않는다. 종합 등급이 주의여도 확정된 수익성 위험 경고는 유지하며, 시장 자료 유무로 같은 점포 경고가 사라지지 않는다. 낮거나 미확정인 점포 신호, 데이터 누락 자체만으로 경고하지 않는다.
- 점주·본사 projection에도 alert를 전달하고, 본사 watchlist에 부분 분석의 점포 경고를 표시한다. 실제 발송은 여전히 disabled다.
- 연속 적자는 as_of 월부터 한 달씩 역순으로 확인한다. 중간 월 누락 또는 흑자/손익0에서 중단하며, 기준 월 자료가 없으면 현재 연속 적자는 0이다. 이는 누락 기간이 안전하다는 의미가 아니다. 기존 missing/partial 표시는 유지한다.
- 본사 요약은 집계에 사용하는 중첩 객체를 검증한다. null 객체, 점수 범위 밖 값·불리언, 잘못된 등급 및 상태/점수 모순은 422다. 비집계 필드는 무시한다.
- 각 branch.as_of는 요청 as_of와 정확히 같아야 한다. 미래·과거 결과를 섞지 않으며, 누락·잘못된 날짜도422다. Backend는 동일 기준일의 결과 묶음을 전달해야 한다.
- 적자 계산 동작 변경을 구분하기 위해 score_version은 risk-siren-v1.2로 유지한다. 현행 `report_analysis.rule_version VARCHAR(20)`에 저장할 수 있는 길이다. 이벤트 중복 방지 키에도 이 버전이 반영된다. alert_policy_version은 confirmed-branch-v1이다.

## 계약 응답 필드와 부분 계산 정책

- `risk.risk_level`은 `grade`에서만 파생한다: `정상→NORMAL`, `주의→CAUTION`, `위험→DANGER`, 미계산은 `null`이다.
- `alert`에는 `risk_level`, `dispatch_owner="middle_backend"`, `suppressed_reason`를 포함한다. 실제 발송은 계속 `disabled`이며 사이렌은 알림 본문을 생성하거나 발송하지 않는다.
- `financial_products`는 `owner="middle_backend"`, `status="grade_only"`, `recommended_grade`, `recommended_risk_level`, 빈 `items`만 반환한다. 상품 조회·선정은 이 서비스의 책임이 아니다.
- `options.grade_policy` 기본값은 `strict`다. `renormalized_partial`과 `branch_only_provisional`은 누락 신호를 0점으로 대체하지 않고 잠정 점수를 만들며, 이 모드에서는 `alert.should_fire=false`로 강제한다.
- `strict` 모드에서 종합 등급이 아직 없더라도 계산 완료된 점포층 또는 수익성 신호와 근거가 위험 하한을 넘으면 `alert.trigger`를 가진 점포 경고 후보를 만들 수 있다. 이 후보는 종합 등급과 분리해 표시해야 한다.
- FMP 운영보고서 매핑에서 필수 입력 필드가 빠진 월은 0원으로 채우지 않고 제외한다. 결과에는 `missing_data`와 `uncertainty`가 남는다.

## 비용 위험 점수 및 숫자 입력 검증 보완 (v1.2)

- SR-05는 영업이익률·이익률 악화·연속 적자의 핵심 신호 수를 분모로 유지한다. 인건비율·쿠폰비율·이자 상승 위험이 추가돼도 평균 분모를 늘리지 않으며, 기존 위험을 희석하지 않고 가산한다.
- 최근 3개월 비용만 증가하고 나머지 자료·가용성이 같다면 수익성 위험 점수와 경고가 감소하지 않는다. 추가 비용 위험이 없는 입력은 기존 계산을 유지한다. 추가 위험이 있는 입력은 이전보다 점수가 높아질 수 있다.
- 기존 0.55/0.45 결합 계수, 위험 하한, 신호 기준값과 0~100 범위는 유지한다. 산식 동작 변경은 `score_version=risk-siren-v1.2`로 구분하고, 이벤트 중복 방지 키에도 반영한다. 현재 middle-backend의 `rule_version VARCHAR(20)`에 맞춘 값이며, 향후 버전 문자열을 확장할 때는 별도 schema migration과 함께 변경한다. 정책은 계속 provisional이다.
- 입력 수치의 NaN·Infinity·-Infinity를 거부한다. 문자열 형태 및 JSON 숫자 `1e309`도 HTTP 422로 반환하며, 오류의 비유한 입력값은 JSON에서 표현 가능한 문자열로 표시한다.
