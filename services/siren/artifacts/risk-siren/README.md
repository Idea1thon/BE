# 위험 사이렌 실행 산출물 지도

## 현재 상태

- 실행 목적: 폐업 사이렌 파이프라인 v1 + 대회용 데모 데이터셋
- 실행 모드: RS-01~RS-03 완료 → RS-04 독립 QA 2회 → RS-05·RS-06 수정 2라운드 → RS-07 핸드오프. **dev PR 전 최종 QA 1회 권고**
- 마지막 갱신: 2026-09-04
- score_version: `risk-siren-v1-provisional`
- 승인 상태: 사람 승인 필요 (가중치·임계값·SR-05 경계·경고 문구·금융상품·발송)
- 실제 발송: disabled

## 산출물 지도

| 파일 또는 디렉터리 | 역할 | 만든 단계 | 상태 | 승인 |
| --- | --- | --- | --- | --- |
| document/risk-siren/ | 요구사항·설계 정본 (v1 개정 반영) | Orchestrator | current | 사람 승인 필요 |
| 10-analysis/signal-audit.md | 5신호·2층 감사 (실데이터 검증) | RS-01 | current | 사람 승인 필요 |
| 20-method/input-output-contract.md | v1 API 계약 (2층·provenance·projections) | RS-02 | current | 사람 승인 필요 |
| service/siren/ (models·reports·risk_signals·pipeline·alerts·explanation·hq_summary·api) | v1 구현 | RS-03 | current | 사람 승인 필요 |
| service/siren/demo/ | 대회용 합성 데이터 생성기 | RS-03 | current | 해당 없음 (합성 표시) |
| tests/test_risk_siren.py | 25개 unittest (정상·애매·실패·부정·반복 + QA 결함 회귀) | RS-03/05/06 | current | — |
| artifacts/risk-siren/demo/ | market_baseline.json, 480 운영보고서, 결과, 롤링 타임라인, hq_summary | RS-03 | current | 데모 전용 |
| 30-implementation/implementation-notes.md | v1 구현 메모 + 수정 라운드 표 | RS-03/05/06 | current | 사람 승인 필요 |
| 30-review/qa-review.md | 독립 QA 2회 + 라운드 2 상태 | RS-04 | current (라운드 2 수정은 QA 재검증 전) | 사람 승인 필요 |
| final/implementation-handoff.md | v1 핸드오프 | RS-07 | current | 사람 승인 필요 |
| improvement-log.md | 실패·개선 기록 | Orchestrator | current | — |

## 상태 규칙

- current: 최신 입력 반영
- stale: 앞 단계 변경으로 재실행 필요
- needs-review: QA 또는 사람 확인 필요
- archived: 이전 실행 보관

## 사람 승인 필요

- SR-05 수익성 악화 신호 채택, 가중치, 악화 임계값, 사용자 문구
- 종합 가중치(w1~w5, 층간)와 40·70 등급 임계값
- 폐업률 공식 명칭, 브랜드 코호트 폐업률의 데모 합성 고지
- SR-04 리뷰 보조점수의 향후 종합 편입, 합성 리뷰 텍스트 템플릿
- SR-03 반경 250m·유사업종 매핑
- 금융상품 카탈로그·노출 규칙, `recommended_actions` 내용
- 이메일 공급자·실제 발송

## 데모 요약

as_of 2026-03-31 기준 20 가맹점: 위험 8 / 주의 4 / 정상 6 / partial 2.
롤링 타임라인에 월별 운영보고서 누적에 따른 사이렌 발동 시점 기록.
모든 결과에 `data_provenance.contains_synthetic = true` + disclosure.
