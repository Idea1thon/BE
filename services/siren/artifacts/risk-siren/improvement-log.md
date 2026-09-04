# 위험 사이렌 하네스 개선 기록

## 2026-09-04 — 초기 scaffold

- 변경: document/risk-siren 요구사항·계약·파이프라인·테스트·ADR 추가
- 변경: 위험 신호 분석, 계약 설계, 구현, 독립 QA Agent 추가
- 변경: auditing, contract, implementation, validation, orchestrator Skill 추가
- 변경: Agent Team 실행·부분 재실행·사람 승인·disabled dispatch 규칙 추가
- 검증: 구조 validator 실행 예정
- 남은 문제: 실제 데이터 감사, FastAPI 구현, 가중치·임계값 승인, 발송 adapter

## 2026-09-04 — 외부 작업공간 이관 및 1차 구현

- 변경: 위험 사이렌 하네스·멀티에이전트·설계 문서를 `/Users/parkjunwoo/Documents/siren`으로 이동
- 변경: `service/siren`에 요청 계약, 네 신호 계산기, 종합 파이프라인, Evidence, disabled alert, 설명 adapter, FastAPI route 추가
- 검증: 5개 unittest, 하네스 구조 validator, import smoke test 통과
- 정책: 가중치·기준값은 `risk-siren-v0-provisional`로 격리하고 종합 신호 누락 시 `partial` 반환
- 남은 문제: 실제 데이터 source 연결, 가중치·임계값 승인, 금융상품 카탈로그, 발송 adapter, dev 브랜치 통합

## 2026-09-04 — v1: 2층 모델 + SR-05 + 대회용 데모 데이터셋

- 결정(사용자): 데이터 접근 제약상 (1) 상권×업종 실측 baseline + 가맹점 단위 2층 분리, (2) SR-05 수익성 악화 신호 추가, (3) 가맹점 매출·손익·리뷰는 대회용 합성 생성(실 추세 앵커, "생성" 명시), (4) SR-04는 보조 신호, (5) 데모 20 가맹점 × 24개월
- 변경: 신호 감사 재실행(RS-01), API 계약 v1(RS-02), `service/siren` 전면 개편(RS-03) — `models`/`reports`/`risk_signals`/`pipeline`/`hq_summary`, `demo/` 서브패키지
- 변경: 서울시 공개데이터로 SR-01(인허가 상권분기 패널, 폐업률 분모=직전분기 영업중 검증), SR-02.market(추정매출 분기), SR-03(인허가 raw 반경 EPSG:5181) 실측 연결
- 변경: `data_provenance` 블록으로 모든 결과·Evidence·설명·본사 집계에 합성 여부 구조화
- 변경: 종합 점수 = `max(branch_layer, 0.35·market + 0.65·branch)` — 양호한 시장이 가맹점 위험을 상쇄하지 못하게. SR-05 ≥ 80이면 단독으로 branch_layer를 위험 수준으로
- 검증: unittest, harness validator, 데모 2회 재생성 바이트 동일(결정론)
- near-miss: 초기 P&L 원가율 이중계상으로 healthy도 적자 → 식자재 비중을 food-only로 분리하고 인건비를 고정+변동으로 재모델링. 시장지수가 시나리오를 상쇄 → 감쇠(0.7+0.3·clamp) 적용

## 2026-09-04 — RS-04 독립 QA 2회 + 수정 라운드 2

- RS-04 라운드 1 (독립 risk-qa-reviewer): NOT PASS, High 2 (RS04-D01 최근 3개월 순매출 ≤ 0 무경보, RS04-D02 허위 provenance 고지), Medium 10, Low 4
- RS-05 수정 라운드 1: High 2 + 계약 위반 Medium(D03 그림자 필드, D04 excluded_future_months, D06 미완결 분기, D07 반경 없는 경쟁 점수, D08 폐업률 100% 초과, D09 리뷰 branch 키, D10 event_type, D11 부분 분기 rolling, D12 신호별 source) 수정. unittest 17→24
- RS-04 라운드 2 (독립): 1차 High 2 + Medium 10 전부 해결 확인. **신규 High 1 (RS04-D13)** — D12 수정이 만든 표면: `_is_synthetic`이 `market_data.source`만 보고 `closure_source`/`sales_source`(D12가 추가)는 안 봄 → 폐업률만 합성 폴백일 때 `contains_synthetic=false` 허위 고지
- RS-06 수정 라운드 2: RS04-D13 → synthetic 판정을 신호별로 분리(`closure_synth`/`market_sales_synth`/`comp_synth`). L05(순매출0 필드 정합), L06(계약 §5), L08(데모 미래분기 절단) 수정. unittest 24→25
- near-miss: D12(신호별 출처 분리)가 D13(신호별 synthetic 판정 누락)을 낳음 — "필드를 쪼개면 그 필드를 읽는 모든 로직도 쪼개야 한다"
- 잔여: RS04-D13 수정이 **독립 QA 재검증 전**(SendMessage 비활성으로 동일 에이전트 재개 불가, 2라운드 자동 루프 소진) → dev 통합 PR 전 최종 QA 1회 권고. 경계(70점) flapping 히스테리시스 정책, 가중치·임계값·SR-05 트리거·순매출0 처리 수치 전부 provisional·사람 승인 대기. 금융상품·recommended_actions 미구현
