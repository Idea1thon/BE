# 위험 사이렌 구현 메모 — v1.2 (RS-03)

- 실행일: 2026-09-07
- 선행: signal-audit.md (RS-01), input-output-contract.md (RS-02) — 모두 current
- score_version: `risk-siren-v1-provisional`
- 테스트: 25개 unittest 통과, harness validator 통과 (RS-03 초기 17 → RS-05 라운드1 후 24 → RS-06 라운드2 후 25)
- 독립 QA: 기존 RS-04 2회 완료. v1.2 role-scope 변경은 새 QA 대상이다.

## 0. v1.2 역할별 projection·provider 경계

| 흐름 | 처리 |
| --- | --- |
| Owner 보고서 제출 | middle-backend가 인증·보고서 저장 후 ID-only trigger를 등록 |
| Siren 조회 | `FmpProvider`가 운영보고서를, `IdeatonProvider`가 주소·상권·시장·경쟁을 읽음 |
| Owner 응답 | `branch_owner`: 점포 components, Evidence, missing/uncertainty, alert, 조치 |
| HQ 응답 | `franchise_hq`: 점포별 component status/watchlist/provenance만 제공 |
| HQ 집계 | middle-backend `GET /api/v1/branches/risk-summary`가 공통 as_of의 점포 결과를 `hq-summary`로 집계 |

중간 백엔드는 IDEATON 원시 데이터를 읽거나 조립하지 않는다. 역할별 응답은
`ReportAnalysis.factors` JSONB의 projection envelope에서 선택하며, 기존 rows는
상세 근거를 임의로 복원하지 않는 호환 fallback을 사용한다.

## 0.1 검증 명령

```bash
/private/tmp/be-pr34/services/middle-backend/.venv/bin/python -m unittest discover -s services/siren/tests -v
JWT_SECRET='test-secret-which-is-at-least-32-characters' PYTHONPATH=services/middle-backend \
  /private/tmp/be-pr34/services/middle-backend/.venv/bin/python -c \
  'from app.main import app; assert any(r.path == "/api/v1/branches/risk-summary" for r in app.routes)'
```

## 1. v0 → v1 변경

| 영역 | v0 | v1 |
| --- | --- | --- |
| 신호 | SR-01~04 (4개, 가맹점 단위) | SR-01·02·03·05 종합 + SR-04 보조 (5개, 2층) |
| grain | 가맹점 | market(상권×업종 실측) + branch(가맹점 월별) 2층 |
| SR-02 기간 | 최근 3개월 | market=분기, branch=최근 3개월 |
| SR-05 | 없음 | 수익성 악화 (월별 운영보고서 손익) 신규 |
| SR-04 | 점수 요소 | 보조 신호 — `review_signal` 블록, 종합 점수 가중치 0 |
| 데이터 | 미연결 | market=서울시 공개데이터 실측, branch/review=대회용 합성 생성기 |
| 응답 | 단일 blob | `layers`, `review_signal`, `data_provenance`, `projections` 추가 |
| 엔드포인트 | analyze | analyze + hq-summary |

## 2. 코드 구조 (`service/siren/`)

| 파일 | 역할 |
| --- | --- |
| `models.py` | v1 요청·응답 계약. `IndustryCode` enum, `BranchLocation`, `MarketData`(closure/sales quarters + competition), `BranchMonthlyReport`(폼 전체 손익), `ReviewsInput`(레코드), `HqSummaryRequest/Response`. `extra="forbid"` |
| `reports.py` | 월별 운영보고서 → 파생 손익 (`net_sales`, `cogs`, `gross_profit`, `operating_profit`, `operating_margin`, `labor_ratio`, `delivery_ratio`, `coupon_ratio`). 3개월 창 헬퍼. 기말>기초 시 원가 0 하한 |
| `risk_signals.py` | 5개 순수 계산기 + `RiskPolicy` v1(provisional). `calculate_market_closure`(분기·rolling 2q/4q, 분모=직전분기 영업중), `calculate_market_sales`(QoQ+YoY), `calculate_competition`(반경·가중), `calculate_branch_sales`(3m vs 3m 가중), `calculate_profitability`(SR-05), `calculate_review_signal`(SR-04 보조) |
| `pipeline.py` | 2층 조립. market/branch layer 점수 → 종합. Evidence(+layer, +synthetic), `data_provenance`, `missing_data`, `uncertainty`, `projections`(owner/hq). `send_notifications=true` → ValueError |
| `alerts.py` | 결정론적 멱등키 `sha256(branch|as_of|version)[:24]`. `should_fire = grade=="위험"`. `dispatch_status` 항상 `disabled` |
| `explanation.py` | 결정론 템플릿 v1. Evidence ID만 참조. 합성 고지 문구 포함 |
| `hq_summary.py` | 본사 집계. `danger_ratio_pct`와 `average_score` 별도 필드 (혼용 금지). watchlist |
| `api.py` | `GET /health`, `POST /internal/risk-sirens/analyze`, `POST /internal/risk-sirens/hq-summary` |

## 3. 종합 점수 정책 (전부 provisional — 사람 승인 필요)

```
market_risk.score = 0.45·SR01 + 0.35·SR02.market + 0.20·SR03      (3개 모두 계산 시)
branch_risk.score = 0.35·SR02.branch + 0.65·SR05                   (2개 모두 계산 시)
   └ SR05 >= 80 이면 branch_risk.score = max(그 값, SR05 - 12)     (지속·심각한 수익성 붕괴는 단독 위험 신호)
risk.score        = max(branch_risk.score, 0.35·market + 0.65·branch)
   └ 시장 위험은 가맹점 위험을 올릴 수 있어도, 양호한 시장이 가맹점 위험을 상쇄하지 않음
grade             = <40 정상 / <70 주의 / >=70 위험   (두 층 모두 calculated 일 때만 부여)
review_signal     = 종합 점수에 미포함 (가중치 0). sub_score·watchlist_flag만
```

SR-05 세부 스코어링: 음수 마진·마진 하락폭·연속 적자 개월·labor_ratio·coupon_ratio·대출이자 상승을 부분 점수로 만들어 `0.55·평균 + 0.45·최댓값`, 그 후 마진 ≤ -12% 또는 연속 적자 ≥ 8개월이면 하한 75, 마진 ≤ -5% 또는 연속 적자 ≥ 4개월이면 하한 55.

## 4. 데모 생성기 (`service/siren/demo/`)

| 파일 | 역할 |
| --- | --- |
| `seoul_data.py` | 서울시 CSV 로더 (인허가 패널·추정매출·인허가 raw·상권 영역). NFC 정규화, `SIREN_SEOUL_DATA_DIR` env |
| `baseline.py` | 20개 (상권×업종) 선정 + market_data 조립 → `market_baseline.json`. 선정 조건: 발달/골목/관광특구, 인허가 완전분기 ≥ 18, 추정매출 2023Q1~2026Q1 연속, 영업중 ≥ 5, 시장 셀 분기평균 ≥ 3억, 극단 변동 배제 |
| `scenarios.py` | 6 시나리오 (healthy / slow_margin_squeeze / sales_decline / delivery_trap / debt_spiral / sparse), 24개월 궤적 |
| `generate.py` | 가맹점별 24개월 운영보고서 + 리뷰 합성. seed=branch_id. 매출 = 업종 기준액 × 실 추정매출 지수(감쇠 0.7+0.3·clamp) × 시나리오 드리프트 × 계절 × 노이즈 |
| `reviews_templates.py` | 일반 한국어 음식점 리뷰 문구 (긍정 10·부정 10·중립 5, 실존 비지칭) |
| `build.py` | 전체 실행: 480 운영보고서, 20 최종 스냅샷 결과, 20×19 롤링 타임라인, hq_summary, SUMMARY.md. 유사업종 매핑 포함 |

실행:
```bash
python -m service.siren.demo.baseline           # market_baseline.json
python -m service.siren.demo.build --reuse       # 전체 데모 산출물
```

## 5. 데모 실행 결과 (as_of 2026-03-31, 20 가맹점)

| 시나리오 | 가맹점 | 결과 |
| --- | --- | --- |
| healthy | 5 | 정상 5 |
| slow_margin_squeeze | 4 | 주의 3, 정상 1 (명동 제과 — 실 시장 강세) |
| sales_decline | 3 | 위험 3 (사이렌 2025-10~2026-02 발동) |
| delivery_trap | 3 | 위험 2, 주의 1 |
| debt_spiral | 3 | 위험 3 |
| sparse | 2 | partial 2 (최근 월 누락 → 등급 미부여, 사이렌 미발동) |

본사 집계: 위험 8 / 주의 4 / 정상 6, danger_ratio 44.4%, average_score 60.1 (두 값 별도 표시).

`data_provenance.contains_synthetic = true`, disclosure 문구가 모든 결과·Evidence·설명·본사 집계에 포함됨.

## 6. 결정론

- `analyze()` 2회 호출 결과 동일 (17개 테스트 중 1개가 검증)
- 데모 전체 재생성 2회 → `results/` 바이트 동일 확인
- 생성기 무작위성은 `Random(f"risk-siren-demo::{branch_id}")` 로 고정

## 7. 미구현·사람 승인 필요

- 종합 가중치(w1~w5, 층간), 40·70 임계값, SR-05 하한값·트리거 — 전부 provisional
- SR-03 반경 250m·유사업종 매핑 — 데모 값, 계약 승인 대상
- SR-04 보조점수의 향후 종합 편입
- 합성 리뷰 텍스트 템플릿 최종 검토
- 금융상품 카탈로그·`recommended_actions` 내용
- 실제 이메일·인앱 발송 (현재 `disabled`)
- 실 데이터 연동 시 `branch_reports`/`reviews`의 `source`를 `synthetic_*` → 실제값으로 교체하면 코어 계산 변경 없이 동작

## 8. RS-05 결함 수정 라운드 1 (2026-09-04, RS-04 QA 반영)

QA 판정 NOT PASS (High 2, Medium 10) → 다음 수정:

| 결함 | 수정 |
| --- | --- |
| **RS04-D01 (High)** 최근 3개월 순매출 합 ≤ 0 → 무경보 | `calculate_profitability`: net_sales 합 ≤ 0 이면 `score=95`, `status=calculated`, `net_sales_nonpositive=true`, `operating_margin_recent_3m_pct="not_calculable"` 반환. branch 층이 SR-05 단독으로라도 산출됨. 전용 Evidence `ev-profit-net-sales-nonpositive`. 테스트 `test_zero_net_sales_is_high_risk_not_missing` |
| **RS04-D02 (High)** 합성 포함 시에도 disclosure "모두 실측" | disclosure 조건에 `market_synth` 포함. market 합성이면 합성 신호 목록을 명시. 테스트 `test_disclosure_not_false_when_market_is_synthetic` |
| RS04-D03 (M) shadow 점수 필드 | `provisional_composite`/`weighted_composite` 제거. `branch_floor_applied`는 `both_calculated`일 때만 true |
| RS04-D04 (M) `excluded_future_months` 미노출 | 응답 최상위 구조화 필드로 추가. 테스트 `test_excluded_future_months_is_structured` |
| RS04-D06 (M) 미완결 분기를 현재 분기로 취급 | `_last_completed_quarter_index(as_of)` — 분기말 ≤ as_of 인 분기만. 테스트 `test_incomplete_quarter_not_counted_as_current` |
| RS04-D07 (M) 반경 없이 경쟁 점수 | `radius_m is None` → SR-03 `status=missing`, `score=None`, missing_data 기재. 테스트 `test_competition_without_radius_is_missing` |
| RS04-D08 (M) 폐업 패널 정합성 | 폐업률 > 100% → `rate_exceeds_100=true` + status partial + uncertainty |
| RS04-D09 (M) 리뷰 점포 키 없음 | `ReviewRecord.branch_id` (optional) 추가. 불일치 레코드 제외 + uncertainty. 테스트 `test_mismatched_review_branch_id_excluded` |
| RS04-D10 (M) event_type 명칭 | `branch_risk_evaluated` 로 변경, `previous_grade: null` 필드 추가 + 주석 (전이 감지는 중간 백엔드 책임) |
| RS04-D11 (M) `부분` 분기 rolling 반영 | rolling 창에 `부분` 분기 포함 시 status partial + `window_has_partial_quarter` 플래그 + uncertainty |
| RS04-D12 (M) Evidence source 통합 문자열 | `MarketData.closure_source`/`sales_source` 추가. 데모가 신호별 정확 출처 지정 |
| RS04-L02 | hq_summary watchlist reason 을 signal-id 형식으로 |
| RS04-L04 | 기준일 이후 리뷰 제외 시 uncertainty |
| RS04-D05 | 코드 동결 후 데모 재생성 (아래) |

라운드 1 후: 24개 unittest (7개 신규), 데모 재생성 결정론 확인.

### 수정 라운드 2 (RS-06, RS-04 재검토 반영)

라운드 1 High 2건 해결 확인. 재검토가 신규 High 1건 발견 → 수정:

| 결함 | 수정 |
| --- | --- |
| **RS04-D13 (High)** D12(신호별 출처 분리)가 만든 표면 — synthetic 판정이 부모 `source`만 봄 | `pipeline.py` synthetic 판정을 신호별로: `closure_synth`/`market_sales_synth`/`comp_synth` = `_is_synthetic(하위_source or source)`. `contains_synthetic`·disclosure·`by_signal`·Evidence 가 신호별 플래그 사용. 테스트 `test_synthetic_sub_source_marks_signal_synthetic` |
| RS04-L05 | 순매출 ≤ 0 반환이 정상경로 필드 포함, `_consecutive_negative_profit()` 헬퍼로 정의 통일 |
| RS04-L06 | 계약 §5 `risk` 필드 열거에 `score_version`·`policy_status` 추가 |
| RS04-L08 | `demo/build.py._completed_quarter()` — 요청의 market 분기를 완결분기까지 절단 |

라운드 2 후: **25개 unittest 통과**, 데모 재생성 결정론(2회 바이트 동일), 헤드라인 데모 결과(위험 8/주의 4/정상 6/partial 2) 불변.

**미해결**: RS04-D13 수정의 독립 QA 재검증 (2라운드 자동 루프 소진 — dev PR 전 최종 QA 1회). **사람 승인**: 순매출 0 처리 수치(score 95 provisional), 가중치·임계값·SR-05 트리거, flapping 히스테리시스, SR-03 반경·유사업종 매핑.

## 9. 잔여 관찰

- 롤링 타임라인에서 경계(70점) 근처 가맹점이 월별로 주의↔위험 flapping (실 P&L·시장 노이즈). D06 수정으로 일부 완화. UI 히스테리시스 여부는 정책 결정
- `slow_margin_squeeze` 중 실 시장이 강한 상권은 정상으로 남음 — 2층 설계 의도이나 임계값 검토 시 확인

## 10. RS-03 PR11-fix — 계약 v1.1 반영 (2026-09-05)

- 발단: `Idea1thon/BE` PR #11 중간 백엔드 리뷰 → 계약 v1.1(`artifacts/risk-siren/20-method/input-output-contract.md`) + 대응표(`pr11-review-response.md`) + `document/risk-siren/adr/ADR-002-integration-ownership-and-identifiers.md`.
- 범위: 코드 반영만. 가중치·임계값·문구·발송·업종 매핑 등 기존 provisional 항목은 그대로 미결.
- 테스트: 기존 25개 unittest 전부 유지 통과 + 신규 20개 추가 = **45개 unittest 통과**. 실행 환경에 `pydantic`/`fastapi`가 없어 `/Users/parkjunwoo/miniconda3/envs/capston_env/bin/python`(pydantic 2.13.4, fastapi 0.136.1)으로 실행함 — 다른 실행자는 동일 패키지가 설치된 인터프리터를 써야 한다.

### 10.1 변경 파일

| 파일 | 변경 |
| --- | --- |
| `service/siren/models.py` | `CONTRACT_VERSION`, `GradeKo`/`RiskLevel`/`GRADE_TO_RISK_LEVEL`/`grade_to_risk_level()` 단일 매핑 신설. `BranchLocation.trade_area_code`/`x_5181`/`y_5181` 선택화, `admin_dong_code_system` 선택 필드 추가. `RiskOptions.grade_policy`(strict 기본) 신설. `RiskSirenResponse` 를 9블록 타입화(`BranchIdentity`,`RiskResult`,`Layers`/`LayerResult`,`EvidenceItem`,`MissingDataItem`,`DataProvenance`/`ProvenanceSignal`,`AlertPayload`/`AlertRecipient`,`FinancialProductsBlock`,`ExplanationBlock`) — `components`/`review_signal`/`projections`는 dict 유지. `HqSummaryResponse`에 `risk_level_distribution`,`alert_candidate_count` 추가, `unread_alert_count`는 타입 자체를 `None` 리터럴로 고정(항상 null, 값 대입 시 ValidationError). franchise_id/branch_id pass-through 규칙을 docstring/주석으로 명시 |
| `service/siren/pipeline.py` | `grade_policy` 분기 로직 추가: `strict`(기본, 기존 동작 그대로 — 회귀 테스트로 바이트 단위 확인), `renormalized_partial`(층별 결측 신호를 제외하고 남은 가중치로 재정규화, 계약 §6), `branch_only_provisional`(시장 층을 완전히 배제하고 이미 계산된 `branch_risk.score`를 그대로 재사용 — 재계산 아님, ADR-001). 비-strict 모드는 `calculation_status`를 항상 `"partial"`로 고정. `risk_level`을 `grade_to_risk_level()` 한 곳에서만 파생해 `risk`/`alert`/`financial_products`에 복사. `financial_products`를 등급 신호만 담도록 축소(`owner`/`status="grade_only"`/`items=[]`). `contract_version`을 최상위에 추가 |
| `service/siren/alerts.py` | `build_alert()`에 `risk_level`(파라미터로 받음, 재계산 안 함), `grade_policy` 인자 추가. `should_fire = (grade_policy=="strict") and grade=="위험" and score is not None` — **비-strict 모드에서는 절대 True가 될 수 없도록 이 한 곳에서만 게이트**. `suppressed_reason`, `dispatch_owner="middle_backend"` 필드 추가 |
| `service/siren/hq_summary.py` | `alert_candidate_count`(이번 요청 내 `should_fire=true` 건수) 신설, `unread_alert_count`는 항상 `None` 반환(이름 그대로 두되 의미상 deprecated), `risk_level_distribution`을 각 branch 결과의 이미 파생된 `risk.risk_level` 값을 세기만 해서 구성(재파생 없음) |
| `tests/test_risk_siren.py` | `RiskSirenV11ContractTests` 클래스 20개 케이스 추가 (아래 10.3) |

### 10.2 계약 대비 확인/미반영 사항

- **trade_area_code/x_5181/y_5181 선택화와 SR-03**: 계약 감사대로 이 세 필드는 계산 경로에서 전혀 읽히지 않는다. SR-03(competition) 의 missing/partial 처리는 이미 `market_data.competition`/`radius_m` 유무로만 판정하고 있었고, 이번 변경으로 새 코드가 필요하지 않았다 — `test_trade_area_code_and_coords_are_optional`, `test_missing_competition_still_partial_regardless_of_location_fields` 로 이 사실을 회귀 고정했다.
- **grade_policy 비-strict 모드의 실제 산식**: 계약 §6이 제시한 규칙(재정규화, branch-only 재사용, 항상 partial, 항상 should_fire=false)을 코드로 옮겼다. 다만 이 산식 자체는 계약 §9-A에서 "사람 승인 필요"로 표시된 **provisional** 값이다. 이번 라운드는 "선택하면 안전하게 동작하는 기계"를 만든 것이지, 운영에서 이 모드를 켜도 좋다는 뜻이 아니다 — 승인 전에는 호출자가 명시적으로 옵트인해야만 동작한다(기본값 strict).
- **admin_dong_code**: 계약대로 형식 검증을 추가하지 않았다(길이·패턴 무관 통과). `admin_dong_code_system` enum만 선택 필드로 추가.
- **industry_code 폴백**: 변경 없음 — 여전히 10개 enum 밖은 422.
- **알림 문구**: 여전히 생성하지 않음. `alert`는 지시 payload로만 유지.
- **financial_products.items**: 항상 `[]` — 실제 카탈로그 매칭 로직 추가하지 않음 (승인 대기 §9-C).

### 10.3 신규 테스트 케이스 요약 (정상·애매·실패·부정·반복)

| 범주 | 테스트 |
| --- | --- |
| 정상 | `test_trade_area_code_and_coords_are_optional`, `test_admin_dong_code_format_is_not_validated`, `test_response_is_typed_and_round_trips`, `test_risk_level_matches_grade_via_single_mapping`, `test_financial_products_is_grade_only_with_empty_items`, `test_alert_has_dispatch_owner_and_suppressed_reason_fields`, `test_grade_policy_defaults_to_strict`, `test_grade_policy_strict_matches_prior_behavior_exactly`, `test_branch_and_franchise_id_pass_through_unchanged` |
| 애매 | `test_missing_competition_still_partial_regardless_of_location_fields`, `test_grade_policy_branch_only_provisional_grades_without_market`, `test_grade_policy_renormalized_partial_recovers_grade_when_one_signal_missing` |
| 실패(422/ValidationError) | `test_admin_dong_code_system_accepts_only_enum`, `test_grade_policy_rejects_unknown_value`, `test_hq_summary_response_rejects_non_null_unread_count` |
| 부정(안전 기본값·강등 금지·결함 주입) | `test_risk_level_is_null_not_normal_when_uncalculated`, `test_unread_alert_count_is_deprecated_always_null`, `test_grade_policy_never_fires_alert_even_when_grade_is_danger`(비-strict + 등급 위험 조합에서도 `should_fire` 는 항상 False임을 두 정책 모두 subTest로 확인) |
| 반복(결정론) | `test_financial_products_is_grade_only_with_empty_items`(2개 시나리오 반복), `test_grade_policy_renormalized_partial_is_deterministic`(동일 입력 2회 호출 결과 동일) |

`test_grade_policy_never_fires_alert_even_when_grade_is_danger`는 실제로 `branch_only_provisional`에서 `grade="위험"`(score≈74.5)이 나오는 것을 확인한 뒤 그 상태에서도 `should_fire=False`+`suppressed_reason="grade_policy=branch_only_provisional"`인지를 검증한다(수동 재현: 이익붕괴 시나리오 + `market_data=None`).

### 10.4 회귀·결정론 확인

- `python -m unittest tests.test_risk_siren` — 45/45 통과 (capston_env 인터프리터).
- `python -m compileall service/siren tests` — 통과.
- 데모 재생성: `python -m service.siren.demo.build --reuse` 2회 연속 실행 후 `artifacts/risk-siren/demo/` 트리 `diff -rq` 바이트 동일 확인. 헤드라인 결과(위험 8/주의 4/정상 6, danger_ratio 44.44%, avg_score 60.08)는 v1과 동일 — 이번 변경이 점수/등급 로직을 건드리지 않았음을 재확인. 각 결과 JSON에 `contract_version`,`risk.risk_level`,`alert.dispatch_owner`/`suppressed_reason`,`financial_products.status="grade_only"` 가 새로 포함됨(필드 추가이므로 기존 대비 diff는 예상된 것).

### 10.5 QA 요청 시 전달 사항

- 코드: `service/siren/models.py`, `pipeline.py`, `alerts.py`, `hq_summary.py` (api.py는 응답 타입이 자동으로 강화되어 변경 없음).
- 테스트: `tests/test_risk_siren.py` (`RiskSirenV1Tests` 25개 + `RiskSirenV11ContractTests` 20개).
- 실행 명령: `/Users/parkjunwoo/miniconda3/envs/capston_env/bin/python -m unittest tests.test_risk_siren -v` (로컬 기본 `python3`에는 pydantic/fastapi 미설치).
- 미검증 영역: (1) `renormalized_partial`/`branch_only_provisional`의 실제 산식은 사람 승인(§9-A) 전이라 QA가 "값이 맞는지"보다 "게이트(항상 partial, 항상 should_fire=false)가 새는지"를 중점 검토해야 함. (2) `HqSummaryResponse`는 계약이 9블록 타입화 대상에 포함하지 않아 여전히 일부 dict 필드(`grade_distribution` 등)로 남아 있음 — 필요 시 별도 라운드에서 타입화 여부 확인 요망. (3) FastAPI 실제 서버 기동(uvicorn) 스모크 테스트는 이번 라운드에서 수행하지 않음(단위 테스트가 `analyze_risk`/`hq_summary` 함수를 직접 호출).
