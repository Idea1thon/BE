---
name: defining-ui-input-contract
description: 입지 추천 웹 폼의 시도·시군구·행정동·업종·특별조건을 구조화하고 검증·애매함 처리 규칙과 API 입력 계약을 만들 때 사용한다. 일반 프롬프트 작성이나 UI 스타일링에는 사용하지 않는다.
---

# Defining UI Input Contract

## 입력

- `.claude/context/project-context.md`
- `artifacts/10-analysis/data-inventory.md`
- 사용자 화면 요구

## 절차

1. `sido`와 `sigungu`는 필수, `dong`은 선택으로 정의한다.
2. 지역 선택은 상위 값이 바뀌면 하위 값을 초기화한다.
3. 업종은 10개 코드 중 하나인 필수 필드로 정규화한다.
4. 특별조건을 아래 구조로 파싱한다.
   - `monthly_rent_max_krw`, `deposit_max_krw`
   - `store_area_min_m2`, `store_area_max_m2`
   - `parking_required`
   - `target_customer`
   - `operating_hours`
   - `business_mode`: delivery, dine_in, mixed
5. 원문, 파싱값, 파싱 신뢰 상태, 확인 필요 항목을 함께 보존한다.
6. 데이터가 없는 조건은 `unsupported_conditions`에 넣고 자동 필터로 쓰지 않는다.

## 출력

`artifacts/10-analysis/input-and-condition-contract.md`에 JSON 예시, 필수값, enum, 오류 코드, 애매함 확인 규칙을 저장한다.

기본 예시:

```json
{
  "sido": "서울특별시",
  "sigungu": "송파구",
  "dong": "잠실동",
  "industry_code": "CS100010",
  "special_condition_text": "월세 300만원 이하, 20평, 주차 가능",
  "conditions": {
    "monthly_rent_max_krw": 3000000,
    "store_area_min_m2": 66.12,
    "parking_required": true
  },
  "confirmation_required": false
}
```

## 품질 기준

- 평과 제곱미터 변환값과 원단위를 함께 보존한다.
- 상충 조건은 임의 선택하지 않고 `confirmation_required=true`로 반환한다.
- 업종 누락 시 추천 실행을 시작하지 않는다.
- 매물 데이터가 없으면 월세·면적·주차를 완전 지원이라고 표시하지 않는다.

