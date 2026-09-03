---
name: auditing-location-data
description: 입지 추천 데이터의 스키마, grain, 기간, 최신성, 결합률, 누락과 추천 사용 적합성을 감사하고 사용·보조·제외·추가필요로 분류할 때 사용한다. 단순 파일 목록 출력이나 데이터 삭제에는 사용하지 않는다.
---

# Auditing Location Data

## 입력

- `.claude/context/data-catalog-contract.md`
- `artifacts/00-input.md`
- `data/`, 관련 `docs/`와 기존 품질 산출물

## 절차

1. 추천의 공간 단위, 업종, 기준 기간, 필요한 결과 수준을 확인한다.
2. 각 파일의 인코딩, 헤더, 행 수, 후보 키, 기간, 공간 grain, 업종 유무를 확인한다.
3. 핵심 키의 중복과 null, 결합 전후 행 수, 공간 매칭률을 검사한다.
4. 분기별 동일값 패턴으로 계단식 갱신 여부를 확인한다.
5. `core`, `conditional`, `audit-only`, `exclude`, `missing`으로 분류한다.
6. 추천 제외와 물리 삭제를 분리해 기록한다.
7. 다음 Agent의 판단을 바꾸는 결함은 `SendMessage`로 공유한다.

## 출력

- `artifacts/10-analysis/data-inventory.md`
- `artifacts/10-analysis/data-usage-classification.md`

각 파일에 목적, grain, 기간, 핵심 컬럼, 사용 역할, 금지 사용, 품질 위험, 근거 경로를 포함한다.

## 품질 기준

- 파일명만 보고 스키마를 추정하지 않는다.
- 매 실행 시 최신 분기와 실제 값 변경 시점을 다시 확인한다.
- 누락과 0을 구분한다.
- 업종이 없는 지표를 업종별 지표로 확장하지 않는다.
- 삭제는 사용자 승인 전 실행하지 않는다.

## 예외

- 파일을 읽을 수 없으면 경로와 오류를 남기고 `미검증`으로 분류한다.
- 키가 many-to-many면 결합을 중단하고 원인과 필요한 교차표를 제시한다.

