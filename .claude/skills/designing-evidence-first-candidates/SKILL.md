---
name: designing-evidence-first-candidates
description: 선택 지역과 업종 안에서 하드 조건, 데이터 품질, 분리된 근거 차원과 후보 등급으로 설명 가능한 입지 후보 선정 명세를 만들 때 사용한다. 검증되지 않은 성공확률 학습이나 단순 Top-K 점수 정렬에는 사용하지 않는다.
---

# Designing Evidence-first Candidates

## 입력

- `.claude/context/project-context.md`
- `.claude/context/metric-contract.md`
- `artifacts/10-analysis/data-inventory.md`
- `artifacts/10-analysis/input-and-condition-contract.md`

## 절차

1. 사용자 지역과 겹치는 상권 폴리곤을 후보 모집단으로 만든다.
2. 상권이 없으면 상권배후지, 그마저 없으면 행정동 fallback을 사용한다.
3. 업종 점포가 0인 후보도 유지하되 `greenfield`로 표시한다.
4. 필수조건과 데이터 지원 여부를 구분해 하드 필터를 적용한다.
5. 현재 수요, 경쟁·시장수용, 진입 건전성, 비용, 수요 구성, 미래 신호, 데이터 신뢰도를 분리 계산한다.
6. 각 차원의 긍정·부정 근거를 보존한다.
7. `추천`, `조건부 검토`, `주의`로 분류한다.
8. 화면 표시 개수는 기본 3~5개로 제한할 수 있으나, 이를 모델의 정확한 Top-K라고 부르지 않는다.

## 선택 규칙

- 지역 내부 분위와 서울 전체 분위의 두 비교 기준을 제공한다.
- 동일 등급에서는 필수조건 충족, 신뢰도, 근거 수, 반대 근거 심각도 순으로 정렬한다.
- 공실·임대료가 없으면 중립값으로 조용히 대체하지 않는다.
- 종합값이 필요하면 `fit_index_v1`, 산식 버전, 누락 처리, `score_is_predictive=false`를 반환한다.

## 출력

`artifacts/20-method/candidate-selection-spec.md`에 모집단, 필터, 지표, 등급, 정렬, 누락 처리, greenfield 처리, 실패 조건을 기록한다.

## 품질 기준

- 미래 기간 데이터가 후보 선정 피처에 들어가지 않는다.
- 매출 수준만으로 추천 등급이 결정되지 않는다.
- 상권·배후지·행정동 동일 개념을 중복 가산하지 않는다.
- 근거가 부족하면 후보 수를 억지로 채우지 않는다.

