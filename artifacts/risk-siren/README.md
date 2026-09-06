# 위험 사이렌 실행 산출물 지도

## 현재 상태

- 실행 목적: 폐업 사이렌 파이프라인 v1 + 대회용 데모 데이터셋
- 실행 모드: RS-01~RS-03 완료 → RS-04 독립 QA 2회 → RS-05·RS-06 수정 2라운드 → RS-07 핸드오프 → **PR #11 리뷰 대응 부분 재실행(RS-02 계약 v1.1 → RS-03 구현 → RS-04 독립 QA) 전부 완료**. Critical/High 0건. 계약 §9 A~G 사람 승인·회신 대기 상태로 안전 기본값(strict/disabled/null)에 게이트됨.
- 마지막 갱신: 2026-09-07 (역할별 projection·FMP/IDEATON provider 경계 반영)
- score_version: `risk-siren-v1-provisional` / contract_version: `risk-siren-contract-v1.1`
- 승인 상태: 사람 승인 필요 (가중치·임계값·SR-05 경계·경고 문구·금융상품·발송)
- 실제 발송: disabled

v1.2 구현 경계: `branch_owner`는 본인 점포 상세 projection, `franchise_hq`는
자사 점포 상태·watchlist·집계 projection만 middle-backend가 노출한다.
Siren의 ID-only trigger는 FMP/IDEATON을 읽기 전용으로 조회한다.

## 산출물 지도

| 파일 또는 디렉터리 | 역할 | 만든 단계 | 상태 | 승인 |
| --- | --- | --- | --- | --- |
| document/risk-siren/ | 요구사항·설계 정본 (v1 개정 반영) | Orchestrator | current | 사람 승인 필요 |
| 10-analysis/signal-audit.md | 5신호·2층 감사 (실데이터 검증) | RS-01 | current | 사람 승인 필요 |
| 20-method/input-output-contract.md | v1.1 API 계약 (2층·provenance·projections + PR #11 대응) | RS-02 / RS-02 재실행 | current | 사람 승인 필요 |
| 20-method/pr11-review-response.md | PR #11 리뷰 항목별 결정·제안·승인 필요 표 + PR 댓글 초안 | RS-02 재실행 | current | 사람 승인 필요 |
| service/siren/ (models·reports·risk_signals·pipeline·alerts·explanation·hq_summary·api) | v1.1 구현 (계약 v1.1 반영 완료 — risk_level 단일 파생, grade_policy, financial_products grade_only 등) | RS-03 재실행 | current | 사람 승인 필요 |
| service/siren/demo/ | 대회용 합성 데이터 생성기 | RS-03 | current | 해당 없음 (합성 표시) |
| tests/test_risk_siren.py | 45개 unittest (기존 25 + PR11 대응 20, 정상·애매·실패·부정·반복 + QA 결함 회귀) | RS-03/05/06 + 재실행 | current | — |
| artifacts/risk-siren/demo/ | market_baseline.json, 480 운영보고서, 결과, 롤링 타임라인, hq_summary (contract_version v1.1 반영) | RS-03 | current | 데모 전용 |
| 30-implementation/implementation-notes.md | v1 구현 메모 + 수정 라운드 표 + "10. RS-03 PR11-fix" | RS-03/05/06 + 재실행 | current | 사람 승인 필요 |
| 30-review/qa-review.md | 독립 QA 2회(v1) + "RS-04 PR11-fix 재검증"(Critical/High/Medium 0, Low 1) | RS-04 + 재실행 | current | 사람 승인 필요 |
| final/implementation-handoff.md | v1 핸드오프 + PR #11 대응 라운드 요약(§0) | RS-07 + 재실행 | current | 사람 승인 필요 |
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
