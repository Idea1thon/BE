# 위험 사이렌 v1 (RS-03) 독립 QA — RS-04

- 실행일: 2026-09-04
- 검토 주체: risk-qa-reviewer (구현자와 분리, 검토 대상 미수정)
- 검토 대상 코드 버전: `service/siren/*.py` (mtime ≤ 2026-09-04 18:53), `service/siren/explanation.py` (mtime 18:58 — 검토 중 변경됨, 아래 프로세스 경고 참조), `tests/test_risk_siren.py` (18:53)
- score_version: `risk-siren-v1-provisional`
- Python: `/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python`
- 서울시 원본: `/Users/parkjunwoo/Documents/data-analysis/data` (읽기 전용)

---

# 수정 라운드 2 상태 (builder 기재 — 독립 QA 미재검증) — 2026-09-04

> 이 절은 builder(구현자)가 작성했다. 독립 QA 재검토는 라운드 1까지만 수행됐고, 이 라운드 2 수정은
> 테스트로 자기검증만 됐다. dev 브랜치 통합 PR 전 **최종 독립 QA 1회**를 권고한다.

RS-06(수정 라운드 2)에서 재검토가 지적한 RS04-D13(High) 및 Low 3건 처리:

| ID | 심각도 | 처리 | 근거 |
| --- | --- | --- | --- |
| **RS04-D13** | High | **수정** | `pipeline.py:80-90` — synthetic 판정을 신호별로 분리: `closure_synth = _is_synthetic(md.closure_source or md.source)`, `market_sales_synth`, `comp_synth`. `contains_synthetic`·`disclosure`·`by_signal`·Evidence 가 신호별 플래그 사용. `test_synthetic_sub_source_marks_signal_synthetic`: `closure_source="synthetic_franchise_cohort"` + `source="seoul_open_data"` → `contains_synthetic=true`, disclosure 가 SR-01 을 합성으로 명시, `by_signal SR-01 synthetic=true`, `SR-02.market synthetic=false`. |
| RS04-L05 | Low | **수정** | 순매출 ≤ 0 early-return 이 정상경로 필드(`operating_margin_previous_3m_pct`, `operating_margin_decline_pt`, `operating_profit_recent_3m_krw`, `labor_ratio_recent_3m`, `coupon_ratio_recent_3m`, `delivery_ratio_recent_3m`, `loan_interest_trend`) 를 `None`/`"unknown"` 로 포함. `consecutive_negative_months` 정의를 `_consecutive_negative_profit()` 헬퍼로 통일(`operating_profit < 0` 기준, 두 경로 동일). |
| RS04-L06 | Low | **수정** | 계약 §5 `risk` 필드 열거에 `score_version`·`policy_status` 추가, §3 예시와 일치시킴. |
| RS04-L08 | Low | **수정** | `demo/build.py._completed_quarter()` — 데모 요청의 `closure_quarters`/`sales_quarters`/`as_of_quarter` 를 완결분기까지로 절단. `demo/requests/*.json` 이 더 이상 미래 분기(2026Q3-부분) 미포함. |
| RS04-L01 | Low | 유지 | `explanation_and_review_assist` 무동작 — v1 범위. 계약에 "미구현" 명시 권장(핸드오프 반영). |
| RS04-L02 | Low | 유지 | watchlist reason 에 `grade:위험`·`partial` 토큰 혼재 — 의미 명확, 형식 미세 불일치. |
| RS04-L07 | Low | **수정** | `improvement-log.md`·`final/implementation-handoff.md` 재생성 (RS-07). |

- 자동 검사: unittest **25/25 통과** (신규 8), harness validator 통과.
- 데모 재생성: 2회 연속 바이트 동일(결정론), 헤드라인 위험 8 / 주의 4 / 정상 6 / partial 2 불변.
- 미해결 High: 없음 (D13 수정). **단 독립 QA 재검증 전** — R7 사람 승인 항목은 전부 유지.

---

# 재검토 (수정 라운드 1 후) — 2026-09-04

- 재검토 주체: risk-qa-reviewer (구현자와 분리, 검토 대상 `service/siren/`·`tests/` 미수정)
- 대상 코드 mtime: `pipeline.py`·`risk_signals.py`·`hq_summary.py`·`demo/build.py`·`demo/generate.py` 19:17, `alerts.py`·`models.py` 19:16, `explanation.py` 18:58, `tests/test_risk_siren.py` 19:17
- 계약: `input-output-contract.md` 19:20 (갱신본), `implementation-notes.md` §8 (수정 라운드 표)
- 데모 산출물: 재검토 중 2회 재생성 → 원본 스냅샷 복원 완료 (`diff -rq` 확인)

## R0. 재검토 판정 요약

**여전히 자동 통과 아님 (NOT PASS).** 단, 1차 High 2건은 **모두 해결**. 새 High 1건(RS04-D13)이 D12 수정으로 노출됨.

- Critical: 0
- High: **1** (RS04-D13 — 신규: market 하위 출처(`closure_source`/`sales_source`)가 합성일 때 provenance 가 "모든 신호가 실측"이라 고지. 1차 RS04-D02 와 동일 계열, D12 수정이 만든 표면)
- Medium: 0 미해결 / 1 부분해결 (RS04-L02 형식)
- Low/관찰: 신규 4 (RS04-L05~L08), 1차 L01 유지
- 수정 라운드 1/2 소진. RS04-D13 은 국소적이고 수정 범위가 작음 → 라운드 2 수정 요청, 사람 승인 에스컬레이션은 아직 아님.
- 자동 검사: unittest **24/24 통과** (7개 신규), harness validator 통과.
- 헤드라인 데모 결과 불변: 위험 8 / 주의 4 / 정상 6 / partial 2, danger_ratio 44.4444%, avg_score 60.0773.

## R1. 1차 결함별 현재 상태

| ID | 심각도 | 상태 | 근거 (재현) |
| --- | --- | --- | --- |
| RS04-D01 | High | **해결** (단서) | `calculate_profitability`: 최근 3개월 순매출 합 ≤ 0 → `score=95.0`, `status="calculated"`, `net_sales_nonpositive=true`, `operating_margin_recent_3m_pct="not_calculable"`, 전용 Evidence `ev-profit-net-sales-nonpositive`. 완전 이력(24개월)+`market_data` 있는 말기 가맹점 → `branch_layer=87.17(calculated)`, `grade="위험"`, `should_fire=true`. 정상 계산된 SR-02.branch(72.62)도 함께 사용됨. `fault_r2.py` D01a. **단서**: implementation-notes §8 의 "branch 층이 SR-05 단독으로라도 산출됨" 은 문자 그대로는 미성립 — `_weighted`(`pipeline.py:63-66`)가 SR-02.branch 점수 None 이면 branch_layer None. 이력 ≤3개월 + 순매출 0 조합(`fault_r2.py` D01b)은 여전히 `partial`·무경보. 이는 "데이터 부족 → partial" 원칙과 일치하므로 차단 아님 (R4·R7 참조). |
| RS04-D02 | High | **해결** (D13 로 계열 잔존) | `market_synth` 을 disclosure·`by_signal`·Evidence·`explanation.text` 에 반영. `source="synthetic_*"` → disclosure "합성 데이터가 포함되어 있습니다: SR-01/02.market/03(시장)." `test_disclosure_not_false_when_market_is_synthetic`, `fault_r2.py` D02a. **그러나** `_is_synthetic` 이 `market_data.source` 만 검사하고 D12 가 추가한 `closure_source`/`sales_source` 는 안 봄 → RS04-D13. |
| RS04-D03 | Medium | **해결** | `risk` 딕셔너리 키 = `score, grade, score_version, calculation_status, policy_status, composite_basis, excludes, branch_floor_applied`. `provisional_composite`/`weighted_composite` 제거(1차 재현 스크립트가 KeyError). `branch_floor_applied` 는 `both_calculated` 아닐 때 `false`. `test_no_shadow_composite_field_when_partial`, `fault_r2.py` D03. |
| RS04-D04 | Medium | **해결** | 응답 최상위 `excluded_future_months`(정렬된 리스트), `RiskSirenResponse` 모델 필드. `fault_r2.py` D04 → `["2026-07","2026-09"]`. free-text uncertainty 도 병행. `test_excluded_future_months_is_structured`. |
| RS04-D05 | Medium | **해결** | 커밋된 `demo/` 산출물이 현재 코드로 재생성한 결과와 **바이트 동일**. 2회 연속 재생성도 동일(결정론). R6. |
| RS04-D06 | Medium | **해결** | `_last_completed_quarter_index(as_of)`: 분기말 ≤ as_of 인 분기만. `as_of=2026-03-31→2026Q1`, `2026-03-30→2025Q4`, `2026-02-28→2025Q4`, `2026-01-01→2025Q4`. 완결분기 0개 → `status="missing"`+`missing_data`+grade null(안전). `calculate_market_sales` 도 동일 컷오프. `test_incomplete_quarter_not_counted_as_current`, `fault_r2.py` D06/D06b. 관찰: 데모 요청이 여전히 미래 분기(2026Q2·2026Q3-부분)를 담아 보냄(계산기는 정확 제외) → RS04-L08. |
| RS04-D07 | Medium | **해결** | `competition.radius_m is None` → `score=None`, `status="missing"`, `missing_data` 에 SR-03, SR-03 Evidence 미생성. `test_competition_without_radius_is_missing`, `fault_r2.py` D07. |
| RS04-D08 | Medium | **해결** (플래그 방식) | 산출 폐업률 > 100% → `rate_exceeds_100=true`, `closure.status="partial"`, uncertainty("100% 초과"), market 층 partial → grade null. `fault_r2.py` D08 (`quarter_rate=1175.29%` → grade null). 입력 거부는 아님(계약 §2.2 가 플래그+partial 로 명시). |
| RS04-D09 | Medium | **해결** | `ReviewRecord.branch_id: str \| None`. 요청 `branch_id` 와 불일치 레코드 제외 + uncertainty("다른 branch_id"), `branch_id=None` 은 유지. `fault_r2.py` D09 → `review_count=2` (br-001 + null). `test_mismatched_review_branch_id_excluded`. |
| RS04-D10 | Medium | **해결** (명명·문서) | `event_type="branch_risk_evaluated"`, `previous_grade: null`, 주석·계약 §3.3 이 "전이 감지는 중간 백엔드 책임" 명시. `should_fire = grade=="위험" and score is not None` 불변. `fault_r2.py` D10. |
| RS04-D11 | Medium | **해결** | `rolling()` 이 창 안에 `부분` 분기 있으면 basis status `partial` 로 강등, `window_has_partial_quarter` 플래그, pipeline 이 uncertainty 강제. `fault_r2.py` D11 (rolling-2q 창에 부분 → status partial). D11b (부분이 -3, rolling-2q 창 밖) → basis status calculated 유지하되 `window_has_partial_quarter=true` + uncertainty (보수적, 허용). |
| RS04-D12 | Medium | **해결** (D13 부작용) | `MarketData.closure_source`/`sales_source` → Evidence·`by_signal` 의 `source` 로 신호별 분리. 데모: SR-01 `seoul_open_data:음식점_상권분기_패널`, SR-02.market `seoul_open_data:추정매출-상권`, SR-03 `seoul_open_data:음식점_인허가_서울`. **부작용**: 이 하위 출처가 synthetic 마커를 가져도 `contains_synthetic` 이 놓침 → RS04-D13. |
| RS04-L01 | Low | 미해결 (허용) | `llm_mode="explanation_and_review_assist"` 여전히 수용·무동작. `explanation.py` docstring 에만 언급, 계약 §2.5 에 "미구현" 명시는 없음. v1 범위상 허용. |
| RS04-L02 | Low | **부분해결** | `hq_summary.watchlist` reason 이 `["SR-05","SR-04"]` 형식으로 축약됨. 그러나 `"grade:위험"`, `"partial"` 같은 비-signal-id 토큰이 섞임(`hq_summary.py:37-44`). 계약 예시(`["SR-05","SR-04"]`)와 형식 근접했으나 완전 일치 아님. |
| RS04-L03 | Low | **해결** | 계약 §3.3 이 멱등키 구분자를 `\|`(공백 없음)로 갱신 — 구현과 일치. |
| RS04-L04 | Low | **해결** | 기준일 이후 작성 리뷰 제외 시 `uncertainty` 에 "기준일 이후 작성된 리뷰는 계산에서 제외했습니다." (`pipeline.py:161-162`). `fault_r2.py` L04. |

## R2. 신규 결함

### RS04-D13 — market 하위 출처(`closure_source`/`sales_source`)가 합성일 때 provenance·Evidence 가 "실측"이라 고지  [High]

- 파일: `service/siren/pipeline.py:80` (`market_synth = _is_synthetic(request.market_data.source)` — `.source` 만 검사), `:315-326` (`by_signal` 이 `synthetic` 은 `market_synth`, `source` 는 신호별 `closure.get("source")` 등으로 분리 → 불일치 가능), `:299` (`contains_synthetic`), 전파 `explanation.py:47-48`
- 심각도 근거: 1차 RS04-D02(High)와 **동일 계열**(허위 provenance 고지). D12 수정이 `closure_source`/`sales_source` 를 신호별 Evidence `source` 로 승격했으나, synthetic 판정은 여전히 부모 `source` 만 본다. signal-audit 는 브랜드 코호트 폐업률을 `synthetic_franchise_cohort` 로 생성한다고 명시 → 폐업률만 합성 폴백을 쓰고 나머지 시장 신호는 실측인 배치가 자연스러운 인코딩(`source="seoul_open_data"`, `closure_source="synthetic_franchise_cohort"`). 이때:
  - `contains_synthetic = false`
  - `disclosure = "모든 신호가 실측 데이터입니다."` (거짓)
  - `explanation.text` 에 동일 거짓 문구 전파
  - `by_signal` SR-01: `source="synthetic_franchise_cohort_closure"`, `synthetic=false` (**machine-readable 플래그도 거짓** — 1차 D02 보다 악화)
  - Evidence SR-01: `source="synthetic_..."`, `synthetic=false`
- 재현:
  ```
  /Users/parkjunwoo/Documents/data-analysis/.venv/bin/python \
    /private/tmp/claude-501/-Users-parkjunwoo-Documents-siren/c2865ee3-d792-4bab-967b-0606ca6cbab9/scratchpad/fault_r2.py
  # === D02b === 블록:
  #   contains_synthetic: False
  #   disclosure: 모든 신호가 실측 데이터입니다.
  #   by_signal SR-01: {'source': 'synthetic_franchise_cohort_closure', 'synthetic': False}
  ```
- 수정 방향(제안, builder 결정): synthetic 판정을 신호별로 — SR-01 은 `_is_synthetic(market.closure_source or market.source)`, SR-02.market 은 `_is_synthetic(market.sales_source or market.source)`, SR-03 은 `_is_synthetic(competition.source or market.source)`. `contains_synthetic`·`disclosure`·`synthetic_signals` 를 그 신호별 플래그 집합에서 파생. 현재 20개 데모는 부모·자식 출처가 일관돼 미발현이나 계약 필드로 도달 가능.

### RS04-L05 — `calculate_profitability` 순매출 ≤ 0 분기가 계약·정상경로 필드를 생략  [Low]

- 파일: `service/siren/risk_signals.py:380-389`
- 근거: 순매출 nonpositive early-return 은 `operating_margin_previous_3m_pct`, `operating_margin_decline_pt`, `operating_profit_recent_3m_krw`, `labor_ratio_recent_3m`, `coupon_ratio_recent_3m`, `delivery_ratio_recent_3m`, `loan_interest_trend`, `cogs_clamped_months` 를 담지 않음(정상 경로·계약 §3 응답 예시엔 존재). 또 `consecutive_negative_months` 의 정의가 정상 경로(`operating_profit < 0`)와 이 경로(`operating_profit < 0 or net_sales <= 0`)에서 다름. downstream(`hq_summary`, 투영)은 `.get()` 이라 깨지지 않으나 스키마 불균일.

### RS04-L06 — 계약 §5 의 `risk` 필드 열거가 §3 예시·구현과 불일치  [Low]

- 파일: `artifacts/risk-siren/20-method/input-output-contract.md:318`
- 근거: §5 는 "`risk` 응답에는 `score, grade, calculation_status, branch_floor_applied, composite_basis, excludes` 만 담는다" 고 하나, §3 예시(line 160-167)와 구현은 `score_version`·`policy_status` 도 포함. §5 열거를 §3 에 맞춰 보완 필요.

### RS04-L07 — 핸드오프·개선기록 산출물 stale  [Low]

- 파일: `artifacts/risk-siren/improvement-log.md` (18:56 — 수정 라운드 1 항목 없음, "17개 unittest"·"v0-provisional" 표기 잔존), `artifacts/risk-siren/final/implementation-handoff.md` (14:49 — "scaffold 구성 완료", "실제 FastAPI route 미구현" 등 현재와 불일치)
- 근거: RS04-D05 와 같은 계열(문서-코드 drift). 핸드오프(RS-05) 단계에서 재생성 필요. QA 차단 아님.

### RS04-L08 — 데모 요청이 `as_of` 이후 분기를 담아 전송  [Low]

- 파일: `service/siren/demo/build.py:121-129` (`closure_quarters`/`sales_quarters` 를 전체 배열로 전달), `:125` (`as_of_quarter` 를 `as_of` 포함 분기로 계산)
- 근거: `demo/requests/demo-br-01.json` 의 `closure_quarters` 마지막 항목이 `2026Q3` `부분`(as_of 2026-03-31). 계산기(`_last_completed_quarter_index`)가 정확히 2026Q1 로 절단하므로 결과 영향 없음(D06 로 방어). 그러나 데모가 "미래 데이터를 계산기에 넘기지 않는다" 취지에는 어긋남. `as_of_quarter` 메타도 완결분기 아닌 포함분기.

## R3. 자동 검사 (재검토)

```
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
→ Ran 24 tests ... OK   (신규 7: zero_net_sales / disclosure_market_synthetic / excluded_future_months /
                          incomplete_quarter / competition_without_radius / mismatched_review_branch_id /
                          no_shadow_composite_field)
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python3 .claude/skills/risk-siren-orchestrator/scripts/validate_harness.py
→ PASS
```

커버리지 잔여 공백: RS04-D13 경로(market 부모 실측 + 하위 출처 합성) 테스트 없음. `_last_completed_quarter_index` 의 분기말 직전일(3-30 등) 경계 테스트 없음(재현으로만 확인).

## R4. 회귀 확인

| 검사 | 결과 |
| --- | --- |
| healthy 가맹점이 D01 로직으로 오탐? | 아니오. `fault_r2.py` D01c → grade 정상, `net_sales_nonpositive=None`. 데모 br-01~05 전부 정상 유지, br-10(위험)은 정상 경로로 `net_sales_nonpositive=None`. |
| D06 (a) as_of=분기말 | `2026-03-31 → 2026Q1` (포함). ✅ |
| D06 (b) 분기 첫날/중간 | `2026-01-01 → 2025Q4`, `2026-02-28 → 2025Q4`, `2026-03-30 → 2025Q4`. ✅ (분기말 전일도 제외 — 보수적) |
| D06 (c) 완결분기 0개 | `status="missing"` + `missing_data` SR-01 + grade null. 안전. ✅ |
| SR-01 손계산 (원본 패널 1계열) | 계열 (3120185, CS100001), as_of 2026-03-31, cutoff 2026Q1: `quarter_rate = 4/146×100 = 2.7397` ✅ / `rolling_2q = (1+4)/142×100 = 3.5211` ✅ / `rolling_4q = 17/146×100 = 11.6438` ✅ / `score = clamp(3.5211/12×100) = 29.3425` ✅. `demo/results/demo-br-01.json` 와 일치. market_layer 27.8337 / composite 23.1011 — 1차와 불변. |
| SR-04 격리 | 1차 재현 재실행: 부정 리뷰 100건 주입 후 `risk.score` 불변(29.5422), 층 점수 불변, `composite_basis` 에 SR-04 없음. ✅ |
| 예측 필드 거부 | `success_probability`/`closure_probability`/`profit_forecast`/`vacancy_rate` 전부 `ValidationError`. ✅ |
| 결정론 | `analyze()` 2회 deep-equal True. 데모 2회 재생성 바이트 동일. ✅ |

## R5. 계약 정합성 (갱신본 대조)

| 항목 | 결과 |
| --- | --- |
| `risk` 딕셔너리 그림자 점수 필드 | **없음** — `provisional_composite`/`weighted_composite` 제거 확인. `branch_floor_applied` 는 계약 §5 에 명시된 허용 필드. |
| `excluded_future_months` 노출 | **노출됨** — 최상위 + `RiskSirenResponse` 모델. |
| `alert.event_type` | `branch_risk_evaluated` — 계약 §3.3·응답 예시와 일치. `previous_grade: null` 존재. |
| Evidence `source` 신호별 | SR-01/02.market/03 이 `closure_source`/`sales_source`/`competition.source` 로 분리 — 계약 §2.2·§3.2 일치. |
| 폐업률 정합성(> 100%) | `rate_exceeds_100` + partial + uncertainty — 계약 §2.2 일치. |
| `ReviewRecord.branch_id` | optional — 계약 §2.4 일치. |
| §5 `risk` 필드 열거 vs §3·구현 | **불일치** — RS04-L06 (`score_version`·`policy_status` 누락). |

## R6. 데모 재검토

- `python -m service.siren.demo.build --reuse` → 헤드라인: 위험 8 / 주의 4 / 정상 6 / partial 2 (br-19·br-20). SUMMARY.md·hq_summary.json 과 일치. ✅
- 커밋본 vs 재생성본: `diff -rq` **바이트 동일** (RS04-D05 해결).
- 연속 2회 재생성: `diff -rq` 동일 (결정론). ✅
- `data_provenance`: `contains_synthetic=true`, 2단 disclosure("가맹점 매출·손익·리뷰는 대회 데모용 합성 … 상권×업종 … 서울시 공개데이터 실측"). `by_signal` SR-01/02.market/03 `synthetic=false` + 정확 출처, SR-02.branch/05/04 `synthetic=true`. ✅
- Evidence synthetic 플래그: `ev-closure-*`/`ev-market-sales-qoq`/`ev-competition-new-3m` = false, `ev-branch-sales-3m`/`ev-profit-margin-3m`/`ev-review-neg-ratio` = true. ✅
- `explanation.text` 에 합성 고지 포함. ✅
- br-19 (partial) 투영: `franchise_hq` 에 타 가맹점 링크·`report_link`·상세 components 없음 (`component_status` 문자열만). RS-T11 위반 없음. ✅
- 재검토 중 재생성분은 원본 스냅샷으로 **복원 완료**.

## R7. 사람 승인 필요 (QA 판정 아님 — 1차 목록 유지·갱신)

1. **RS04-D01 순매출 ≤ 0 처리 수치** — `score=95.0` 은 provisional. 또한 이력 ≤3개월 + 순매출 0 조합에서 `branch_layer` 가 여전히 미산출(partial·무경보)되는 것이 허용 정책인지 확정 필요 (현재는 "데이터 부족 → partial" 원칙과 일치).
2. 종합 가중치 w1~w5(0.45/0.35/0.20, 0.35/0.65), 층간 wm/wb(0.35/0.65) — 전부 provisional.
3. 40·70 등급 임계값 — provisional.
4. SR-05 하한 트리거(마진 ≤ −12% 또는 연속적자 ≥ 8 → 하한 75 등) 및 부분점수 블렌드(`0.55·평균 + 0.45·최댓값`).
5. SR-03 반경 250m, 유사업종 매핑(`demo/build.py:25-36`), "신규" 기준.
6. 등급 flapping 히스테리시스 정책 — 데모 br-16 타임라인에서 2025-03~2026-03 사이 위험↔주의 반복(D06 로 market 층 변동 일부 완화, branch 층 70점 근처 P&L 노이즈는 잔존).
7. 경고 문구(정상·주의·위험), 합성 리뷰 템플릿 실존 비지칭 최종 확인.
8. 금융상품 카탈로그·노출 규칙, 실제 이메일·인앱 발송 (현재 `disabled`·`catalog_match_pending` 유지 확인됨).

## R8. 재현 명령 (재검토)

```bash
PY=/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python
SIREN=/Users/parkjunwoo/Documents/siren
SCRATCH=/private/tmp/claude-501/-Users-parkjunwoo-Documents-siren/c2865ee3-d792-4bab-967b-0606ca6cbab9/scratchpad

cd "$SIREN" && $PY -m unittest discover -s tests -p 'test_*.py' -v      # 24 OK
$PY .claude/skills/risk-siren-orchestrator/scripts/validate_harness.py  # PASS

cd "$SIREN" && $PY "$SCRATCH/fault_r2.py"    # D01a/b/c, D02a/b(=D13), D03, D04, D06/D06b, D07, D08, D09, D10, D11/D11b, L04

# 데모 재생성 + stale/결정론 + 복원
cp -R artifacts/risk-siren/demo "$SCRATCH/demo_r2_orig"
cd "$SIREN" && $PY -m service.siren.demo.build --reuse
diff -rq "$SCRATCH/demo_r2_orig" artifacts/risk-siren/demo          # 동일 (D05 해결)
cd "$SIREN" && $PY -m service.siren.demo.build --reuse
diff -rq "$SCRATCH/demo_r2_orig" artifacts/risk-siren/demo          # 동일 (결정론)
rm -rf artifacts/risk-siren/demo && cp -R "$SCRATCH/demo_r2_orig" artifacts/risk-siren/demo   # 복원

# SR-01 손계산 대조
grep ',3120185,CS100001,' /Users/parkjunwoo/Documents/data-analysis/data/인허가/음식점_상권분기_패널.csv
$PY -c "import json;d=json.load(open('artifacts/risk-siren/demo/results/demo-br-01.json'));print(d['components']['closure'])"
```

## R9. 최종 판정 (재검토)

**자동 통과 아님 (NOT PASS).**

- Critical 0, **High 1** (RS04-D13 — 신규, provenance 허위 고지, D12 수정이 노출), Medium 미해결 0, Low 관찰 5.
- 1차 High 2건(RS04-D01, RS04-D02) **모두 해결**. 1차 Medium 10건 중 D03·D04·D05·D06·D07·D08·D09·D10·D11·D12 **전부 해결** (D08 은 플래그+partial 방식, D10 은 명명·문서). 1차 Low 4건 중 L03·L04 해결, L02 부분해결, L01 유지.
- SR-01 폐업률 손계산, 완결분기 컷오프, 종합 점수·등급 매핑, SR-04 격리, 권한 투영(RS-T11), 알림 `disabled`·422·멱등키, 파이프라인·데모 결정론, 커밋 데모 산출물 최신성 — **검증 통과**.
- RS04-D13 은 계약 필드(`closure_source`/`sales_source`)로 도달 가능한 허위 provenance 경로이며 skill 판정 기준상(High = 허위 고지 계열) 통과 차단. 수정 범위 작음(신호별 synthetic 판정) → **builder 라운드 2 수정 요청**. 라운드 2 후에도 High 잔존 시 R7 과 함께 사람 승인 에스컬레이션.
- 운영 배포 가능 상태 아님. R7 사람 승인 항목 미해결.

---

# [1차 기록] 최초 RS-04 QA (수정 전) — 판정 NOT PASS (High 2 / Medium 10 / Low 4)

## 0. 판정 요약

**자동 통과 아님.** High 결함 2건(RS04-D01, RS04-D02)이 미해결. Critical 없음.

- Critical: 0
- High: 2 (RS04-D01 SR-05 매출0 무경보, RS04-D02 provenance 허위 고지)
- Medium: 10
- Low/관찰: 4
- 사람 승인 필요 항목은 8절 참조

builder가 High 2건 + Medium 중 계약 위반분(D03·D04·D05)을 수정한 뒤 RS-04 재검토 필요. 최대 2회 수정 후에도 High가 남으면 사람 승인 에스컬레이션.

---

## 1. 검증 항목별 판정표

| # | 항목 | 판정 | 근거 |
| --- | --- | --- | --- |
| 1 | 자동 검사 (unittest 17건 + harness validator) | 통과 | 2절 |
| 2 | SR-01 폐업률 공식·분모·rolling 정의 (손계산 대조) | 통과 | 3.1절 — 원본 패널 2계열 재계산 일치 |
| 2b | SR-01 분모 0 → not_calculable 유지 | 통과 | `test_zero_denominator_closure_is_not_calculable`, 재현 |
| 2c | SR-01 `부분` 분기 rolling 제외/uncertainty 강제 | **미구현(Medium)** | RS04-D11 |
| 3 | SR-05 net_sales/cogs/operating_profit 매핑 | 통과 | 3.3절 — 계약 §2.3 폼 매핑과 일치 |
| 3b | 기말재고>기초재고 시 원가 0 하한 + uncertainty | 통과 | `reports.py:63-64`, `pipeline.py:150-151` |
| 3c | net_sales = 0 시 비율 not_calculable / 강한 위험 별도 처리 | **실패(High)** | RS04-D01 |
| 4 | 종합 점수 `max(branch, 0.35·m+0.65·b)` + SR-05≥80 하한 | 통과 | 3.2절 — `pipeline.py:188-224` |
| 4b | 40/70 등급 경계 (39.99/40.0/69.99/70.0/65) | 통과 | 결함주입 4, 5.4절 |
| 4c | 두 층 중 하나라도 partial/missing → grade·score null, status partial | 통과 (단 shadow 필드 노출) | RS04-D03 |
| 5 | SR-04 보조 신호 격리 (리뷰 유무에 종합 점수 불변) | 통과 | 5.2절 — 부정 100건 주입 후 score 불변 |
| 5b | `review_signal` 별도 블록, composite_basis에 SR-04 없음 | 통과 | `pipeline.py:260-261` |
| 6 | 결함 주입 최소 세트 | 부분 통과 | 5절 (D01·D02·D07 발견) |
| 7 | 권한 투영 (franchise_hq 링크·타 프랜차이즈·본인 보고서 링크 없음) | 통과 | 6절 — RS-T11 위반 없음 |
| 7b | 두 투영 score/grade 동일 원본 (ADR-001) | 통과 | `pipeline.py:339-373` 모두 `result["risk"]` 참조 |
| 8 | 알림 경계 (dispatch_status=disabled, 422, 멱등키, should_fire) | 통과 (event_type 명칭 이슈) | 5.10·5.12절, RS04-D10 |
| 9 | 결정론 (analyze 2회, 데모 2회) | 통과 (단 커밋 산출물 stale) | 7절, RS04-D05 |
| 10 | 데모 합성 데이터 정합성 (provenance/disclosure/synthetic 플래그) | 통과 (조건부 D02) | 3.4·6절 |

---

## 2. 자동 검사 결과

### 2.1 단위 테스트
```
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```
→ `Ran 17 tests ... OK` (전부 통과).

### 2.2 하네스 validator
```
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python3 .claude/skills/risk-siren-orchestrator/scripts/validate_harness.py
```
→ `PASS: risk-siren harness structure and contracts are present`

### 2.3 커버리지 공백 (테스트는 통과하나 다음이 미검증)
- net_sales ≤ 0 인 달/구간의 SR-05 거동 (RS04-D01) — 테스트 없음
- market_data.source 가 synthetic 이고 branch/review 가 실측인 경우의 disclosure (RS04-D02) — 테스트 없음
- market 층이 `partial`(score 존재)일 때 `risk.score`/`provisional_composite` 관계 (RS04-D03) — 테스트 없음
- `radius_m` 누락 시 SR-03 거동 (RS04-D07) — 테스트 없음
- 폐업률 패널의 내부 정합성 위반 입력 (closures > active) (RS04-D08) — 테스트 없음
- `as_of` 가 분기 중간일 때 해당 분기 포함 여부 (RS04-D06) — 테스트 없음
- `hq-summary`에서 partial 가맹점의 watchlist 편입 — 테스트 없음

---

## 3. 수동 경계면 검토 (손계산)

### 3.1 SR-01 폐업률 — 원본 패널 재계산 (통과)

원본: `data/인허가/음식점_상권분기_패널.csv`. `폐업률` 컬럼 정의 = 당분기 `폐업_수` ÷ **직전 분기** `영업중_수` × 100 (첫 분기는 당분기 값 폴백).

**계열 (3120185, CS100001)**, `as_of=2026-03-31` → cutoff 2026Q1:
| 항목 | 손계산 | 구현 출력 (`demo/results/demo-br-01.json`) | 판정 |
| --- | --- | --- | --- |
| quarter_rate | 4 ÷ 146(2025Q4 영업중) × 100 = 2.7397 | 2.7397 | ✅ |
| rolling_2q_rate | (1+4) ÷ 142(2025Q3 영업중) × 100 = 3.5211 | 3.5211 | ✅ |
| rolling_4q_rate | (6+6+1+4) ÷ 146(2025Q1 영업중) × 100 = 11.6438 | 11.6438 | ✅ |
| score (basis=rolling_2q) | clamp(3.5211 / 12.0 × 100) = 29.3425 | 29.3425 | ✅ |

**계열 (3120189, CS100001)** 파일 `폐업률` 대조: 20212 → 19÷366×100 = 5.19 (파일 5.19 ✅), 20213 → 12÷365×100 = 3.288 (파일 3.29 ✅), 20211(첫) → 11÷366×100 = 3.01 (파일 3.01 ✅).

rolling 분모 = **창 시작 분기의 직전 분기 `영업중_수`** (`risk_signals.py:110-118`, `base = prior_active(min(idxs) - 1)`), 계약 §2.2 및 signal-audit 정의와 일치. 분모 0 → `_rate()`가 `(None, "not_calculable")` 반환, rolling 은 창 미완 시 `missing` (`risk_signals.py:82-85, 112-116`). ✅

### 3.2 종합 점수·등급 (통과)

`demo-br-01` 전체 체인 재계산:
- market_layer = 0.45·29.3425 + 0.35·37.156 + 0.20·8.125 = 27.8337 → 구현 27.8337 ✅
- branch_layer = 0.35·28.8506 + 0.65·16.0848 = 20.5528 → 구현 20.5528 ✅ (prof<80, 하한 미적용)
- weighted = 0.35·27.8337 + 0.65·20.5528 = 23.1011; branch_layer(20.55) < weighted → composite = 23.1011 → 구현 23.1011 ✅
- 23.1011 < 40 → 정상 ✅

`max(branch_layer, 0.35·m + 0.65·b)` 규칙 (`pipeline.py:218-224`), SR-05 ≥ 80 → `branch_layer = max(branch_layer, SR05-12)` (`pipeline.py:194-196`) 모두 구현과 일치. 등급 임계값 40/70 (`risk_signals.py:50-51`), `< 40` 정상 / `< 70` 주의 / `>= 70` 위험 (`pipeline.py:235-240`). 경계 39.99→정상 / 40.0→주의 / 69.99→주의 / 70.0→위험 / 65.0→주의 (5.4절 결함주입으로 확인). ✅

두 층 모두 `calculated` 일 때만 `risk.score`·`grade` 부여 (`both_calculated`, `pipeline.py:231-253`). `sparse` 데모 2건에서 `score=null grade=null calculation_status=partial should_fire=false` 확인 (`demo/results/demo-br-19,20.json`). ✅ (단 RS04-D03 shadow 필드)

### 3.3 SR-05 파생 지표 (매핑은 통과, 경계는 D01)

`reports.py:45-120` 대조:
- `net_sales` = (홀 credit+cash+simple_pay) + (배달 baemin+coupang_eats+other) + (포장 credit+cash+simple_pay) − customer_refund − own_coupon_discount → 계약 §2.3 폼 매핑과 일치 ✅
- `cogs` = food_ingredients + sub_materials + alcohol + beverage + (inventory_begin − inventory_end); `raw_cogs < 0` → `cogs_clamped=True`, `cogs = max(0, raw_cogs)` → 계약 "기말>기초 시 0 하한 + uncertainty" 일치, `pipeline.py:150-151`가 uncertainty 문구 추가 ✅
- `operating_profit` = (net_sales − cogs) − labor_total − variable_total − opex_total − finance_total → taxonomy §2-3, 계약 §2.3 일치 ✅
- `operating_margin` (분기 스코어링용) = Σoperating_profit ÷ Σnet_sales × 100, `net ≤ 0` 이면 `None` (`risk_signals.py:332-338`)

**SR-02.market 손계산** (`demo-br-01`, 추정매출 3120185·CS100001):
- QoQ = (15,197,356,148 − 16,753,606,616) ÷ 16,753,606,616 × 100 = −9.289 → 구현 −9.289 ✅
- YoY = (15,197,356,148 − 15,088,629,780) ÷ 15,088,629,780 × 100 = 0.7206 → 구현 0.7206 ✅
- score = 0.6·clamp(9.289/15·100) + 0.4·0 = 37.156 → 구현 37.156 ✅

### 3.4 데모 합성 데이터 정합성 (통과)

`demo/results/` 표본 (br-01 healthy, br-12 sales_decline, br-16 debt_spiral, br-19 sparse) 확인:
- `data_provenance.contains_synthetic = true`, disclosure 문구 존재 ✅
- `by_signal`: SR-01/SR-02.market/SR-03 `synthetic=false` (실측), SR-02.branch/SR-05/SR-04 `synthetic=true` ✅
- Evidence 각 항목 `synthetic` 플래그가 층별로 정확 ✅ (`ev-branch-sales-3m`, `ev-profit-margin-3m`, `ev-review-neg-ratio` = true / 나머지 = false)
- `explanation.text` 에 합성 고지 문구 포함 ✅
- (조건부) market source 가 synthetic 인 경로는 미검증 → RS04-D02

---

## 4. 자동 검사 vs 수동 검토 정리

| 구분 | 결과 |
| --- | --- |
| 자동 (unittest·validator) | 통과 — 단, D01/D02/D03/D06/D07/D08 커버 안 함 |
| 수동 손계산 (SR-01·SR-02.market·종합) | 통과 — 원본 데이터 재계산 일치 |
| 수동 계약 대조 | D03(shadow 필드), D04(excluded_future_months 미노출), D09(리뷰 branch 키 없음) 발견 |
| 결함 주입 | D01·D02·D07·D08·D10·D11 확인 |
| 재현성 | 파이프라인 결정론 통과 — 커밋 산출물 stale(D05) |

---

## 5. 결함 주입 결과

재현 스크립트: `/private/tmp/claude-501/.../scratchpad/fault_inject.py`, `fault2.py` (읽기 전용, 대상 코드 미수정).

### 5.1 예측 필드 거부 (통과)
`success_probability`, `closure_probability`, `profit_forecast`, `vacancy_rate` — top-level 및 `market_data`/`options` 중첩 삽입 모두 `ValidationError` (`ContractModel.model_config = extra="forbid"`, `models.py:29-30`). ✅

### 5.2 SR-04 격리 — 부정 리뷰 100건 주입 (통과)
```
score no-reviews : 29.5422
score +100 neg   : 29.5422   (동일)
layers 동일       : True
review sub_score : 100.0, watchlist_flag: True (별도 블록에만 반영)
```
종합 점수·층 점수·등급 불변. `composite_basis` 에 SR-04 없음, `excludes=["SR-04"]`. ✅

### 5.3 net_sales 붕괴 (최근 3개월 합 ≤ 0) → **실패 (RS04-D01)**
홀/배달/포장 매출 0, 차감 유지 → `profitability.status="partial"`, `score=None` → `branch_risk={score:None,status:missing}` → `risk.score=None, grade=None, should_fire=False`.
SR-02.branch 는 `score=72.62`로 정상 계산됐으나 `_weighted`가 하나라도 None이면 None 반환(`pipeline.py:63-66`)하여 **폐기됨**.
대조: 매출을 정상의 3%로만 낮추고 차감 0 → `net_sales>0` → margin −2810% → SR-05 score 96.56 → 등급 위험, `should_fire=True`. 즉 **무경보 구간은 "최근 3개월 순매출 합 ≤ 0" 칼날 경계에 한정**되나, 이는 signal-audit가 "매출 0 자체는 강한 위험 신호로 별도 처리"라고 명시한 케이스다.

### 5.4 등급 매핑 경계 (통과)
`pipeline._weighted` 몽키패치로 composite 고정:
```
39.99 → 정상   40.0 → 주의   69.99 → 주의   70.0 → 위험   65.0 → 주의
```
RS-T10(점수 65를 위험으로) 재현 불가 = 매핑 정상. ✅

### 5.5 기준일 이후 운영보고서 (부분 통과 — RS04-D04)
`2026-07` 보고서 추가 → `uncertainty`에 `"기준일 이후 월은 계산에서 제외했습니다: 2026-07"` 추가, 계산에서 제외 (`reports.py:128-137`). 그러나 계약 §2.3이 명시한 **구조화 필드 `excluded_future_months` 는 응답에 없음** — free-text uncertainty 만 존재.

### 5.6 market_data = None (통과)
`calculation_status=partial`, `market_risk.status=missing`, `risk.score=null`, `grade=null`, `missing_data`에 SR-01/02.market/03 사유. 0점·안전 처리 아님. ✅ (`test_missing_market_data_is_partial`도 커버)

### 5.7 market 층 partial 시 shadow 점수 (RS04-D03)
`closure_quarters[-1].quarter_status = "부분"` → `closure.status="partial"` → `market_risk={score:61.92,status:partial}` → `risk.score=None` (정상) **그러나** `risk.provisional_composite=29.5326`, `risk.weighted_composite=29.5326` 노출.

### 5.8 synthetic market + 실측 branch → **실패 (RS04-D02)**
`market_data.source="synthetic_franchise_cohort"`, 모든 `sales_source/cost_source="...(비synthetic)"`, `reviews=None`:
```
contains_synthetic : True
disclosure         : "모든 신호가 실측 데이터입니다."   ← 모순
by_signal SR-01    : synthetic=True (정상)
evidence SR-01     : synthetic=True (정상)
```
machine-readable 플래그는 맞지만 사람이 읽는 disclosure 가 **거짓**. `explanation.py:47-48`이 이 문구를 `explanation.text`로 전파.

### 5.9 폐업률 패널 정합성 위반 (RS04-D08)
`closures=999, active_count_end=5` 주입 → `quarter_rate=1175.29%`, `rolling_2q=1083.87%`, `score=100.0`, `status="calculated"` (경고·플래그 없음). `closures ≤ active_count_end + new_openings` 류의 정합성 검증 부재.

### 5.10 send_notifications = true (통과)
`analyze()` 가 `ValueError` raise (`pipeline.py:329-332`), `api.py:33-34`가 422 로 변환. `test_api_blocks_real_dispatch` 커버. ✅

### 5.11 결정론 (통과)
동일 입력 `analyze()` 2회 deep-equal `True` (debt_spiral 페이로드).

### 5.12 멱등키·이벤트 (통과 / RS04-D10)
`idempotency_key("br-001", 2026-03-31, version)` 2회 동일 = `b194ec0ff199c03db616ec61` (24자, `sha256(branch|as_of|version)[:24]`, `alerts.py:10-12`). `should_fire = grade=="위험" and score is not None` (`alerts.py:24`) — 위험일 때만 true 확인. **다만** `event_type="branch_risk_grade_changed"` 인데 직전 등급 입력이 없어 위험→위험 재평가에서도 매번 발화 (RS04-D10).

---

## 6. 권한 투영 검토 (통과, RS-T11 위반 없음)

`_build_projections` (`pipeline.py:336-374`):
- `franchise_hq`: `branch_id, franchise_id, score, grade, calculation_status, component_status(상태 문자열만), review_watchlist_flag, data_provenance(contains_synthetic+disclosure만)`. **다른 가맹점 링크·타 프랜차이즈 결과·본인 보고서 링크 없음.** `report_link` 키 자체가 없음. ✅ (`test_projection_hq_has_no_other_branch_links` 커버)
- `branch_owner`: 본인 `branch_id` 전체 상세 + `report_link: None` (아직 링크 없음).
- 두 투영의 `score`/`grade`/`calculation_status` 는 모두 동일 `result["risk"]` 딕셔너리 참조 → 별도 계산 없음, ADR-001 준수. ✅
- `hq-summary` (`hq_summary.py`): analyze 결과 N건을 투영만 하고 재계산 없음. `danger_ratio_pct`(위험÷계산됨×100)와 `average_score` 별도 필드 (`HqSummaryResponse`). ✅

데모 `demo/results/demo-br-19.json` `projections.franchise_hq` 확인: 타 가맹점 정보 없음, 링크 없음.

---

## 7. 재현성 결과

| 검사 | 명령 | 결과 |
| --- | --- | --- |
| `analyze()` 2회 | `fault_inject.py` §11 | deep-equal True |
| 데모 연속 2회 재생성 | `python -m service.siren.demo.build --reuse` ×2 | `diff -rq` 바이트 동일 (파이프라인 결정론 ✅) |
| **커밋된 산출물 vs 재생성** | 위 + `diff -rq` (스냅샷 대조) | **9/20 result 파일에서 `explanation.text` 변경** → RS04-D05 |

RS04-D05 상세: `service/siren/explanation.py` 가 `2026-09-04 18:58` 에 수정(리뷰 방향별 문구 분기 추가)됐으나 `artifacts/risk-siren/demo/results/*.json` 은 `18:54` 생성. 재생성 시 `demo-br-06,07,08,10,11,13,14,16,17,20` 의 리뷰 참고 문구가 바뀜. 점수·등급·timeline·hq_summary 는 **모두 불변**. improvement-log "데모 2회 재생성 바이트 동일" 주장은 연속 재생성 한정으로만 참.
(검토 중 QA가 재생성한 산출물은 원본 스냅샷으로 복원 완료 — `diff -rq` 확인.)

---

## 8. 결함 목록

### RS04-D01 — SR-05: 최근 3개월 순매출 합 ≤ 0 인 가맹점에 경보가 뜨지 않음  [High]

- 파일: `service/siren/risk_signals.py:332-338` (`margin()` — `net <= 0` 이면 `None`), `:371-377` (`recent_margin is None` → `score=None, status="partial"`); `service/siren/pipeline.py:63-66` (`_weighted` — 하나라도 None → None), `:188-203` (branch_layer)
- 심각도 근거: signal-audit SR-05 "분모 0" 항목이 "`net_sales = 0` 인 월 → 비율 지표 `not_calculable`, **매출 0 자체는 강한 위험 신호로 별도 처리**"를 명시. 구현은 (1) `not_calculable` 라벨 없이 `partial` 반환, (2) 강한 위험 처리 없음, (3) 그 결과 정상 계산된 SR-02.branch(72.62)까지 폐기되어 `grade=null / should_fire=false`. 폐업 조기 경보 시스템에서 완전 보고 중인 말기 가맹점이 **무경보**가 되는 방향의 오류.
- 재현:
  ```
  /Users/parkjunwoo/Documents/data-analysis/.venv/bin/python \
    /private/tmp/claude-501/-Users-parkjunwoo-Documents-siren/c2865ee3-d792-4bab-967b-0606ca6cbab9/scratchpad/fault_inject.py
  # === 3. net_sales collapse ... === 블록:
  #   profitability status: partial / score: None
  #   branch_layer: {'score': None, 'status': 'missing'}
  #   risk.score/grade: None None / should_fire: False
  ```
- 수정 방향(제안, builder 결정): `net_sales(구간 합) <= 0` 을 SR-05 의 독립 고위험 상태로 정의(예: score 하한 + status `calculated` 또는 명시적 `not_calculable` + branch_layer 가 SR-02.branch 단독으로라도 산출되도록). 계약 §5 "분모 0 → 해당 지표 not_calculable" 과 signal-audit "별도 처리" 를 함께 반영.
- **[재검토 상태: 해결 (단서). R1 참조.]**

### RS04-D02 — data_provenance disclosure 가 합성 포함 시에도 "모든 신호가 실측"이라 고지  [High]

- 파일: `service/siren/pipeline.py:277-282` (`disclosure` 삼항 조건이 `any([sales_synth, cost_synth, review_synth])` 로 **`market_synth` 누락**), 전파: `service/siren/explanation.py:47-48`
- 심각도 근거: `contains_synthetic=true` 와 `disclosure="모든 신호가 실측 데이터입니다."` 가 동시에 출력됨(모순). signal-audit SR-01 이 브랜드 코호트 폐업률을 `synthetic_franchise_cohort` 로 생성한다고 명시 → 실제로 발생 가능한 경로. 화면·설명이 사용자에게 "합성 없음"이라 거짓 고지.
- 재현: `fault_inject.py` `=== 8. ===` 블록 →
  ```
  contains_synthetic: True
  disclosure       : 모든 신호가 실측 데이터입니다.
  ```
- 현재 20개 데모에서는 market 이 항상 실측이라 미발현. 수정: 조건에 `market_synth` 포함, 또는 disclosure 를 `by_signal` 의 synthetic 집합에서 파생.
- **[재검토 상태: 해결. 단 동일 계열 신규 결함 RS04-D13 (하위 출처 미검사). R1·R2 참조.]**

### RS04-D03 — 계약에 없는 shadow 점수 필드 (`provisional_composite`/`weighted_composite`/`branch_floor_applied`)  [Medium]

- 파일: `service/siren/pipeline.py:252-263` (`risk` 딕셔너리)
- 근거: 계약 §3 응답 스키마·설계 문서 전체에 해당 필드 없음. `RiskSirenResponse.risk` 가 `dict` 라 `extra="forbid"` 미적용. `calculation_status="partial"` 이고 `risk.score=null` 인데 `provisional_composite` 가 실제 종합값(예: 29.5326)을 노출 → 계약 §3.1 "한 층이라도 미계산 → risk.score = null" 원칙과 충돌. 외부 UI 가 이 값을 점수로 표시할 여지.
- 재현: `fault_inject.py` `=== 7. ===` → `risk.score: None` 이면서 `provisional_composite: 29.5326`.
- 수정: 필드 제거하거나 `calculation_status != "calculated"` 일 때 `null` 로.
- **[재검토 상태: 해결. shadow 필드 제거, branch_floor_applied 는 계약에 명시적으로 편입. R1·R5 참조.]**

### RS04-D04 — `excluded_future_months` 구조화 필드 미노출  [Medium]

- 파일: `service/siren/pipeline.py:147-149` (uncertainty 문자열만), `service/siren/reports.py:123-137` (`future` 리스트는 계산되나 응답에 미전달)
- 근거: 계약 §2.3 "`as_of` 이후 월은 계산 제외(응답 `excluded_future_months`)". 정보는 free-text uncertainty 에만 존재해 기계 판독 불가.
- 재현: `fault_inject.py` `=== 5. ===` → `"excluded_future_months field present: False"`.
- **[재검토 상태: 해결. 최상위 구조화 필드 + 모델. R1·R5 참조.]**

### RS04-D05 — 커밋된 데모 산출물이 현재 코드와 불일치 (stale)  [Medium]

- 파일: `artifacts/risk-siren/demo/results/*.json` (9건), `service/siren/explanation.py` (18:58 수정) vs 산출물 (18:54)
- 근거: 7절. 재생성 시 `explanation.text` 변경(점수·등급 불변). 핸드오프 전 데모 재생성 필요.
- 재현: `diff -rq <snapshot> artifacts/risk-siren/demo` 후 `python -m service.siren.demo.build --reuse` 재실행 → `explanation.text` 라인만 상이.
- **[재검토 상태: 해결. 커밋본 = 재생성본 바이트 동일. R6 참조. 단 improvement-log·final handoff 는 별도 stale → RS04-L07.]**

### RS04-D06 — market 층이 `as_of` 포함 분기를 완결분기로 취급  [Medium]

- 파일: `service/siren/risk_signals.py:73-74` (`_as_of_quarter` = as_of 를 포함하는 분기), `:94-98`·`:165-169` (cutoff `<=` 로 해당 분기 포함)
- 근거: `as_of=2025-01-31` → `latest_quarter=2025Q1` 사용(2·3월 미도래분 포함). 계약 §2.1 "이후 월/분기 제외", taxonomy §6 "미래 기간 데이터로 과거 위험 계산 금지" 취지와 상충. 데모 롤링 타임라인에서 market_risk 월별 급변(br-16: 63→56→36→16→38…)과 등급 flapping(주의↔위험 반복) 의 한 원인. **최종 스냅샷(as_of=2026-03-31, 2026Q1)** 은 마지막 실측 분기와 일치하므로 헤드라인 데모 결과는 영향 없음.
- 재현: `fault2.py` `=== C. ===` → `latest_quarter used: 2025Q1`.
- 수정: 완결분기(분기말 ≤ as_of)만 포함, 또는 데모가 market_data 분기를 as_of 로 절단.
- **[재검토 상태: 해결. `_last_completed_quarter_index`. R1·R4 참조. 데모 요청은 여전히 미래 분기 전송(계산기가 방어) → RS04-L08.]**

### RS04-D07 — SR-03: `radius_m` 없이도 경쟁 점수 산출  [Medium]

- 파일: `service/siren/risk_signals.py:224-246` (score 는 `:229-230` 에서 무조건 계산, `complete` 판정만 `radius_m` 확인)
- 근거: 계약 §2.2 "`radius_m` ... 미정 시 그 부분 `missing`", RS-T04 "신규 개점일·반경 정의 없음 → 경쟁 점수 미계산". 구현은 `status="partial"` + 점수 산출 + `missing_data` 에 SR-03 미기재. 반경을 모른 채 카운트만으로 만든 점수는 근거가 불완전. 순효과(market 층 partial → 등급 null)는 안전하나 sub-score 가 부적절.
- 재현: `fault2.py` `=== B. ===` → `competition score 34.375, radius_m null, missing_data has SR-03: False`.
- **[재검토 상태: 해결. radius None → status missing, score None, missing_data 기재, Evidence 미생성. R1 참조.]**

### RS04-D08 — market_data 폐업 패널 내부 정합성 미검증  [Medium]

- 파일: `service/siren/models.py:74-86` (`MarketClosureQuarter` — `ge=0` 만), `service/siren/risk_signals.py:82-118`
- 근거: `closures=999, active_count_end=5` → 폐업률 1175%, score clamp 100, `status="calculated"`. RS-T09(연간 분모를 단기 공식으로 가장 / 분모·기간 불일치)류 조작이 방어층 없이 통과. 계약이 조립 책임을 호출자에 위임하나 `closures ≤ active+new` 정도의 sanity check 부재.
- 재현: `fault_inject.py` `=== 9. ===`.
- **[재검토 상태: 해결(플래그 방식). rate > 100% → rate_exceeds_100 + partial + uncertainty → grade null. R1 참조.]**

### RS04-D09 — 리뷰 레코드에 점포 연결 키 없음 → RS-T07 검증 불가  [Medium]

- 파일: `service/siren/models.py:228-233` (`ReviewRecord` = `review_id, written_at, rating, text, sentiment_label` — `branch_id` 없음)
- 근거: signal-audit SR-04 생성 사양과 taxonomy §5 는 "점포 연결 키" 보존을 요구. 계약 §2.4 스키마가 이를 누락. 코어가 다른 점포 리뷰가 섞여 들어와도 탐지 불가. 완화: SR-04 는 종합 점수 가중치 0.
- 재현: `fault2.py` `=== D. ===` → `ReviewRecord fields: ['review_id', 'written_at', 'rating', 'text', 'sentiment_label']`.
- **[재검토 상태: 해결. `ReviewRecord.branch_id` optional, 불일치 제외 + uncertainty. R1 참조.]**

### RS04-D10 — `alert.event_type="branch_risk_grade_changed"` 이나 등급 전이 감지 없음  [Medium]

- 파일: `service/siren/alerts.py:24-26`
- 근거: 직전 등급 입력이 없어 `grade=="위험"` 이면 매 평가마다 발화. 데모 타임라인에서 위험→주의→위험 flapping 시 반복 발화(br-16: 2025-03, 2025-07, 2025-09, 2025-10…). 멱등키는 `as_of` 포함이라 같은 달 재실행만 멱등, 연속 월은 각각 발화. 이벤트명이 "변경"을 함의하나 로직은 "상태". 계약 §3.3 자체가 `should_fire = grade=="위험"` 이라 발화 로직은 계약과 일치하지만, 이벤트 타입 명명 또는 전이 감지 입력이 필요.
- **[재검토 상태: 해결(명명·문서). `event_type="branch_risk_evaluated"`, `previous_grade: null`, 전이 감지는 중간 백엔드 책임으로 계약 §3.3 명시. flapping 히스테리시스는 R7 #6 사람 승인 항목으로 유지.]**

### RS04-D11 — SR-01 `부분` 분기 데이터가 rolling·quarter_rate 에 그대로 반영  [Medium]

- 파일: `service/siren/risk_signals.py:110-140` (`rolling()` 이 `부분` 분기를 창에서 제외하지 않음; `status` 만 `partial` 로 강등)
- 근거: signal-audit SR-01 "`부분` 분기는 폐업/개업 집계 미완 → `partial` 로 표시하고 **rolling 창에서 제외하거나 uncertainty 강제**". 구현은 미완 폐업 수를 그대로 합산. 데모 창(2026-03 종료)에는 `부분` 분기가 안 들어와 미발현이나, 운영/후속 as_of 에서 발현.
- **[재검토 상태: 해결. 창에 부분 분기 → basis status partial + `window_has_partial_quarter` + uncertainty 강제. R1 참조.]**

### RS04-D12 — SR-01/SR-02.market Evidence 의 `source` 가 신호별이 아닌 통합 문자열  [Medium]

- 파일: `service/siren/risk_signals.py:154`·`:210` (`"source": market.source`), `service/siren/pipeline.py:104-124`
- 근거: 데모 Evidence 의 SR-01 `source` = `"seoul_open_data:인허가_상권분기_패널+추정매출+음식점_인허가_서울"` (세 원천 결합). 계약 §3.2·signal-audit 는 신호별 정확 출처(`seoul_open_data:인허가_상권분기_패널`)를 기대. SR-03 은 `competition.source` 로 올바르게 분리됨.
- **[재검토 상태: 해결. `MarketData.closure_source`/`sales_source` → 신호별 Evidence source. 단 이 필드가 synthetic 판정에 미반영 → RS04-D13 (High). R1·R2 참조.]**

### Low / 관찰

- **RS04-L01**: `llm_mode="explanation_and_review_assist"` 수용되나 LLM 런타임 미연결 — `explanation_only` 와 동일 동작. v1 범위상 허용, 계약에 "미구현" 명시 권장. **[재검토: 미해결(허용).]**
- **RS04-L02**: `hq_summary.watchlist` reason 문자열이 계약 예시(`["SR-05","SR-04"]`)보다 장황(`"SR-05 영업이익 연속 적자"` 등). 의미 동일, 형식 불일치. **[재검토: 부분해결. signal-id 축약됐으나 "grade:위험"·"partial" 비-signal-id 토큰 잔존.]**
- **RS04-L03**: 멱등키 조인 문자열이 `"|"` (공백 없음) — 계약 표기 `branch_id | as_of | score_version` 와 미세 상이. 결정론·길이(24) 정상. **[재검토: 해결. 계약 §3.3 이 `|`(공백 없음)로 갱신.]**
- **RS04-L04**: `as_of` 이후 작성 리뷰는 윈도우에서 조용히 제외되며 uncertainty 문구 없음 (`risk_signals.py:429-430`). **[재검토: 해결. `pipeline.py:161-162` uncertainty 추가.]**

---

## 9. 사람 승인 필요 (QA가 판정하지 않음)

1. **RS04-D01 수정 방향** — "매출 0 = 강한 위험 신호" 의 구체 점수·등급 처리 정책 (signal-audit가 별도 처리를 요구하나 수치·트리거는 미정)
2. 종합 가중치 w1~w5(0.45/0.35/0.20, 0.35/0.65), 층간 wm/wb(0.35/0.65) — 전부 `provisional`, 데이터 검증 전
3. 40·70 등급 임계값 — `provisional`
4. SR-05 하한 트리거 (마진 ≤ −12% 또는 연속적자 ≥ 8 → 하한 75; ≤ −5% 또는 ≥ 4 → 하한 55) 및 부분점수 블렌드(`0.55·평균 + 0.45·최댓값`)
5. SR-03 반경 250m, 유사업종 매핑(`demo/build.py:25-36`), "신규" 기준
6. 등급 flapping 히스테리시스 정책 (RS04-D06·D10 과 연동)
7. 경고 문구(정상·주의·위험), 합성 리뷰 템플릿 실존 비지칭 최종 확인
8. 금융상품 카탈로그·노출 규칙, 실제 이메일·인앱 발송 (현재 `disabled`·`catalog_match_pending` 유지 확인됨)

---

## 10. 프로세스 경고

검토 중 `service/siren/explanation.py` 가 변경됨(첫 read 시 단일 문구 → 이후 read 시 리뷰 방향별 분기, mtime 2026-09-04 18:58). QA 는 현재 디스크 버전 기준으로 재검증했으나, RS-04 재검토 전 builder 는 대상 코드를 동결하고 데모 산출물을 재생성(RS04-D05)해야 한다. **[재검토: 라운드 1 후 코드 동결됨(mtime 19:16-19:17), 데모 재생성·바이트 동일 확인.]**

## 11. 재현 명령 모음

```bash
PY=/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python
SIREN=/Users/parkjunwoo/Documents/siren
SCRATCH=/private/tmp/claude-501/-Users-parkjunwoo-Documents-siren/c2865ee3-d792-4bab-967b-0606ca6cbab9/scratchpad

# 자동 검사
cd "$SIREN" && $PY -m unittest discover -s tests -p 'test_*.py' -v
$PY .claude/skills/risk-siren-orchestrator/scripts/validate_harness.py

# 결함 주입 (읽기 전용, 대상 코드 미수정)
cd "$SIREN" && $PY "$SCRATCH/fault_inject.py"      # D01, D02, D03, D07(via 9), D08, D10  (라운드 1 이후 일부 KeyError — fault_r2.py 사용)
cd "$SIREN" && $PY "$SCRATCH/fault2.py"            # D01 대조, D07, D06, D09
cd "$SIREN" && $PY "$SCRATCH/fault_r2.py"          # 재검토용 — D01a/b/c, D02a/b(=D13), D03, D04, D06/D06b, D07, D08, D09, D10, D11/D11b, L04

# SR-01 손계산 대조
grep ',3120185,CS100001,' /Users/parkjunwoo/Documents/data-analysis/data/인허가/음식점_상권분기_패널.csv
$PY -c "import json;d=json.load(open('artifacts/risk-siren/demo/results/demo-br-01.json'));print(d['components']['closure'])"

# 결정론 / stale 산출물 (D05)
cp -R artifacts/risk-siren/demo "$SCRATCH/demo_snap"
cd "$SIREN" && $PY -m service.siren.demo.build --reuse
diff -rq "$SCRATCH/demo_snap" artifacts/risk-siren/demo    # 라운드 1 후: 바이트 동일
rm -rf artifacts/risk-siren/demo && cp -R "$SCRATCH/demo_snap" artifacts/risk-siren/demo   # 복원
```

## 12. 최종 판정

**자동 통과 아님 (NOT PASS).**

- Critical 0, **High 2** (RS04-D01, RS04-D02), Medium 10, Low 4.
- SR-01 폐업률 공식·분모·rolling, SR-02.market, 종합 점수·등급 매핑, SR-04 격리, 권한 투영(RS-T11), 알림 disabled·422·멱등키, 파이프라인 결정론 — **검증 통과**.
- High 2건은 "무경보 방향의 오류"와 "허위 provenance 고지"로 skill 판정 기준상 통과 차단. builder 수정 후 RS-04 재검토. Medium 중 D03·D04·D06·D08·D11·D12 는 계약/문서 정합성 결함으로 재검토 시 함께 확인.
- 운영 배포 가능 상태 아님. 8절 사람 승인 항목 미해결.

**→ 수정 라운드 1 후 재검토 결과는 이 문서 상단 "재검토 (수정 라운드 1 후)" 절 참조. 최종: NOT PASS, High 1 (RS04-D13 신규).**
