---
name: validating-recommendation-evidence
description: 입력 계약, 데이터 분류, 후보 선정, RAG Evidence의 경계면을 교차 검증하고 누수·grain 혼합·출처 누락·과도한 확정 표현을 반려할 때 사용한다. 검토 대상을 직접 수정하는 데에는 사용하지 않는다.
---

# Validating Recommendation Evidence

## 입력

- `artifacts/00-input.md`
- `artifacts/10-analysis/`
- `artifacts/20-method/`
- `.Codex/context/`

## 절차

1. `python3 .Codex/skills/validating-recommendation-evidence/scripts/validate_harness.py`를 실행한다.
2. UI 필드가 후보 생성 필터로 전달되는지 확인한다.
3. 후보 판정이 실제 Evidence 필드로 설명되는지 확인한다.
4. 기간, 업종, 공간 grain, 누락 처리, 출처를 교차 검증한다.
5. 과거매출 의존 모델을 운영 근거로 사용했는지 확인한다.
6. 결함 주입 사례를 실행해 검토가 반려하는지 확인한다.
7. 애매하면 실패 또는 미검증으로 둔다.

## 출력

`artifacts/30-review/recommendation-quality-review.md`

필수 섹션:

- 요약: 통과, 실패, 미검증, 사람 승인 필요
- 상세 검증: 기준, 판정, 근거, 조치
- 경계면 이슈
- 미검증 영역

## 품질 기준

- `score_is_predictive=false`가 없는 임시 점수는 High로 반려한다.
- 매물 데이터 없이 상세주소·링크를 만든 경우 Critical로 반려한다.
- 계단식 데이터를 분기 모멘텀으로 사용하면 High로 반려한다.
- 검토 대상은 수정하지 않는다.

