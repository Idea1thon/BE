---
name: building-rag-evidence
description: 입지 후보를 출처·기간·공간 단위·추천 및 반대 근거·누락을 가진 RAG Evidence JSON과 사용자 설명 카드로 변환할 때 사용한다. 후보 선정이나 근거 없는 홍보 문구 생성에는 사용하지 않는다.
---

# Building RAG Evidence

## 입력

- `.claude/context/rag-contract.md`
- `artifacts/20-method/candidate-selection-spec.md`
- `artifacts/10-analysis/data-inventory.md`
- 실행 시 구조화 후보 목록

## 절차

1. 후보 ID, 공간 단위, 지역, 업종을 검증한다.
2. 각 지표를 단위, 분기, 비교 범위, 출처 경로와 함께 Evidence로 변환한다.
3. 추천 근거와 반대 근거를 별도 배열로 만든다.
4. 누락 데이터와 신뢰도 저하 이유를 기록한다.
5. 사용자 문장은 Evidence 값으로만 생성한다.
6. 숫자 회수는 메타데이터 필터 후 수행한다.

## 출력

- 스키마: `artifacts/20-method/rag-evidence-schema.json`
- 실행 데이터: `artifacts/20-method/candidate-evidence.json`

## 품질 기준

- 모든 수치 근거에 `period`, `spatial_grain`, `source_path`가 있다.
- `counter_evidence`가 빈 경우에도 빈 이유가 설명 가능하다.
- 데이터가 없으면 `null`을 유지한다.
- 정확한 주소·매물 링크는 검증된 매물 데이터가 있을 때만 제공한다.
- 합성 SNS는 관측 데이터와 혼동되지 않는다.

