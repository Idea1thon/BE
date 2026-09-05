---
name: evaluating-legacy-ranking
description: 기존 Top-K 스코어링 모델을 오프라인 기준선으로 감사해 누수, 과거매출 의존, 모집단 제외, 성능 과신을 찾을 때 사용한다. 운영 추천 순위를 생성하거나 배포하는 데에는 사용하지 않는다.
---

# Evaluating Legacy Ranking

## 입력

- `scripts/phase1_analysis/scoring_topk_recommend.py`
- `scripts/phase1_analysis/scoring_evaluate_all.py`
- `docs/스코어링_추천모델_보고서.md`
- `output/scoring/`이 보존된 실행에서는 해당 결과를 읽는다. 현재 작업공간에서는 용량 절감을 위해 삭제되었으므로 보고서 수치와 코드만 감사한다.

## 절차

1. 피처 기간과 라벨 기간의 시간 순서를 확인한다.
2. 학습·검증 모집단에서 업종 점포 0인 상권이 빠지는지 확인한다.
3. 전체 피처와 과거매출 단독 성능을 비교한다.
4. 점수와 과거매출 상관을 확인한다.
5. 고정 k, 기저율, R-precision 등 평가 설계를 검토한다.
6. 업종별 계수 부호와 분산을 확인한다.
7. 결과를 운영 가능, 기준선 전용, 폐기로 구분한다.

## 출력

`artifacts/20-method/legacy-ranking-audit.md`에 주장, 수치 근거, 운영 금지 이유, 재검토 조건을 저장한다.

## 품질 기준

- 높은 F1을 신규 점포 성공 예측으로 재해석하지 않는다.
- 무작위 기준과 lift를 함께 본다.
- 기존 업종이 없는 지역의 제외를 명시한다.
- 실제 신규점포 outcome이 없으면 production-ready로 판정하지 않는다.
