# 위험 사이렌 입력·출력 계약 (RS-02) — v1

- 실행일: 2026-09-04
- 선행: `artifacts/risk-siren/10-analysis/signal-audit.md` (current)
- 상태: current / 사람 승인 필요 (가중치·임계값·SR-05 경계·경고 문구)
- score_version: `risk-siren-v1-provisional` (v0 → v1: 5신호, 2층 구조, 월별 운영보고서, 보조 리뷰)

## 1. 경계

```
중간 백엔드 (인증·권한·입력 수집·화면 집계)
  -> POST /internal/risk-sirens/analyze          단일 가맹점 위험 결과
  -> POST /internal/risk-sirens/hq-summary       본사 집계 (analyze 결과 N건 투영)
  <- risk result + evidence + alert event + data_provenance + projections
```

- `service/siren/`는 순수: 요청 JSON → 결과 JSON. 파일·네트워크 접근 없음.
- 상권×업종 실측 집계(`market_data`)와 합성 운영보고서·리뷰는 **호출자(중간 백엔드 또는 데모 하네스)가 요청에 담아 전달**한다.
- 데모 하네스(`service/siren/demo/`)가 서울시 CSV 집계 + 합성 생성기로 요청을 만든다. 코어는 그 출처를 모른다.

## 2. 요청 스키마

### 2.1 필수 식별·위치

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `request_id` | str | 비어있지 않음 |
| `franchise_id` | str | 비어있지 않음 |
| `branch_id` | str | 비어있지 않음. 알림 멱등 키·권한 범위의 기준 |
| `brand_name` | str \| null | **표시 전용**. 데이터 조인 키로 쓰지 않음 |
| `as_of` | date (ISO) | 이 날짜의 말일 기준. 이후 월/분기 데이터는 계산에서 제외 |
| `industry_code` | enum | `CS100001` 한식 / `CS100002` 중식 / `CS100003` 일식 / `CS100004` 양식 / `CS100005` 제과점 / `CS100006` 패스트푸드 / `CS100007` 치킨 / `CS100008` 분식 / `CS100009` 호프-간이주점 / `CS100010` 커피-음료 |
| `location.gu_code` | str | 자치구 코드 |
| `location.admin_dong_code` | str \| null | 행정동 코드 |
| `location.trade_area_code` | str | 상권 코드 (`market_data` 조인 키) |
| `location.x_5181` / `y_5181` | float | EPSG:5181 좌표. SR-03 반경 계산용 |

### 2.2 `market_data` — 상권×업종 실측 (SR-01, SR-02.market, SR-03)

```json
"market_data": {
  "source": "seoul_open_data",
  "closure_source": "seoul_open_data:음식점_상권분기_패널",
  "sales_source": "seoul_open_data:추정매출-상권",
  "as_of_quarter": "2026Q1",
  "closure_quarters": [
    {"quarter": "2024Q2", "active_count_end": 120, "new_openings": 3, "closures": 4, "quarter_status": "완전"}
  ],
  "sales_quarters": [
    {"quarter": "2024Q2", "amount_krw": 41234000000, "txn_count": 512345}
  ],
  "competition": {
    "radius_m": 250,
    "same_industry_new_recent_3m": 2, "same_industry_new_previous_3m": 1,
    "similar_industry_new_recent_3m": 3, "similar_industry_new_previous_3m": 2,
    "similar_industry_codes": ["CS100007", "CS100010"],
    "active_only": true,
    "source": "seoul_open_data:음식점_인허가_서울"
  }
}
```

| 규칙 | 내용 |
| --- | --- |
| `closure_source` / `sales_source` | (선택) 신호별 정확 출처. 없으면 `source` 사용. Evidence 의 `source` 로 전파 |
| 완결 분기만 | `as_of` 를 포함하는 분기는 분기말이 `as_of` 이하일 때만 사용 (미완결 분기 제외) |
| `closure_quarters` | 시간순. 각 분기 `closures ÷ 직전 분기 active_count_end × 100` = 분기 폐업률. 첫 분기는 당분기 값 폴백 |
| rolling | `rolling_2q` = 최근 2분기 `closures` 합 ÷ 창 시작 직전 분기 `active_count_end` × 100. `rolling_4q` 동일. 별도 필드로 응답 |
| `quarter_status` | `부분`이 최신 분기이거나 rolling 창에 포함되면 `closure.status = "partial"` + `window_has_partial_quarter = true` + uncertainty 강제 |
| 분모 0 | 직전 분기 `active_count_end` = 0 → `not_calculable` |
| 정합성 | 산출 폐업률 > 100% → `rate_exceeds_100 = true` + `status = "partial"` + uncertainty (패널 입력 이상) |
| `sales_quarters` | `amount_krw`는 분기 합계(카드매출 추정치). 절대액 신뢰 금지, 추세만. 최근 분기 vs 직전 분기, 최근 vs 전년 동분기 변화율 |
| `competition` | **`radius_m` 필수** — 없으면 SR-03 `status = "missing"`, 점수 미산출, `missing_data` 기재 (반경 모르는 카운트로 점수를 만들지 않음). `active_only=false`면 uncertainty |
| `market_data` 누락 | `market_risk` 층 전체 `missing` → 종합 점수 `partial` |

### 2.3 `branch_reports` — 누적 월별 운영보고서 (SR-02.branch, SR-05)

배열, 시간순, 월 중복 불가. `month` `YYYY-MM`. `as_of` 이후 월은 계산 제외(응답 `excluded_future_months`).

```json
{
  "month": "2026-02",
  "sales": {
    "hall":     {"credit": 21000000, "cash": 3000000, "simple_pay": 9000000},
    "delivery": {"baemin": 12000000, "coupang_eats": 7000000, "other": 1000000},
    "takeout":  {"credit": 4000000, "cash": 500000, "simple_pay": 2000000}
  },
  "deductions": {"customer_refund": 400000, "own_coupon_discount": 2600000},
  "cogs":     {"food_ingredients": 19000000, "sub_materials": 1200000, "alcohol": 3000000,
               "beverage": 1500000, "inventory_begin": 5000000, "inventory_end": 5200000},
  "labor":    {"fulltime": 9000000, "parttime": 6500000, "four_major_insurance": 1400000,
               "meal_welfare": 700000, "short_term": 800000},
  "variable": {"platform_fee": 3800000, "delivery_agency_fee": 1900000, "supplies": 900000,
               "utilities": 2100000, "marketing_ad": 1200000},
  "opex":     {"rent_mgmt": 7500000, "equipment_rental": 600000, "telecom_it": 300000,
               "tax_bookkeeping": 300000, "insurance": 250000, "card_fee": 1300000},
  "finance":  {"loan_interest": 1200000, "other_misc": 400000},
  "sales_source": "synthetic_pos",
  "cost_source":  "synthetic_self_reported"
}
```

| 폼 항목 → 키 매핑 | |
| --- | --- |
| 홀 매출 신용카드/현금/간편결제 | `sales.hall.credit/cash/simple_pay` |
| 배달 매출 배민/쿠팡이츠/기타 | `sales.delivery.baemin/coupang_eats/other` |
| 포장 매출 신용카드/현금/간편결제 | `sales.takeout.credit/cash/simple_pay` |
| 고객 환불 / 자체 할인 쿠폰 적용액 | `deductions.customer_refund/own_coupon_discount` |
| 당월 식자재 / 부자재 매입 / 주류 / 음료 / 기초·기말 재고액 | `cogs.food_ingredients/sub_materials/alcohol/beverage/inventory_begin/inventory_end` |
| 정규직·파트타임 급여 / 4대 보험료 / 식대·복리후생비 / 단기 인력 급여 | `labor.fulltime/parttime/four_major_insurance/meal_welfare/short_term` |
| 플랫폼 수수료 / 배달 대행료 / 소모품비 / 수도광열비 / 마케팅·광고비 | `variable.platform_fee/delivery_agency_fee/supplies/utilities/marketing_ad` |
| 임차료·관리비 / 기기 렌탈료 / 통신·IT / 세무·기장 대행료 / 보험료 / 카드 수수료 | `opex.rent_mgmt/equipment_rental/telecom_it/tax_bookkeeping/insurance/card_fee` |
| 대출 이자 / 기타 잡비 | `finance.loan_interest/other_misc` |

| 검증 | |
| --- | --- |
| 모든 금액 | `>= 0` |
| `month` | `YYYY-MM`, 중복 금지, `as_of` 이후 제외 |
| `sales_source` | `pos` \| `synthetic_pos` \| `self_reported` \| `synthetic_self_reported` |
| `cost_source` | `self_reported` \| `synthetic_self_reported` \| `pos` \| `synthetic_pos` |
| 파생 원가 | `cogs` = 식자재+부자재+주류+음료 + (기초재고 − 기말재고). 기말>기초로 원가 음수 시 0 하한 + uncertainty |
| `branch_reports` 누락 | 최근 6개월 구간 불완전 → `branch_risk` 층 `partial`/`missing` |
| **최근 3개월 순매출 합 ≤ 0** | 영업 정지에 준하는 최고위험. SR-05 `score = 95`(provisional), `status = "calculated"`, `net_sales_nonpositive = true`, `operating_margin_recent_3m_pct = "not_calculable"`. branch 층이 SR-05 단독으로 산출 — **누락으로 처리하지 않음** |

### 2.4 `reviews` — 보조 (SR-04), 생략 가능

```json
"reviews": {
  "source": "synthetic_reviews",
  "records": [
    {"review_id": "rv-0001", "branch_id": "br-001", "written_at": "2026-02-11",
     "rating": 2, "text": "...", "sentiment_label": "부정"}
  ]
}
```

| 규칙 | |
| --- | --- |
| `records` 비었거나 `reviews` 생략 | `review_signal.status = "missing"` — **정상**, 종합 점수 영향 없음 |
| `rating` | 1~5 정수 |
| `sentiment_label` | `긍정` \| `부정` \| `중립` |
| `source` | `synthetic_reviews` \| `naver_place` \| `kakao_map` \| `delivery_app` |
| `branch_id` | (선택) 점포 연결 키. 요청 `branch_id` 와 다르면 해당 레코드 제외 + uncertainty (RS-T07) |
| `written_at` | ISO date, `as_of` 이후 제외 (제외 시 uncertainty) |

### 2.5 `options`

| 필드 | 값 | 기본 |
| --- | --- | --- |
| `llm_mode` | `disabled` \| `explanation_only` \| `explanation_and_review_assist` | `explanation_only` |
| `send_notifications` | bool | `false` — `true`는 승인된 dispatch adapter 없이 422 |

v1 에서 `explanation_and_review_assist` 는 **미구현** — LLM 런타임 미연결이라 `explanation_only` 와 동일 동작(결정론 템플릿). 계약에는 남겨두되 실제 LLM 분류 보조는 후속.

## 3. 응답 스키마

```json
{
  "request_id": "...",
  "branch": {"franchise_id": "...", "branch_id": "...", "brand_name": "...",
             "as_of": "2026-03-31", "industry_code": "CS100009", "trade_area_code": "3120189"},
  "risk": {
    "score": 63.2, "grade": "주의",
    "score_version": "risk-siren-v1-provisional",
    "calculation_status": "calculated",
    "policy_status": "provisional",
    "composite_basis": ["SR-01", "SR-02.market", "SR-03", "SR-02.branch", "SR-05"],
    "excludes": ["SR-04"]
  },
  "layers": {
    "market_risk": {"score": 58.0, "status": "calculated"},
    "branch_risk": {"score": 71.5, "status": "calculated"}
  },
  "components": {
    "closure":       {"score": ..., "quarter_rate": ..., "rolling_2q_rate": ..., "rolling_4q_rate": ...,
                      "active_count_end": ..., "status": "calculated", "source": "..."},
    "sales_decline": {
      "market": {"score": ..., "recent_quarter_change_pct": ..., "yoy_change_pct": ..., "status": "..."},
      "branch": {"score": ..., "recent_3m_change_pct": ..., "previous_3m_change_pct": ...,
                 "recent_period_missing_months": [], "status": "..."}
    },
    "competition":   {"score": ..., "radius_m": 250, "same_new_recent_3m": ..., "same_new_delta": ...,
                      "similar_new_recent_3m": ..., "weighted_new_count": ..., "status": "...", "source": "..."},
    "profitability": {"score": ..., "operating_profit_recent_3m": ..., "operating_margin_recent_3m": ...,
                      "operating_margin_previous_3m": ..., "labor_ratio_recent_3m": ...,
                      "coupon_ratio_recent_3m": ..., "loan_interest_trend": "...",
                      "consecutive_negative_months": 0, "status": "..."}
  },
  "review_signal": {
    "status": "calculated",
    "sub_score": 71.0,
    "direction": "worsening",
    "review_count": 42, "negative_count": 19,
    "negative_ratio": 0.4524, "negative_ratio_previous_period": 0.28,
    "positive_ratio": 0.40,
    "watchlist_flag": true,
    "evidence_id": "ev-review-neg-ratio",
    "model": "deterministic-count-v0"
  },
  "evidence": [
    {"evidence_id": "ev-closure-rolling-2q", "signal_id": "SR-01", "layer": "market",
     "value": 8.33, "unit": "percent", "period": "2025Q4/2026Q1",
     "grain": "trade_area-industry-quarter", "source": "seoul_open_data:음식점_상권분기_패널",
     "synthetic": false, "supports": "최근 2분기 폐업률"}
  ],
  "missing_data": [{"signal_id": "SR-04", "reason": "..."}],
  "uncertainty": ["가중치와 기준값은 provisional 정책이며 운영 적용 전 사람 승인이 필요합니다."],
  "excluded_future_months": [],
  "data_provenance": {
    "contains_synthetic": true,
    "disclosure": "가맹점 매출·손익·리뷰는 대회 데모용 합성 데이터입니다. 상권×업종 폐업률·시장 매출·경쟁업체 등장은 서울시 공개데이터 실측입니다.",
    "by_signal": [
      {"signal_id": "SR-01",        "layer": "market",    "source": "seoul_open_data:인허가_상권분기_패널", "synthetic": false},
      {"signal_id": "SR-02.market", "layer": "market",    "source": "seoul_open_data:추정매출",            "synthetic": false},
      {"signal_id": "SR-03",        "layer": "market",    "source": "seoul_open_data:음식점_인허가_서울",   "synthetic": false},
      {"signal_id": "SR-02.branch", "layer": "branch",    "source": "synthetic_pos",                       "synthetic": true},
      {"signal_id": "SR-05",        "layer": "branch",    "source": "synthetic_self_reported",             "synthetic": true},
      {"signal_id": "SR-04",        "layer": "auxiliary", "source": "synthetic_reviews",                   "synthetic": true}
    ]
  },
  "alert": {
    "event_type": "branch_risk_evaluated",
    "event_id": "evt-...", "idempotency_key": "...",
    "branch_id": "...", "grade": "주의", "previous_grade": null, "score": 63.2, "as_of": "2026-03-31",
    "recipients": [
      {"role": "branch_owner", "channels": ["in_app", "email"]},
      {"role": "franchise_hq", "channels": ["in_app", "email"]}
    ],
    "report_link": null,
    "should_fire": false,
    "dispatch_status": "disabled",
    "evidence_ids": ["..."]
  },
  "financial_products": {"status": "catalog_match_pending", "items": []},
  "explanation": {"text": "...", "evidence_ids": ["..."], "model": "deterministic-template-v1"},
  "projections": {
    "branch_owner": {
      "branch_id": "...", "score": 63.2, "grade": "주의",
      "components": "...(full detail for own branch)...",
      "review_signal": "...", "recommended_actions": [], "financial_products": [],
      "report_link": null
    },
    "franchise_hq": {
      "branch_id": "...", "score": 63.2, "grade": "주의",
      "component_summary": "...(signal-level status only)...",
      "review_watchlist_flag": true
    }
  }
}
```

### 3.1 점수·등급 규칙

| 항목 | 규칙 |
| --- | --- |
| `market_risk.score` | `w1·closure + w2·sales_market + w3·competition` (가중치 provisional) |
| `branch_risk.score` | `w4·sales_branch + w5·profitability` (가중치 provisional) |
| `risk.score` | `wm·market_risk.score + wb·branch_risk.score`, 두 층 모두 `calculated`일 때만 |
| 한 층이라도 미계산 | `risk.score = null`, `grade = null`, `calculation_status = "partial"`, `uncertainty`에 사유 |
| `grade` | `< 40` 정상 / `< 70` 주의 / `>= 70` 위험 (임계값 provisional, 사람 승인 필요) |
| `review_signal` | 종합 점수 **미포함** (가중치 0). `sub_score`·`watchlist_flag`만 |
| 예측 필드 | `success_probability`, `closure_probability`, `profit_forecast`, `vacancy_rate` 등 **계약 금지**. `extra="forbid"`로 거부 |

### 3.2 Evidence 필수 메타

`evidence_id, signal_id, layer, value, unit, period, grain, source, synthetic, supports` — 전부 필수. 계산된 값만 Evidence 생성. `synthetic=true`면 화면·설명에 합성 표시.

### 3.3 알림

| 항목 | 규칙 |
| --- | --- |
| 멱등 키 | `sha256("branch_id|as_of|score_version")[:24]` (구분자 `|`, 공백 없음) |
| `event_type` | `branch_risk_evaluated` — 매 평가의 **현재 상태 스냅샷**. 등급 전이(이전→현재) 감지는 중간 백엔드가 `previous_grade` 를 보관·비교. 이 서비스의 `previous_grade` 는 항상 `null` |
| `should_fire` | `grade == "위험"` AND `score is not None` |
| `dispatch_status` | 항상 `"disabled"` (승인·adapter 전) |
| 수신자 | `branch_owner`, `franchise_hq` 동일 이벤트, 권한별 투영은 `projections`에서 |
| `send_notifications=true` | 422 |

### 3.4 권한별 투영 (`projections`)

| 규칙 | |
| --- | --- |
| `branch_owner` | 본인 `branch_id` 결과만. 전체 components·review_signal·조치 제안·금융상품·본인 보고서 링크 |
| `franchise_hq` | 해당 branch의 점수·등급·신호별 status 요약·watchlist 플래그. **다른 가맹점 링크·타 프랜차이즈 결과 금지** (RS-T11: 위반 시 Critical) |
| 교차 검증 | 두 투영의 `score`·`grade`·`as_of`는 동일 원본에서 나와야 함 (다른 계산 금지, ADR-001) |

## 4. `hq-summary` 엔드포인트

요청: `{request_id, franchise_id, as_of, branch_results: [analyze 응답 N건]}`
응답:
```json
{
  "franchise_id": "...", "as_of": "...",
  "branch_count": 20,
  "grade_distribution": {"정상": 11, "주의": 6, "위험": 3},
  "danger_ratio_pct": 15.0,          // 위험 등급 수 / 계산된 가맹점 수 × 100
  "average_score": 47.3,             // danger_ratio_pct 와 혼용 표시 금지 (pipeline-design 6절)
  "watchlist": [{"branch_id": "...", "grade": "위험", "reasons": ["SR-05", "SR-04"]}],
  "unread_alert_count": 3,
  "data_provenance": {"contains_synthetic": true, "disclosure": "..."}
}
```
`danger_ratio_pct`와 `average_score`는 별도 필드로 두고 화면에서 하나만 선택해 표시.

## 5. 오류 상태

| 조건 | 응답 |
| --- | --- |
| 필수 식별자·`as_of`·`industry_code` 누락 | 422 ValidationError |
| 음수 금액/건수, 잘못된 `month`/`quarter` 형식 | 422 |
| `industry_code` enum 밖 | 422 |
| `send_notifications=true` | 422 |
| `market_data` 없음 | 200, `market_risk.status="missing"`, `calculation_status="partial"` |
| `branch_reports` 6개월 구간 불완전 | 200, `branch_risk.status="partial"`, missing 월 명시 |
| 분모 0 (폐업률·매출 변화) | 200, 해당 지표 `not_calculable` |
| 최근 3개월 순매출 합 ≤ 0 | 200, SR-05 최고위험 (누락 아님, §2.3 참조) |
| `competition.radius_m` 없음 | 200, SR-03 `status="missing"`, `missing_data` 기재 |
| `as_of` 포함 분기 미완결 | 200, market 신호는 직전 완결 분기 기준 |

`risk` 응답 필드는 정확히: `score`(두 층 calculated 시에만, 아니면 `null`), `grade`, `score_version`, `calculation_status`, `policy_status`, `composite_basis`, `excludes`, `branch_floor_applied`. `provisional_composite`·`weighted_composite` 같은 그림자 점수 필드는 노출하지 않는다 (§3 예시와 동일).

## 6. 미결정 · 사람 승인 필요

1. `market_risk`/`branch_risk` 내부 가중치(w1~w5), 층간 가중치(wm/wb) — 전부 provisional
2. 40·70 등급 임계값
3. SR-05 악화 트리거: 영업이익 연속 음수 개월 수, 마진 하락폭, labor_ratio·coupon_ratio 임계
4. SR-03 반경 N (250/500 m), 유사업종 매핑 규칙, "신규" = 인허가일 기준
5. SR-04 보조점수의 향후 종합 편입 여부
6. 경고 문구(정상·주의·위험), 합성 리뷰 텍스트 템플릿
7. 금융상품 카탈로그·노출 규칙
8. 이메일 공급자·실제 발송
9. 데모 창 종료월 확정 (2026-03) 및 20개 상권×업종 선정 목록 (RS-03에서)
