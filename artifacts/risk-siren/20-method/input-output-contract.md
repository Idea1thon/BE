# 위험 사이렌 입력·출력 계약 (RS-02) — v1.1

- 실행일: 2026-09-04 (v1) / 2026-09-05 (v1.1 — PR #11 중간 백엔드 리뷰 대응) / 2026-09-07 (v1.2 — 역할별 projection·provider 경계)
- 선행: `artifacts/risk-siren/10-analysis/signal-audit.md` (current)
- 상태: current / 사람 승인 필요 (가중치·임계값·SR-05 경계·경고 문구 + v1.1 신규 승인 항목 §9)
- `score_version`: `risk-siren-v1.2` (기존 점수 로직을 구분하는 저장 호환 버전)
- `contract_version`: `risk-siren-contract-v1.1` (응답에 명시. 점수 버전과 별개로 계약 표면 변경을 추적)
- 리뷰 대응표: `artifacts/risk-siren/20-method/pr11-review-response.md`

> v1.1 요약: 응답 타입화(risk·alert 등), `risk_level` 영문 enum 추가, `location` 좌표·상권코드 선택화,
> 금융상품 축소, 알림 소유권 명시, `unread_alert_count` deprecated, 식별자 pass-through 명문화, 동기 호출 성능 실측.
> 점수·등급 계산식과 가중치는 바뀌지 않았다.
>
> v1.2 변경: ID-only `analyze-trigger`, FMP/IDEATON provider 조회, 점주/HQ projection을
> middle-backend가 인증된 audience별로 필터링하고, 본사 전용 `risk-summary`를 추가했다.

## 0. 이 문서를 읽는 두 독자

| 독자 | 먼저 볼 절 |
| --- | --- |
| 중간 백엔드(호출자) | §1 경계·소유권, §2 요청, §3 응답, §7 인증·권한, §8 호출 방식·성능 |
| 구현자(RS-03 builder) | §3.5 타입화 모델 스펙, §5 오류, §6 degraded 정책, §10 변경 이력 |

## 1. 경계

```
중간 백엔드 (인증·권한·입력 수집·저장·화면 집계·실제 발송·금융상품 매칭)
  -> POST /internal/risk-sirens/analyze          기존 canonical payload 호환 경로
  -> POST /internal/risk-sirens/analyze-trigger  ID-only trigger → provider 조회 → 단일 결과
  -> POST /internal/risk-sirens/hq-summary       본사 집계 (analyze 결과 N건 투영)
  <- risk result + evidence + alert payload + data_provenance + projections
```

- `services/siren/pipeline.py`는 순수 계산을 유지한다. `orchestrator.py`와 provider만
  읽기 전용 FMP/IDEATON DB를 조회하고 canonical 요청으로 매핑한다.
- `analyze-trigger` 요청에는 원시 시장·운영 데이터를 담지 않는다. provider가 branch/report
  ID를 기준으로 조회하며 middle-backend는 인증·상태 저장·화면 projection을 소유한다.
- 데모 하네스(`service/siren/demo/`)가 서울시 CSV 집계 + 합성 생성기로 요청을 만든다. 코어는 그 출처를 모른다.

### 1.1 소유권 분리 (v1.1 신설 — PR #11 P2 대응)

| 관심사 | 소유 | 사이렌이 하는 일 | 사이렌이 하지 않는 일 |
| --- | --- | --- | --- |
| 인증·권한·세션 | 중간 백엔드 | 없음 (내부 전용 엔드포인트) | 토큰 검증, 사용자 조회 |
| 점포·프랜차이즈 마스터 | middle-backend + FMP provider | ID-only trigger에서 읽기 전용 조회, 식별자 **pass-through** (§2.1.1) | Siren이 마스터를 소유하거나 수정하지 않음 |
| 상권·좌표·시장 데이터 | IDEATON provider | 주소→상권·좌표, `store_quarter`, `sales_quarter`, `permitted_establishment` 조회 | middle-backend가 시장 데이터를 조립하지 않음 |
| 위험 점수·등급 | **사이렌** | 결정론적 계산 + Evidence | — |
| 알림 발송 | 중간 백엔드 | 발송 **지시 payload** 생성 (§3.3) | 이메일·인앱 실제 발송, 읽음 상태 |
| 미확인 알림 수 | 중간 백엔드 | 없음 (v1.1에서 deprecated, §4) | 읽음 여부 추적 |
| 금융상품 매칭 | 중간 백엔드 (REQ-OW-07/09/10) | 등급·risk_level만 제공 (§3.4) | 상품 선택, 순위, 최대 3개 규칙 |
| 지역 코드 ↔ 한글명 매핑 | 중간 백엔드 | 코드 그대로 수신·에코 | 명칭 변환 (§8.2) |
| 업종 코드 체계 변환 | 중간 백엔드 | `CS1000xx`만 수신 (§2.1.3) | I2xx ↔ CS1000xx 매핑표 유지 |

원칙: **한 값의 산출 책임은 한 곳에만 둔다.** 두 서비스가 같은 값을 내려주면 화면이 무엇을 믿을지 모호해지고, 불일치가 조용히 발생한다.

## 2. 요청 스키마

### 2.1 필수 식별·위치

| 필드 | 타입 | 필수 | 규칙 |
| --- | --- | --- | --- |
| `request_id` | str | 필수 | 비어있지 않음. 1~128자. 응답에 그대로 에코 |
| `franchise_id` | str | 필수 | 비어있지 않음. 1~64자. **pass-through** (§2.1.1) |
| `branch_id` | str | 필수 | 비어있지 않음. 1~64자. 알림 멱등 키·리뷰 대조·권한 범위의 기준 |
| `brand_name` | str \| null | 선택 | **표시 전용**. 데이터 조인 키로 쓰지 않음 |
| `as_of` | date (ISO) | 필수 | 이 날짜 기준. 이후 월/분기 데이터는 계산에서 제외 |
| `industry_code` | enum | 필수 | §2.1.3의 10개 코드만 허용. 그 밖은 422 |
| `location.gu_code` | str | 필수 | 자치구 코드 5자리 (예: `11680`). 중간 백엔드 값과 이미 일치 확인됨 |
| `location.admin_dong_code` | str \| null | **선택** | 계산에 쓰지 않음. 에코·추적용. 코드 체계 주의 (§2.1.2) |
| `location.admin_dong_code_system` | enum \| null | **선택 (v1.1 신설)** | `seoul_trade_area_admin_dong_8` \| `mois_admin_dong_10` \| `mois_legal_dong_10` \| `unknown` |
| `location.trade_area_code` | str \| null | **선택 (v1.1 변경)** | v1에서는 필수였음. 계산에 쓰지 않고 응답 `branch.trade_area_code`로 에코만 |
| `location.x_5181` / `y_5181` | float \| null | **선택 (v1.1 변경)** | v1에서는 필수였음. 코어는 좌표를 쓰지 않음 (§2.1.4) |

#### 2.1.1 식별자 pass-through (PR #11 P3 답)

`franchise_id`·`branch_id`는 **문자열로 받아 문자열로 돌려준다.** 사이렌은 이 값을 저장하지 않고, 다른 요청의 값과 비교하지 않으며, 어떤 마스터 테이블과도 조인하지 않는다. 사용처는 정확히 세 곳이다.

1. 응답 에코 (`branch.branch_id`, `branch.franchise_id`, `projections.*.branch_id`)
2. `reviews.records[].branch_id`와의 **동일 요청 내** 문자열 일치 검사 (불일치 레코드 제외 + uncertainty)
3. 알림 멱등 키 재료 — `sha256("branch_id|as_of|score_version")[:24]`

**따라서 BIGSERIAL 정수를 문자열로 변환해 보내도 안전하다.** 단 3번 때문에 다음이 계약이다.

- 같은 점포는 **항상 같은 문자열 표현**으로 보낼 것. `"123"`과 `"0123"`, `" 123"`은 서로 다른 멱등 키를 만든다.
- 권장 정규화: `str(int)` (zero-padding·공백·접두사 없음). 요청 값은 앞뒤 공백이 제거된 뒤 사용된다.
- 사이렌은 값의 의미를 검사하지 않으므로, **잘못된 branch_id를 보내면 잘못된 멱등 키가 조용히 생성된다.** 대조 책임은 중간 백엔드에 있다.

#### 2.1.2 행정동 코드 체계 — 감사 결과 (PR #11 P1 답, 추측 금지)

리뷰의 "8자리 vs 10자리" 진단은 **부분적으로만 맞다.** 서울시 원천 데이터로 대조한 결과는 다음과 같다.

| 확인 사실 | 근거 |
| --- | --- |
| 사이렌 데모의 8자리 코드는 서울시 상권분석서비스 `행정동_코드` 체계다 | `data/영역/행정동/서울시 상권분석서비스(영역-행정동).csv` 425행, `data/영역/상권/…(영역-상권).csv`의 `행정동_코드`가 이 집합의 부분집합 |
| 이 8자리는 **행정표준코드(행정동) 10자리의 앞 8자리**와 일치한다 | 청운효자동 `1111051500` → `11110515`, 사직동 `1111053000` → `11110530` (CSV 확인) |
| 서울시 행정동 코드의 6~8번째 자리는 **510~870 범위**뿐이다 (425개 전수) | 강남구: `11680510` 신사동 … `11680750` 수서동 |
| 리뷰가 제시한 `1168010100`의 앞 8자리는 `11680101` → **이 집합에 없다** | `11680101` not in 425-code set. 접미 `101`은 510~870 범위 밖 |
| `1168010100`은 강남구 **신사동 법정동** 코드 형태로 보인다 | 행정동이 아니라 법정동 체계로 추정 — **추정이며 확정하지 않음** |

**결론(결정): 사이렌은 `admin_dong_code`를 앞 8자리 절단으로 정규화하지 않는다.** 절단은 이 사례에서 존재하지 않는 코드를 만든다. 대신,

- `admin_dong_code`는 **선택·에코 전용**이며 어떤 신호 계산에도 쓰이지 않는다. 값이 달라도 위험 결과는 변하지 않는다.
- 형식 검증을 하지 않는다(길이·패턴 무검증). 8자리든 10자리든 422를 내지 않는다.
- 보낼 경우 `admin_dong_code_system`을 함께 보내면 어떤 체계인지 응답·Evidence 추적에 남는다.

**확인 필요(중간 백엔드):** `1168010100`이 (a) 법정동 코드인지 (b) 행정동 코드인지 회신 바란다. 향후 행정동 단위 신호를 도입할 때만 매핑이 필요하며, 매핑표 작성은 이번 범위 밖이다. 법정동 → 행정동은 1:N이므로 자동 변환은 불가능하다.

#### 2.1.3 업종 코드 체계 (PR #11 P1 답)

`CS1000xx`는 **서울 열린데이터광장 「서울시 상권분석서비스」의 서비스 업종 코드** 체계다. 사이렌이 새로 만든 코드가 아니며, 위험 계산의 원천 파일이 이 코드로 파티션되어 있다.

- `data/인허가/음식점_상권분기_패널.csv`의 `업종코드` (SR-01)
- `data/추정매출/*/*(추정매출-상권)*.csv`의 `서비스_업종_코드` (SR-02.market)
- `data/인허가/음식점_인허가_서울.csv`의 `업종코드` (SR-03)

즉 **사이렌이 코드 체계를 바꾸면 실측 데이터와의 조인이 끊긴다.** 그래서 사이렌은 `CS1000xx`를 유지하고, 변환은 호출자 쪽에서 한다(추천 API도 같은 체계를 쓰므로 두 서비스가 이미 정합).

| `industry_code` | 라벨 | 중간 백엔드 대응(회신 필요) |
| --- | --- | --- |
| `CS100001` | 한식음식점 | `I201` 한식음식점 — 라벨 일치, 확정은 회신 후 |
| `CS100002` | 중식음식점 | ? |
| `CS100003` | 일식음식점 | ? |
| `CS100004` | 양식음식점 | ? |
| `CS100005` | 제과점 | ? |
| `CS100006` | 패스트푸드점 | ? |
| `CS100007` | 치킨전문점 | ? |
| `CS100008` | 분식전문점 | ? |
| `CS100009` | 호프-간이주점 | ? |
| `CS100010` | 커피-음료 | `I212` 커피전문점 — 라벨 일치, 확정은 회신 후 |

- 매핑표 **소유는 중간 백엔드**다. 사이렌은 위 10개 코드와 라벨을 정본으로 제공한다.
- 위 10개(외식업)에 없는 업종은 **422로 거절**한다. 임의 대체 코드로 폴백하지 않는다(잘못된 상권 통계로 점수를 만들지 않기 위함).
- 지원 업종 확대는 신호 감사(RS-01) 재실행이 필요하며 이번 범위 밖이다.
- **확인 필요:** `I2xx`의 전체 코드 목록과 그 체계 이름(표준산업분류/자체 코드 여부). 라벨 문자열 일치만으로 매핑을 확정하지 않는다.

#### 2.1.4 상권 코드·좌표를 선택으로 바꾼 이유와 그 한계 (PR #11 P1 핵심)

**코드 감사 결과:** `trade_area_code`, `x_5181`, `y_5181`는 `service/siren/` 계산 경로에서 **한 번도 읽히지 않는다.**
`grep` 결과 사용처는 (1) `pipeline.py`가 `branch.trade_area_code`로 에코, (2) `service/siren/demo/`가 요청을 **조립할 때** 조인 키·반경 계산에 사용 — 두 곳뿐이다. 따라서 필드를 선택으로 바꿔도 어떤 신호도 나빠지지 않는다.

**결정: 세 필드를 선택(nullable)으로 완화한다.** 없으면 `branch.trade_area_code = null`로 에코하고 422를 내지 않는다.

**그러나 이것만으로는 호출이 풀리지 않는다.** 실제 제약은 스키마가 아니라 `market_data`의 출처다.

| 사실 | 영향 |
| --- | --- |
| `market_data`는 상권×업종 grain으로 집계된 값을 **호출자가 만들어 보내는** 입력이다 | 상권 코드가 없으면 그 집계를 조회할 키가 없다 |
| `competition`은 좌표 반경 내 신규 인허가 수를 **호출자가 미리 센 값**이다 | 좌표가 없으면 SR-03 입력을 만들 수 없다 |
| 한 신호라도 점수가 없으면 그 층 점수가 `null`이 된다 (`pipeline._weighted`) | SR-03만 빠져도 market 층 전체가 `missing` |
| 두 층이 모두 `calculated`일 때만 `risk.score`·`grade`가 나온다 | **등급이 아예 `null`** → 중간 백엔드 `report_analysis.risk_level`에 넣을 값이 없음 |

실측 확인 (demo-br-01):

| 입력 변형 | `risk.score` | `grade` | `calculation_status` |
| --- | --- | --- | --- |
| 원본 | 23.1 | 정상 | calculated |
| `competition`만 제거 | **null** | **null** | partial |
| `market_data` 전체 제거 | **null** | **null** | partial |

즉 리뷰가 제안한 "(c) 상권 신호를 빼고 선택 필드로" 를 그대로 적용하면 **SR-03만 빠지는 게 아니라 등급 자체가 사라진다.** 이 점을 계약에 명시하고, 대응은 §6 `options.grade_policy`로 분리한다.

**선택지 (사람 승인 필요 — §9-A):**

| 안 | 내용 | 얻는 것 | 잃는 것 |
| --- | --- | --- | --- |
| (a) 점포 등록 스키마 확장 | 중간 백엔드가 상권 코드 또는 주소→좌표를 수집 | 5신호 전부, 등급 정상 산출 | 등록 화면 요구사항 변경 |
| (b) 사이렌이 좌표로 상권 역산 | 사이렌이 서울시 영역 CSV를 적재해 최근접 상권 매칭 | 좌표만 있으면 동작 | **순수 함수 경계 붕괴**(파일 I/O·데이터 배포) → **v1 범위 밖으로 명시** |
| (c) gu×업종 coarse baseline | `market_data.grain="gu"`로 자치구 단위 집계 전달 (§2.2) | 상권 코드 없이 market 층 산출 | 점수 의미가 상권 단위와 달라짐(비교 불가) |
| (d) branch-only 잠정 등급 | 시장 층 없이 가맹점 층만으로 잠정 등급 (§6) | 운영보고서만으로 즉시 등급 | 시장 위험 미반영, "잠정" 표기 필수 |

**사이렌의 권장(제안, 최종 결정 아님):** 단기는 (d) + (a) 병행. 중간 백엔드는 이미 월별 보고서 35개 항목을 1:1로 보유하므로 (d)로 바로 등급을 얻고, (a)가 완료되면 시장 층을 켜서 5신호 등급으로 승격한다. (b)는 채택하지 않는다.

### 2.2 `market_data` — 상권×업종 실측 (SR-01, SR-02.market, SR-03)

```json
"market_data": {
  "source": "seoul_open_data",
  "closure_source": "seoul_open_data:음식점_상권분기_패널",
  "sales_source": "seoul_open_data:추정매출-상권",
  "grain": "trade_area",
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
| **`grain` (v1.1 신설)** | `trade_area`(기본) \| `gu`. `gu`면 Evidence `grain`이 `gu-industry-quarter`로 바뀌고 uncertainty 1건이 강제된다. **운영에서 `gu` 사용은 사람 승인 필요(§9-A)** |
| 완결 분기만 | `as_of` 를 포함하는 분기는 분기말이 `as_of` 이하일 때만 사용 (미완결 분기 제외) |
| `closure_quarters` | 시간순. 각 분기 `closures ÷ 직전 분기 active_count_end × 100` = 분기 폐업률. 첫 분기는 당분기 값 폴백 |
| rolling | `rolling_2q` = 최근 2분기 `closures` 합 ÷ 창 시작 직전 분기 `active_count_end` × 100. `rolling_4q` 동일. 별도 필드로 응답 |
| `quarter_status` | `부분`이 최신 분기이거나 rolling 창에 포함되면 `closure.status = "partial"` + `window_has_partial_quarter = true` + uncertainty 강제 |
| 분모 0 | 직전 분기 `active_count_end` = 0 → `not_calculable` |
| 정합성 | 산출 폐업률 > 100% → `rate_exceeds_100 = true` + `status = "partial"` + uncertainty (패널 입력 이상) |
| `sales_quarters` | `amount_krw`는 분기 합계(카드매출 추정치). 절대액 신뢰 금지, 추세만. 최근 분기 vs 직전 분기, 최근 vs 전년 동분기 변화율 |
| `competition` | **`radius_m` 필수** — 없으면 SR-03 `status = "missing"`, 점수 미산출, `missing_data` 기재 (반경 모르는 카운트로 점수를 만들지 않음). `active_only=false`면 uncertainty |
| `market_data` 누락 | `market_risk` 층 전체 `missing` → 종합 점수 `partial`, `grade=null` (§2.1.4) |

### 2.3 `branch_reports` — 누적 월별 운영보고서 (SR-02.branch, SR-05)

> PR #11에서 중간 백엔드 입력 항목 35개와 1:1 대응 확인됨. **v1.1에서 변경 없음.** 매핑 계층만으로 연결 가능.

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
| `branch_id` | (선택) 점포 연결 키. 요청 `branch_id` 와 **문자열이** 다르면 해당 레코드 제외 + uncertainty (RS-T07). 정수→문자열 변환 규칙을 요청 `branch_id`와 동일하게 맞출 것 (§2.1.1) |
| `written_at` | ISO date, `as_of` 이후 제외 (제외 시 uncertainty) |

### 2.5 `options`

| 필드 | 값 | 기본 |
| --- | --- | --- |
| `llm_mode` | `disabled` \| `explanation_only` \| `explanation_and_review_assist` | `explanation_only` |
| `send_notifications` | bool | `false` — `true`는 승인된 dispatch adapter 없이 422 |
| **`grade_policy` (v1.1 신설)** | `strict` \| `renormalized_partial` \| `branch_only_provisional` | `strict` (§6) |

v1 에서 `explanation_and_review_assist` 는 **미구현** — LLM 런타임 미연결이라 `explanation_only` 와 동일 동작(결정론 템플릿). 계약에는 남겨두되 실제 LLM 분류 보조는 후속.

## 3. 응답 스키마

```json
{
  "contract_version": "risk-siren-contract-v1.1",
  "request_id": "...",
  "branch": {"franchise_id": "...", "branch_id": "...", "brand_name": "...",
             "as_of": "2026-03-31", "industry_code": "CS100009", "industry_label": "호프-간이주점",
             "trade_area_code": "3120189"},
  "risk": {
    "score": 63.2, "grade": "주의", "risk_level": "CAUTION",
    "score_version": "risk-siren-v1.2",
    "calculation_status": "calculated",
    "policy_status": "provisional",
    "composite_basis": ["SR-01", "SR-02.market", "SR-03", "SR-02.branch", "SR-05"],
    "excludes": ["SR-04"],
    "branch_floor_applied": false
  },
  "layers": {
    "market_risk": {"score": 58.0, "status": "calculated"},
    "branch_risk": {"score": 71.5, "status": "calculated"}
  },
  "components": { "...": "§3.2" },
  "review_signal": { "...": "§3.2" },
  "evidence": [
    {"evidence_id": "ev-closure-rolling-2q", "signal_id": "SR-01", "layer": "market",
     "value": 8.33, "unit": "percent", "period": "rolling-2q~2026Q1",
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
      {"signal_id": "SR-01",        "layer": "market",    "source": "seoul_open_data:음식점_상권분기_패널", "synthetic": false},
      {"signal_id": "SR-02.market", "layer": "market",    "source": "seoul_open_data:추정매출-상권",        "synthetic": false},
      {"signal_id": "SR-03",        "layer": "market",    "source": "seoul_open_data:음식점_인허가_서울",   "synthetic": false},
      {"signal_id": "SR-02.branch", "layer": "branch",    "source": "synthetic_pos",                       "synthetic": true},
      {"signal_id": "SR-05",        "layer": "branch",    "source": "synthetic_self_reported",             "synthetic": true},
      {"signal_id": "SR-04",        "layer": "auxiliary", "source": "synthetic_reviews",                   "synthetic": true}
    ]
  },
  "alert": {
    "event_type": "branch_risk_evaluated",
    "event_id": "evt-...", "idempotency_key": "...",
    "branch_id": "...", "grade": "주의", "risk_level": "CAUTION",
    "previous_grade": null, "score": 63.2, "as_of": "2026-03-31",
    "recipients": [
      {"role": "branch_owner", "channels": ["in_app", "email"]},
      {"role": "franchise_hq", "channels": ["in_app", "email"]}
    ],
    "report_link": null,
    "should_fire": false,
    "suppressed_reason": null,
    "dispatch_status": "disabled",
    "dispatch_owner": "middle_backend",
    "evidence_ids": ["..."]
  },
  "financial_products": {
    "owner": "middle_backend",
    "status": "grade_only",
    "recommended_grade": "주의",
    "recommended_risk_level": "CAUTION",
    "items": []
  },
  "explanation": {"text": "...", "evidence_ids": ["..."], "model": "deterministic-template-v1"},
  "projections": {
    "branch_owner": { "...": "§3.6" },
    "franchise_hq": { "...": "§3.6" }
  }
}
```

### 3.1 점수·등급 규칙

| 항목 | 규칙 |
| --- | --- |
| `market_risk.score` | `w1·closure + w2·sales_market + w3·competition` (가중치 provisional) |
| `branch_risk.score` | `w4·sales_branch + w5·profitability` (가중치 provisional) |
| `risk.score` | `max(branch_risk, wm·market_risk + wb·branch_risk)` — 양호한 시장이 가맹점 위험을 상쇄하지 못함. 두 층 모두 `calculated`일 때만 (`grade_policy=strict`) |
| 한 층이라도 미계산 | `risk.score = null`, `grade = null`, `risk_level = null`, `calculation_status = "partial"`, `uncertainty`에 사유 |
| `grade` | `< 40` 정상 / `< 70` 주의 / `>= 70` 위험 (임계값 provisional, 사람 승인 필요) |
| **`risk_level` (v1.1 신설)** | `grade`의 영문 표현. `정상→NORMAL`, `주의→CAUTION`, `위험→DANGER`, `null→null`. 파생값이므로 `grade`와 **절대 불일치하지 않는다** |
| `review_signal` | 종합 점수 **미포함** (가중치 0). `sub_score`·`watchlist_flag`만 |
| 예측 필드 | `success_probability`, `closure_probability`, `profit_forecast`, `vacancy_rate` 등 **계약 금지**. 요청은 `extra="forbid"`로 거부, 응답에 생성 금지 |

**`risk_level = null`의 처리 (중간 백엔드 확인 필요, §9-B):** 중간 백엔드 `report_analysis.risk_level`은 `NORMAL/CAUTION/DANGER` 3값만 갖는다. 사이렌은 데이터가 부족할 때 **안전(NORMAL)으로 강등하지 않고 `null`을 반환한다**(CLAUDE.md 처리 원칙). 따라서 컬럼을 nullable로 두거나 `UNDETERMINED`를 추가해야 한다. 사이렌이 `null` 대신 `NORMAL`을 보내는 선택지는 **채택하지 않는다** — 미계산을 안전으로 표시하는 것은 이 서비스의 금지 사항이다.

### 3.2 타입화되지 않은 블록의 최소 키 스키마 (PR #11 P2 대응)

`risk`·`layers`·`alert`·`evidence`·`missing_data`·`data_provenance`·`explanation`·`financial_products`·`branch`는 v1.1에서 **Pydantic 모델로 타입화한다**(§3.5). 아래 두 블록은 신호별 필드 수가 많고 status에 따라 가변이라 v1.1에서는 `dict`로 두되, **최상위 키를 계약으로 고정**한다. 여기 없는 키는 추가될 수 있으나 아래 키는 제거되지 않는다.

`components` — 최상위 4키 고정:

| 키 | 하위 | 항상 존재하는 필드 |
| --- | --- | --- |
| `closure` | — | `score`(float\|null), `status`, `source`, `quarter_rate`, `rolling_2q_rate`, `rolling_4q_rate`, `latest_quarter`, `window_has_partial_quarter`, `rate_exceeds_100`, `active_count_end` |
| `sales_decline` | `.market` | `score`, `status`, `source`, `latest_quarter`, `recent_quarter_change_pct`, `yoy_change_pct` |
| `sales_decline` | `.branch` | `score`, `status`, `recent_3m_change_pct`, `previous_3m_change_pct`, `recent_3m_total_krw`, `previous_3m_total_krw`, `recent_period_missing_months` |
| `competition` | — | `score`, `status`, `source`, `radius_m`, `same_new_recent_3m`, `same_new_delta`, `similar_new_recent_3m`, `weighted_new_count`, `active_only` |
| `profitability` | — | `score`, `status`, `operating_margin_recent_3m_pct`, `operating_margin_previous_3m_pct`, `operating_profit_recent_3m_krw`, `consecutive_negative_months`, `labor_ratio_recent_3m`, `coupon_ratio_recent_3m`, `loan_interest_trend`, `recent_period_missing_months` |

- 모든 component의 `status`: `calculated` \| `partial` \| `missing` \| `not_calculable`
- `status != "calculated"`인 component의 `score`는 `null`이며, **0으로 읽으면 안 된다.**

`review_signal` — `status` 고정, 나머지는 `status="calculated"`일 때만 보장:
`status`, `sub_score`, `direction`(`worsening`\|`stable`\|`improving`), `review_count`, `negative_count`, `positive_count`, `negative_ratio`, `positive_ratio`, `negative_ratio_previous_period`, `negative_ratio_delta`, `watchlist_flag`, `source`, `model`.

### 3.3 알림 (PR #11 P2 답 — "발송 지시로 보면 되는가": 그렇다)

`alert`는 **알림 그 자체가 아니라 "이 내용으로 알림을 만들라"는 발송 지시 payload**다. 사이렌은 이메일·인앱을 보내지 않고, 큐에 넣지 않으며, 발송 이력·읽음 상태를 갖지 않는다.

| 항목 | 규칙 |
| --- | --- |
| 멱등 키 | `sha256("branch_id|as_of|score_version")[:24]` (구분자 `|`, 공백 없음). 같은 점포·같은 기준일·같은 점수 버전이면 **재호출해도 동일** → 중간 백엔드는 이 키로 중복 발송을 막는다 |
| `event_id` | `evt-` + 멱등 키. 별도 의미 없음 |
| `event_type` | `branch_risk_evaluated` — 매 평가의 **현재 상태 스냅샷**. 등급 전이(이전→현재) 감지는 중간 백엔드가 `previous_grade`를 보관·비교. 이 서비스의 `previous_grade`는 항상 `null` |
| `should_fire` | `grade == "위험"` AND `score is not None` AND `grade_policy == "strict"`. **이 값이 `true`일 때만 발송 대상**이다. `false`면 저장만 하고 보내지 않는다 |
| `suppressed_reason` (v1.1 신설) | `should_fire=false`이면서 등급이 위험인 경우의 사유. 예: `"grade_policy=branch_only_provisional"`, `"calculation_status=partial"`. 아니면 `null` |
| `dispatch_status` | 항상 `"disabled"` — 승인·adapter 이전 상태. **사이렌이 발송을 시도하지 않았다는 뜻**이지, 중간 백엔드가 보내면 안 된다는 뜻이 아니다 |
| `dispatch_owner` (v1.1 신설) | 항상 `"middle_backend"` — 실제 발송 주체 명시 |
| 수신자 | `branch_owner`, `franchise_hq` 동일 이벤트. 권한별 내용 차이는 `projections`에서 |
| `report_link` | 항상 `null`. 링크 생성은 중간 백엔드(URL 체계를 사이렌이 모름) |
| `send_notifications=true` | 422 — 사이렌에게 발송을 시키려는 호출은 거절 |
| 문구 | 사이렌은 알림 **본문 문구를 만들지 않는다.** 문구는 승인 대상(§9-E)이며 중간 백엔드가 `grade`/`risk_level`/`evidence_ids`로 구성 |

### 3.4 금융상품 (PR #11 P2 답 — 사이렌은 상품을 고르지 않는다)

v1에서 사이렌의 `financial_products`는 항상 `{"status": "catalog_match_pending", "items": []}`였다. 즉 **실제 상품 매칭은 한 번도 구현된 적이 없다.** 중간 백엔드가 `financial_product` 테이블과 `GET /api/v1/financial-products`, 등급별 최대 3개 규칙(REQ-OW-07/09/10)을 이미 구현했으므로, 소유권을 중간 백엔드로 확정하는 것이 옳다.

**v1.1 결정(계약 축소):** 사이렌은 **등급 신호만** 내려준다.

| 필드 | 값 |
| --- | --- |
| `owner` | 항상 `"middle_backend"` |
| `status` | `"grade_only"` (v1의 `"catalog_match_pending"`은 v1.2에서 제거) |
| `recommended_grade` | `risk.grade`와 동일 값 (`정상`\|`주의`\|`위험`\|`null`) |
| `recommended_risk_level` | `risk.risk_level`과 동일 값 |
| `items` | **항상 `[]`**. deprecated — v1.2에서 필드 제거 예정. 화면은 이 값을 읽지 않는다 |

- 사이렌은 상품 카테고리를 **만들어내지 않는다.** 승인된 카탈로그가 없는 상태에서 카테고리 문자열을 생성하면 근거 없는 추천이 된다.
- `projections.branch_owner.financial_products`도 같은 블록을 그대로 투영한다(별도 계산 없음).
- 최종 소유권 확정은 §9-C.

### 3.5 타입화된 응답 모델 스펙 (구현자용 — RS-03에서 반영)

`service/siren/models.py`의 `RiskSirenResponse`를 아래로 대체한다. `ContractModel`은 `extra="forbid"`.

```python
GradeKo   = Literal["정상", "주의", "위험"]
RiskLevel = Literal["NORMAL", "CAUTION", "DANGER"]
GRADE_TO_RISK_LEVEL: dict[str, str] = {"정상": "NORMAL", "주의": "CAUTION", "위험": "DANGER"}
ComponentStatus = Literal["calculated", "partial", "missing", "not_calculable"]

class BranchIdentity(ContractModel):
    franchise_id: str
    branch_id: str
    brand_name: str | None = None
    as_of: str                      # ISO date
    industry_code: str
    industry_label: str
    trade_area_code: str | None = None      # v1.1: nullable

class RiskResult(ContractModel):
    score: float | None
    grade: GradeKo | None
    risk_level: RiskLevel | None            # v1.1 신설 (grade 파생)
    score_version: str
    calculation_status: Literal["calculated", "partial"]
    policy_status: Literal["provisional", "approved"]
    grade_policy: Literal["strict", "renormalized_partial", "branch_only_provisional"] = "strict"
    composite_basis: list[str]
    excludes: list[str]
    branch_floor_applied: bool = False

class LayerResult(ContractModel):
    score: float | None
    status: Literal["calculated", "partial", "missing"]

class Layers(ContractModel):
    market_risk: LayerResult
    branch_risk: LayerResult

class EvidenceItem(ContractModel):
    evidence_id: str
    signal_id: str
    layer: Literal["market", "branch", "auxiliary"]
    value: float | int | str | None
    unit: str
    period: str
    grain: str
    source: str | None
    synthetic: bool
    supports: str

class MissingDataItem(ContractModel):
    signal_id: str
    reason: str

class ProvenanceSignal(ContractModel):
    signal_id: str
    layer: Literal["market", "branch", "auxiliary"]
    source: str | None
    synthetic: bool

class DataProvenance(ContractModel):
    contains_synthetic: bool
    disclosure: str
    by_signal: list[ProvenanceSignal]

class AlertRecipient(ContractModel):
    role: Literal["branch_owner", "franchise_hq"]
    channels: list[Literal["in_app", "email"]]

class AlertPayload(ContractModel):
    event_type: Literal["branch_risk_evaluated"]
    event_id: str
    idempotency_key: str
    branch_id: str
    grade: GradeKo | None
    risk_level: RiskLevel | None
    previous_grade: None = None             # 항상 null (전이 판정은 중간 백엔드)
    score: float | None
    as_of: str
    recipients: list[AlertRecipient]
    report_link: None = None                # 항상 null (URL 체계 미보유)
    should_fire: bool
    suppressed_reason: str | None = None
    dispatch_status: Literal["disabled"]
    dispatch_owner: Literal["middle_backend"] = "middle_backend"
    evidence_ids: list[str]

class FinancialProductsBlock(ContractModel):
    owner: Literal["middle_backend"] = "middle_backend"
    status: Literal["grade_only"]
    recommended_grade: GradeKo | None
    recommended_risk_level: RiskLevel | None
    items: list[dict] = Field(default_factory=list)   # 항상 []. v1.2 제거 예정

class ExplanationBlock(ContractModel):
    text: str | None
    evidence_ids: list[str]
    model: str | None

class RiskSirenResponse(ContractModel):
    contract_version: Literal["risk-siren-contract-v1.1"] = "risk-siren-contract-v1.1"
    request_id: str
    branch: BranchIdentity
    risk: RiskResult
    layers: Layers
    components: dict            # 최상위 키 계약 = §3.2
    review_signal: dict         # 최상위 키 계약 = §3.2
    evidence: list[EvidenceItem]
    missing_data: list[MissingDataItem]
    uncertainty: list[str]
    excluded_future_months: list[str]
    data_provenance: DataProvenance
    alert: AlertPayload
    financial_products: FinancialProductsBlock
    explanation: ExplanationBlock
    projections: dict           # 최상위 키 계약 = §3.6
```

구현 주의:

- `risk_level`은 **`grade`에서만 파생**한다. 별도 분기 로직으로 계산하면 두 값이 갈라질 수 있다 (`GRADE_TO_RISK_LEVEL.get(grade)` 한 줄).
- `alert.risk_level`도 같은 매핑 한 곳에서 나온다.
- `financial_products.recommended_*`는 `risk`에서 복사한다. 재계산 금지 (ADR-001: 같은 사실은 한 번만 계산).
- 타입화 후 회귀 테스트 필요: 기존 25개 unittest + 데모 20건 재생성 바이트 비교(추가 필드로 인한 diff는 예상됨 → 스냅샷 갱신 1회).

### 3.6 권한별 투영 (`projections`)

| 규칙 | |
| --- | --- |
| `branch_owner` | 본인 `branch_id` 결과만. 키: `branch_id, score, grade, risk_level, calculation_status, layers, components, review_signal, evidence, missing_data, uncertainty, recommended_actions, financial_products, report_link, data_provenance` |
| `franchise_hq` | 키: `branch_id, franchise_id, score, grade, risk_level, calculation_status, component_status, review_watchlist_flag, data_provenance`. **다른 가맹점 링크·타 프랜차이즈 결과 금지** (RS-T11: 위반 시 Critical) |
| 교차 검증 | 두 투영의 `score`·`grade`·`risk_level`·`as_of`는 동일 원본에서 나와야 함 (다른 계산 금지, ADR-001) |
| **호출자 의무 (v1.1 명시)** | 본사 사용자 화면에 `analyze` **원본 응답을 그대로 전달하면 안 된다.** 원본에는 점포 상세(components·evidence)가 들어 있다. 본사에는 `projections.franchise_hq`만 투영한다 |

중간 백엔드의 권한별 경계:

| audience | 인증 주체 | 반환 projection | 금지 |
| --- | --- | --- | --- |
| `branch_owner` | 본인 점포 owner | `projections.branch_owner` — components·evidence·missing_data·uncertainty·alert·조치 | 다른 점포·본사 집계 |
| `franchise_hq` | 자사 HQ | `projections.franchise_hq` — component_status·watchlist·provenance + `/api/v1/branches/risk-summary` 집계 | owner 상세 원본·타 프랜차이즈 |

## 4. `hq-summary` 엔드포인트

요청: `{request_id, franchise_id, as_of, branch_results: [analyze 응답 N건]}`

```json
{
  "franchise_id": "...", "as_of": "...",
  "branch_count": 20,
  "calculated_count": 17,
  "grade_distribution": {"정상": 11, "주의": 4, "위험": 2},
  "risk_level_distribution": {"NORMAL": 11, "CAUTION": 4, "DANGER": 2},
  "danger_ratio_pct": 11.7647,
  "average_score": 47.3,
  "watchlist": [{"branch_id": "...", "grade": "위험", "risk_level": "DANGER", "reasons": ["SR-05", "SR-04"]}],
  "alert_candidate_count": 2,
  "unread_alert_count": null,
  "data_provenance": {"contains_synthetic": true, "disclosure": "..."}
}
```

| 필드 | 규칙 |
| --- | --- |
| `danger_ratio_pct` | 위험 등급 수 ÷ **계산된** 가맹점 수 × 100. `average_score`와 혼용 표시 금지 (pipeline-design 6절) |
| `average_score` | 계산된 점수의 평균. 화면은 둘 중 하나만 고른다 |
| `risk_level_distribution` (v1.1 신설) | `grade_distribution`의 영문 표현. 파생값 |
| `alert_candidate_count` (v1.1 신설) | **이번 요청에 담긴** `branch_results` 중 `alert.should_fire == true` 인 건수. "발송 후보 수"이지 "미확인 수"가 아니다 |
| `unread_alert_count` | **deprecated (v1.1) — 항상 `null`.** v1.2에서 필드 제거 |

**`unread_alert_count`를 없애는 이유 (PR #11 P2 답):** 리뷰 지적이 맞다. v1 구현은 `branch_results` 안의 `should_fire` 개수를 셌을 뿐이라 "미확인(unread)"이 아니었다. 읽음 여부는 중간 백엔드 DB에만 있으므로 중간 백엔드가 세는 것이 맞다. 잘못된 숫자를 계속 내려보내는 대신 **`null`로 고정해 아무도 신뢰하지 못하게** 하고, 의미가 정확한 `alert_candidate_count`를 별도로 제공한다.

`hq-summary`는 **재계산하지 않는다.** 전달받은 결과를 세기만 하며, 점포 점수를 다시 만들지 않는다.

## 5. 오류 상태

| 조건 | 응답 |
| --- | --- |
| `request_id`/`franchise_id`/`branch_id`/`as_of` 누락 | 422 ValidationError |
| `industry_code` 누락 또는 enum 밖 (예: `I201` 그대로 전송) | 422 — 폴백 금지 (§2.1.3) |
| `location.gu_code` 누락 | 422 |
| **`location.trade_area_code`/`x_5181`/`y_5181` 누락** | **200 (v1.1 변경, v1에서는 422)** — `branch.trade_area_code=null` 에코 |
| `location.admin_dong_code` 길이·형식 불일치 | 200 — 검증하지 않음 (§2.1.2) |
| 음수 금액/건수, 잘못된 `month`/`quarter` 형식 | 422 |
| `options.grade_policy` enum 밖 | 422 |
| `send_notifications=true` | 422 |
| 예측 필드(`success_probability` 등) 포함 | 422 (`extra="forbid"`) |
| `market_data` 없음 | 200, `market_risk.status="missing"`, `calculation_status="partial"`, `grade=null` |
| `competition.radius_m` 없음 | 200, SR-03 `status="missing"` → market 층 `missing` → `grade=null` (§2.1.4) |
| `branch_reports` 6개월 구간 불완전 | 200, `branch_risk.status="partial"`, missing 월 명시 |
| 분모 0 (폐업률·매출 변화) | 200, 해당 지표 `not_calculable` |
| 최근 3개월 순매출 합 ≤ 0 | 200, SR-05 최고위험 (누락 아님, §2.3 참조) |
| `as_of` 포함 분기 미완결 | 200, market 신호는 직전 완결 분기 기준 |

`risk` 응답 필드는 정확히: `score`, `grade`, `risk_level`, `score_version`, `calculation_status`, `policy_status`, `grade_policy`, `composite_basis`, `excludes`, `branch_floor_applied`. `provisional_composite`·`weighted_composite` 같은 그림자 점수 필드는 노출하지 않는다.

## 6. degraded 모드 — `options.grade_policy` (v1.1 신설)

입력이 부족할 때 등급을 어떻게 다룰지 **호출자가 명시적으로 선택**하게 한다. 기본값은 현재 동작과 동일하다.

| 값 | 동작 | `calculation_status` | `alert.should_fire` | 승인 |
| --- | --- | --- | --- | --- |
| `strict` (기본) | 두 층 모두 `calculated`일 때만 점수·등급. 아니면 전부 `null` | `calculated` 또는 `partial` | 등급 위험이면 `true` | 현행 (v1 동작) |
| `renormalized_partial` | 층 안에서 누락 component를 빼고 **남은 가중치를 재정규화**해 층 점수 산출. 두 층이 모두 최소 1개 component를 가질 때만 종합 산출 | 항상 `partial` | **항상 `false`** + `suppressed_reason` | **사람 승인 필요 (§9-A)** |
| `branch_only_provisional` | 시장 층이 통째로 없을 때 `risk.score = branch_risk.score`. 시장 신호는 `excludes`로 이동 | 항상 `partial` | **항상 `false`** + `suppressed_reason` | **사람 승인 필요 (§9-A)** |

공통 규칙 (비-strict 모드):

- `risk.grade_policy`에 사용한 값을 그대로 반환한다. 화면은 이 값이 `strict`가 아니면 **"잠정" 표기를 강제**한다.
- `uncertainty`에 어떤 신호가 빠졌는지, 그래서 점수 의미가 어떻게 좁아졌는지 문장을 추가한다.
- `composite_basis`는 **실제로 점수에 기여한 신호만** 담고, 빠진 신호는 `excludes`로 옮긴다 (v1의 정적 리스트 → v1.1에서 동적).
- **실제 사이렌(알림)은 울리지 않는다.** 부분 데이터로 점주·본사에게 위험 경고를 보내지 않는다는 것이 이 서비스의 기본 안전장치다.
- 누락을 0점으로 대체하지 않는다. 재정규화는 "없는 신호를 안전하다고 보는 것"이 아니라 "남은 신호만으로 좁게 본다"는 뜻이며, 그 사실이 응답에 남는다.

## 7. 인증·권한 전제

- `/internal/*` 경로는 **내부 전용**이다. 사이렌은 토큰을 검증하지 않고 사용자 개념도 없다. 외부에 노출하지 않는 것은 배포 측 책임이다.
- 사이렌은 요청에 담긴 `franchise_id`·`branch_id`를 **신뢰한다.** "이 사용자가 이 점포를 볼 수 있는가"는 중간 백엔드가 판정한 뒤 호출한다.
- 사이렌은 요청 1건당 점포 1건만 계산하므로, 잘못된 권한으로 호출되면 잘못된 점포 결과가 나간다. 교차 노출 방지는 §3.6 투영 규칙 + 호출 측 권한 검사 두 겹이다.
- 개인정보: 요청에 점주 이름·연락처·주소 문자열을 담지 않는다. 계약에 그런 필드가 없고 `extra="forbid"`로 거절된다. 리뷰 `text`는 원문이 들어오므로 로그 적재 시 주의(사이렌은 로그를 남기지 않음).

## 8. 호출 방식과 성능 (PR #11 질문 답)

### 8.1 동기 호출로 충분한가 — 실측

`service/siren`은 **파일·네트워크·DB·LLM 호출이 없는 순수 계산**이다. `llm_mode=explanation_only`의 설명문도 결정론 템플릿이라 외부 호출이 없다.

데모 요청 20건 × 5회 반복 실측 (Python 3.14, 로컬, FastAPI 오버헤드 제외):

| 항목 | 값 |
| --- | --- |
| 요청 payload 크기 | 29~67 KB (월별 보고서 24개월 + 분기 21개 × 2 + 리뷰 ~22건) |
| Pydantic 검증 | 중앙값 0.19 ms |
| `analyze()` 계산 | 중앙값 0.18 ms |
| 검증+계산 합계 | 중앙값 0.37 ms / p95 0.49 ms / 최대 1.35 ms |
| 응답 크기 | 약 12 KB |
| 20개 점포 순차 처리 | 약 10 ms |

**결정(현 범위): 동기 유지.** 사람이 체감할 수 있는 지연이 아니며, 202 접수 + 폴링을 도입하면 상태 저장소가 필요해져 "상태 없는 순수 계산" 경계가 깨진다.

**재검토 조건 (이 중 하나라도 발생하면 202+폴링으로 전환 검토):**

1. `llm_mode`에 실제 LLM 런타임을 연결할 때 (외부 API 지연 수 초 단위)
2. 사이렌이 `market_data`를 직접 조회하게 될 때 (§2.1.4 (b)안 — 현재 범위 밖)
3. 한 요청에서 수백 개 점포를 배치 처리할 때

**사람 승인 필요(§9-D):** `INTERFACE_SPEC` 4장이 202+폴링으로 적혀 있으므로, 문서와 구현 중 어느 쪽을 맞출지는 두 팀의 합의 사항이다. 사이렌은 동기 구현을 이미 갖고 있고 전환 비용은 중간 백엔드 쪽 어댑터가 흡수하는 편이 작다는 의견을 제시한다(사이렌 앞에 중간 백엔드가 202 접수 → 내부 동기 호출 → 결과 저장 → 폴링 응답).

### 8.2 지역 식별자 체계 — 코드 vs 한글명

| 서비스 | 입력 | 이유 |
| --- | --- | --- |
| 추천 API | `sigungu: "송파구"` | 기존 계약 (다른 저장소 소유) |
| 위험 사이렌 | `gu_code: "11680"` | 위험 계산의 원천(서울시 상권·인허가·추정매출)이 코드로 파티션됨 |

**제안(사이렌 측 의견, 최종 결정 아님):** 사이렌은 코드를 유지한다. 한글명은 표기 변형(띄어쓰기·이체자·행정구역 개편)에 취약해 조인 키로 부적합하다. `gu_code ↔ 자치구명` 25행 매핑은 서울시 원천 파일(`영역-상권` CSV의 `자치구_코드`/`자치구_코드_명`)에 있으므로, 필요하면 사이렌이 **참조표를 산출물로 제공**할 수 있다(계약 변경 아님). 두 API 중 어느 쪽을 어느 방향으로 맞출지는 §9-D.

## 9. 미결정 · 사람 승인 필요

**v1.1 신규 (PR #11 발)**

- **A. 시장 데이터 확보 경로 + degraded 정책** — §2.1.4 (a)/(c)/(d) 중 선택, `grade_policy` 비-strict 값의 운영 사용 여부, `market_data.grain="gu"` 허용 여부. 사이렌 권장: (d) 단기 + (a) 중기, (b)는 v1 범위 밖.
- **B. `risk_level = null` 저장 정책** — 중간 백엔드 `report_analysis.risk_level` 컬럼을 nullable로 둘지, `UNDETERMINED`를 추가할지. 사이렌이 `NORMAL`로 대체하는 안은 채택하지 않음.
- **C. 금융상품 소유권 확정** — 사이렌 `financial_products`를 등급 신호만으로 축소(§3.4)하고 상품 매칭은 중간 백엔드로 확정.
- **D. 호출 방식·지역 코드 체계 정렬** — 동기 유지 vs 202+폴링(`INTERFACE_SPEC` 4장), 코드 vs 한글명.
- **E. 알림 문구·발송 트리거** — `should_fire=true`일 때 중간 백엔드가 실제로 보낼지, 문구는 무엇인지.
- **F. 업종 매핑표 확정** — `I2xx` 전체 목록과 체계 회신 후 확정 (§2.1.3).
- **G. 행정동 코드 체계 회신** — `1168010100`이 법정동인지 행정동인지 (§2.1.2).

**v1에서 이월 (변경 없음)**

1. `market_risk`/`branch_risk` 내부 가중치(w1~w5), 층간 가중치(wm/wb) — 전부 provisional
2. 40·70 등급 임계값, 경계 flapping 히스테리시스
3. SR-05 악화 트리거: 영업이익 연속 음수 개월 수, 마진 하락폭, labor_ratio·coupon_ratio 임계
4. SR-03 반경 N (250/500 m), 유사업종 매핑 규칙, "신규" = 인허가일 기준
5. SR-04 보조점수의 향후 종합 편입 여부
6. 경고 문구(정상·주의·위험), 합성 리뷰 텍스트 템플릿
7. 금융상품 카탈로그·노출 규칙
8. 이메일 공급자·실제 발송
9. 데모 창 종료월 확정 (2026-03) 및 20개 상권×업종 선정 목록

## 10. 변경 이력

| 버전 | 일자 | 변경 | 발단 |
| --- | --- | --- | --- |
| v0 | 2026-09-04 | 4신호 단일층 초안 (`document/risk-siren/02-input-output-contract.md`) | 초기 설계 |
| v1 | 2026-09-04 | 5신호·2층, 월별 운영보고서 35항목, `data_provenance`, `projections`, `hq-summary` | RS-01 신호 감사 |
| **v1.1** | **2026-09-05** | 아래 표 | **PR #11 중간 백엔드 리뷰** |

v1.1 상세 (모두 **응답 필드 추가 또는 요청 제약 완화**이므로 기존 호출자를 깨지 않는다. 예외는 `unread_alert_count` 의미 변경 1건):

| # | 변경 | 종류 | 리뷰 항목 |
| --- | --- | --- | --- |
| 1 | `location.trade_area_code`/`x_5181`/`y_5181` 필수 → 선택 | 제약 완화 | P1 |
| 2 | `location.admin_dong_code` 형식 무검증 명시 + `admin_dong_code_system` 선택 필드 | 추가 | P1 |
| 3 | 8자리 절단 정규화 **미채택** 근거 기록 (존재하지 않는 코드 생성) | 문서 | P1 |
| 4 | `industry_code` 체계 출처(서울 상권분석서비스)와 매핑 책임 명시 | 문서 | P1 |
| 5 | 응답 `risk`·`layers`·`alert`·`evidence`·`missing_data`·`data_provenance`·`explanation`·`financial_products`·`branch` Pydantic 타입화 | 계약 강화 | P2 |
| 6 | `risk.risk_level` / `alert.risk_level` (NORMAL/CAUTION/DANGER) 추가 | 추가 | P2 |
| 7 | `contract_version` 응답 필드 추가 | 추가 | P2 |
| 8 | `components`·`review_signal`·`projections` 최상위 키 스키마 문서화 | 문서 | P2 |
| 9 | `financial_products` 등급 신호만으로 축소, `owner`/`status="grade_only"` | 축소 | P2 |
| 10 | `alert` = 발송 지시 payload 명시, `dispatch_owner`·`suppressed_reason` 추가 | 추가·문서 | P2 |
| 11 | `hq-summary.unread_alert_count` deprecated(항상 `null`), `alert_candidate_count` 신설 | **의미 변경** | P2 |
| 12 | 식별자 pass-through 규칙 + 문자열 정규화 요구 명문화 | 문서 | P3 |
| 13 | `options.grade_policy`(strict/renormalized_partial/branch_only_provisional) 신설 | 추가 | P1 파생 |
| 14 | 동기 호출 실측치·재검토 조건, 지역 코드 체계 입장 기재 | 문서 | 질문 |
| 15 | 인증·권한 전제(§7) 신설, 본사 화면에 원본 응답 전달 금지 명시 | 문서 | — |

**구현 상태:** 이 문서는 계약이며, `service/siren/models.py` 반영은 RS-03(builder) 작업이다. v1.1 계약과 현재 코드의 차이는 위 표의 1·2·5·6·7·9·10·11·13 항목이다.
