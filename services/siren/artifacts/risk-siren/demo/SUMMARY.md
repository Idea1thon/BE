# 데모 실행 요약

- 생성일 기준 as_of: 2026-03-31
- 가맹점: 20개 × 24개월 = 480 운영보고서
- 본사 집계: 위험 8 / 주의 4 / 정상 6 (danger_ratio 44.4444%, avg_score 60.0773)

| branch | 시나리오 | 구 | 업종 | score | grade | status | 리뷰 | 첫 사이렌 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| demo-br-01 | healthy | 강남구 | 한식음식점 | 23.1011 | 정상 | calculated | calculated | - |
| demo-br-02 | healthy | 강남구 | 한식음식점 | 32.474 | 정상 | calculated | missing | - |
| demo-br-03 | healthy | 강남구 | 중식음식점 | 21.1826 | 정상 | calculated | calculated | - |
| demo-br-04 | healthy | 광진구 | 중식음식점 | 30.1046 | 정상 | calculated | calculated | - |
| demo-br-05 | healthy | 광진구 | 일식음식점 | 20.2475 | 정상 | calculated | missing | - |
| demo-br-06 | slow_margin_squeeze | 광진구 | 일식음식점 | 52.6378 | 주의 | calculated | calculated | - |
| demo-br-07 | slow_margin_squeeze | 마포구 | 양식음식점 | 65.813 | 주의 | calculated | calculated | - |
| demo-br-08 | slow_margin_squeeze | 마포구 | 양식음식점 | 56.6577 | 주의 | calculated | calculated | - |
| demo-br-09 | slow_margin_squeeze | 중구 | 제과점 | 34.7777 | 정상 | calculated | missing | - |
| demo-br-10 | sales_decline | 마포구 | 패스트푸드점 | 89.1658 | 위험 | calculated | calculated | 2026-01-31 |
| demo-br-11 | sales_decline | 서초구 | 패스트푸드점 | 88.4879 | 위험 | calculated | calculated | 2026-02-28 |
| demo-br-12 | sales_decline | 영등포구 | 치킨전문점 | 90.6388 | 위험 | calculated | missing | 2025-10-31 |
| demo-br-13 | delivery_trap | 서초구 | 분식전문점 | 77.637 | 위험 | calculated | calculated | 2026-02-28 |
| demo-br-14 | delivery_trap | 서초구 | 분식전문점 | 72.7983 | 위험 | calculated | calculated | 2026-03-31 |
| demo-br-15 | delivery_trap | 성동구 | 호프-간이주점 | 69.8419 | 주의 | calculated | missing | - |
| demo-br-16 | debt_spiral | 성동구 | 호프-간이주점 | 83.3399 | 위험 | calculated | calculated | 2025-03-31 |
| demo-br-17 | debt_spiral | 성동구 | 커피-음료 | 85.928 | 위험 | calculated | calculated | 2025-10-31 |
| demo-br-18 | debt_spiral | 송파구 | 커피-음료 | 86.557 | 위험 | calculated | missing | 2025-10-31 |
| demo-br-19 | sparse | 송파구 | 한식음식점 | None | None | partial | calculated | - |
| demo-br-20 | sparse | 송파구 | 중식음식점 | None | None | partial | calculated | - |

## 시나리오별 기대

- **healthy**: 정상 — 시장 추세 추종, 마진 안정 → 기대 정상
- **slow_margin_squeeze**: 완만한 마진 잠식 — 매출 유지, 인건비·비용 상승 → 기대 주의
- **sales_decline**: 매출 감소 — 시장보다 빠른 순매출 하락 (가속) → 기대 주의~위험
- **delivery_trap**: 배달 의존 심화 — 수수료·포장인력으로 마진 붕괴 → 기대 주의~위험
- **debt_spiral**: 부채 악순환 — 대출이자 증가 + 쿠폰 남발 + 적자 → 기대 위험
- **sparse**: 보고 누락 — 최근 구간 운영보고서 없음 → 기대 partial (최근 구간 불완전)
