# 사이렌 Backend 연동 계약 보완

## 책임

Backend가 인증된 사용자 범위로 점포·보고서를 조회하고 사이렌에 전달한다. 사이렌은 계산 결과를 반환하며, 보고서 상태 전이·결과 저장·알림 생성과 발송·금융상품 조회는 Backend 책임이다. 이 변경은 호출·DB 저장 연동 자체를 구현하지 않는다.

본사 요약은 요청 franchise_id와 각 결과의 branch.franchise_id가 일치해야 한다. branch_id가 없거나 동일 점포 결과가 중복되면 422로 거부한다. 이 입력 검증은 Backend의 인증·권한 검사를 대체하지 않는다.

`unread_alert_count`는 본사 요약 응답에서 제거했다. 위험 이벤트 발생 여부는 미읽음 알림 개수가 아니다. Backend는 실제 Notification 수신자 및 읽음 상태로 계산한다. 데모 hq_summary.json은 여러 본사를 섞은 한 결과 대신 `summaries` 배열에 본사별 응답을 저장한다.

## 연간 가맹점 폐업 통계

분석 요청에 선택 객체 `franchise_closure`를 추가한다.

```json
{
  "franchise_id": "fr-001",
  "year": 2025,
  "previous_year_end_count": 100,
  "new_openings": 20,
  "closures": 12,
  "source": "본사 연간 실폐업 집계",
  "synthetic": false
}
```

- 분자 C = 해당 연도 실제 폐업 가맹점 수. 계약 종료·해지 건수는 실폐업 확인 없이 대체하지 않는다.
- B = 전년도 말 계약 유지 가맹점 수, N = 해당 연도 신규 개점 수.
- operating_base_rate_pct = C / (B + N) × 100. 예시 10%.
- previous_year_base_rate_pct = C / B × 100. 예시 12%.
- 분모가 0이면 해당 비율은 null이다. 전년도 기준 비율은 100%를 초과할 수 있으며 상한으로 자르지 않는다.
- 동일 브랜드·집계 기준을 사용하고 중복 개점/명의변경을 신규점으로 세지 않는다. C > B+N, 음수·소수·불리언 건수는 거부한다.
- 이 버전은 as_of 연도 이전에 완료된 연간 통계만 받는다. 당해 연도 누계(YTD)는 지원하지 않으며 연간 값으로 가장해 받지 않는다.
- 입력이 없으면 결과는 missing이며, 계산 불가를 0%로 표시하지 않는다.
- 응답에 분자·분모·출처·합성 여부와 formula_version을 포함한다.
- 공식기관 출처가 확인되지 않았으므로 formula_authority는 project_defined다. 국가 공인 공식이나 개별 점포 폐업 확률로 표시하지 않는다.
- 두 비율은 브랜드 배경 지표다. 기존 지역·업종별 분기 상권 통계와 합치지 않으며, 위험 점수·등급·경고에 중복 가산하지 않는다.

## 검증

저장소 루트에서 `python -m unittest discover -s services/siren/tests -v`.
데모 재생성: `python -m services.siren.demo.build --reuse` (저장된 공개 지표 기준선과 합성 점포 데이터 사용).

## 확인된 점포 위험 경고와 정적 리뷰 보완

- 시장 자료가 없거나 일부 지표가 미완성일 때 종합 점수·등급은 계속 null이다. 다만 `calculated`인 점포 층 또는 수익성 지표가 기존 위험 하한(`caution_upper_bound`) 이상이고 해당 지표의 evidence가 있으면 `alert.should_fire=true`로 반환한다.
- `alert.trigger`는 basis(`composite`, `branch_risk`, `profitability`), 해당 점수, 기준값, 근거 ID를 제공한다. 점포 경고를 종합 위험 등급으로 표시하지 않는다. 종합 등급이 주의여도 확정된 수익성 위험 경고는 유지하며, 시장 자료 유무로 같은 점포 경고가 사라지지 않는다. 낮거나 미확정인 점포 신호, 데이터 누락 자체만으로 경고하지 않는다.
- 점주·본사 projection에도 alert를 전달하고, 본사 watchlist에 부분 분석의 점포 경고를 표시한다. 실제 발송은 여전히 disabled다.
- 연속 적자는 as_of 월부터 한 달씩 역순으로 확인한다. 중간 월 누락 또는 흑자/손익0에서 중단하며, 기준 월 자료가 없으면 현재 연속 적자는 0이다. 이는 누락 기간이 안전하다는 의미가 아니다. 기존 missing/partial 표시는 유지한다.
- 본사 요약은 집계에 사용하는 중첩 객체를 검증한다. null 객체, 점수 범위 밖 값·불리언, 잘못된 등급 및 상태/점수 모순은 422다. 비집계 필드는 무시한다.
- 각 branch.as_of는 요청 as_of와 정확히 같아야 한다. 미래·과거 결과를 섞지 않으며, 누락·잘못된 날짜도422다. Backend는 동일 기준일의 결과 묶음을 전달해야 한다.
- 적자 계산 동작 변경을 구분하기 위해 score_version은 risk-siren-v1.1-provisional로 갱신했다. 이벤트 중복 방지 키에도 이 버전이 반영된다. alert_policy_version은 confirmed-branch-v1이다.
