# 위험 사이렌 v1 구현 핸드오프

- 갱신: 2026-09-04
- score_version: `risk-siren-v1-provisional`
- 상태: 구현·2층 데모 완료 / 독립 QA 2회 / **dev 통합 PR 전 최종 QA 1회 권고**
- 실제 발송: `disabled`, 금융상품: `catalog_match_pending`

## 1. 무엇이 됐나

| 영역 | 상태 |
| --- | --- |
| 신호 감사 (RS-01) | 완료 — `10-analysis/signal-audit.md`. 서울시 공개데이터로 SR-01/02.market/03 실측 검증, 폐업률 분모 원본 대조 |
| API 계약 v1 (RS-02) | 완료 — `20-method/input-output-contract.md`. 5신호 2층, `data_provenance`, `projections`, `hq-summary` |
| 구현 (RS-03) | 완료 — `service/siren/` (models·reports·risk_signals·pipeline·alerts·explanation·hq_summary·api) + `service/siren/demo/` |
| 테스트 | 25 unittest 통과 (정상·애매·실패·부정·반복 + QA 결함 회귀), harness validator 통과 |
| 데모 데이터셋 | 20 가맹점 × 24개월 = 480 운영보고서, 실 추정매출 추세 앵커. `artifacts/risk-siren/demo/` |
| 독립 QA (RS-04) | 2회 수행. 라운드 1: High 2 → 수정. 라운드 2: 1차 결함 전부 해결 확인 + 신규 High 1(D13) → 수정 (라운드 2 수정은 QA 재검증 전) |

## 2. 코어 설계 요약

```
market_risk = 0.45·SR01(폐업률) + 0.35·SR02.market(분기매출) + 0.20·SR03(경쟁)   ← 서울시 실측
branch_risk = 0.35·SR02.branch(순매출감소) + 0.65·SR05(수익성악화)                ← 운영보고서
              └ SR05 >= 80 이면 branch_risk = max(., SR05 - 12)
              └ 최근 3개월 순매출 합 ≤ 0 이면 SR05 = 95 (영업정지 준함)
risk.score  = max(branch_risk, 0.35·market + 0.65·branch)   ← 양호한 시장이 가맹점 위험을 상쇄 못 함
grade       = <40 정상 / <70 주의 / >=70 위험  (두 층 모두 calculated 일 때만)
review_signal = SR04, 종합 점수 밖 (가중치 0). watchlist 플래그·안내 문구만
```

가중치·임계값·SR-05 트리거·순매출0 처리 수치(95)는 전부 `provisional` — 사람 승인 전 운영값 아님.

## 3. 데모 실행

```bash
export SIREN_SEOUL_DATA_DIR=/Users/parkjunwoo/Documents/data-analysis/data
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python -m service.siren.demo.baseline
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python -m service.siren.demo.build --reuse
```

결과 (as_of 2026-03-31): 위험 8 / 주의 4 / 정상 6 / partial 2. `demo/timeline/*.json` 에 월별 운영보고서 누적에 따른 사이렌 발동 시점. `demo/SUMMARY.md`, `demo/hq_summary.json`.

## 4. 실 데이터 연동 시

- `branch_reports[].sales_source` / `cost_source` 를 `synthetic_pos`/`synthetic_self_reported` → `pos`/`self_reported`(또는 `franchise_hq`) 로 교체
- `reviews.source` 를 `synthetic_reviews` → `naver_place` 등으로
- `market_data.closure_source`/`sales_source` 를 실 출처로
- 코어 계산기 변경 불필요 — `data_provenance.contains_synthetic` 이 자동으로 `false`

## 5. 다음 실행이 할 일

1. **최종 독립 QA 1회** — RS-06(라운드 2, RS04-D13 등) 수정이 QA 재검증 전. `30-review/qa-review.md` 최상단 "수정 라운드 2 상태" 절 참조
2. 사람 승인 게이트 (아래) 통과
3. `recommended_actions` (조치 제안) 와 `financial_products` 카탈로그 매처 구현 — 현재 빈 슬롯
4. 등급 flapping 히스테리시스 정책 결정 (경계 근처 월별 주의↔위험 반복)
5. `explanation_and_review_assist` LLM 분류 보조 연결 (현재 무동작)
6. `/Users/parkjunwoo/Documents/data-analysis` 의 `dev` 브랜치로 통합 PR — `service/siren/`, `tests/`, 계약 문서

## 6. 사람 승인 필요 (미해결)

- SR-05 수익성 악화의 신호 채택, 가중치, 악화 임계값, 순매출 0 처리 수치(현재 95), 사용자 문구
- 종합 가중치 w1~w5(0.45/0.35/0.20, 0.35/0.65), 층간 wm/wb(0.35/0.65)
- 40·70 등급 임계값
- SR-03 반경 250m, 유사업종 매핑(`demo/build.py`), "신규" = 인허가일 기준
- SR-04 리뷰 보조점수의 향후 종합 편입
- 폐업률 공식 명칭, 브랜드 코호트 폐업률 합성 폴백 고지 문구
- 합성 리뷰 텍스트 템플릿 실존 비지칭 최종 확인
- 경고 문구(정상·주의·위험)
- 금융상품 카탈로그·노출 규칙
- 이메일 공급자·실제 발송

## 7. 주의

데모 산출물의 수치는 대회용 합성 데이터다. 실제 가맹점 판정 결과가 아니며, 이 핸드오프를 운영 알림·금융상품 노출·발송 승인으로 해석하지 않는다.
