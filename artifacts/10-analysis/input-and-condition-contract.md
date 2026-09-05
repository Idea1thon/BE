# 폼 입력 및 특별조건 계약

## LLM 입력 해석 단계

와이어프레임의 지역 선택값은 UI가 구조화된 값으로 전달하고, 자유 텍스트는 LLM이 `InputInterpretation` 초안으로 해석한다. 초안은 최종 요청 DTO가 아니며 C3 계약 검증기를 통과해야만 후보 생성에 전달된다.

```json
{
  "selected_region": {
    "sido": "서울특별시",
    "sigungu": "송파구",
    "dong": "잠실동"
  },
  "raw_user_text": "아침 손님이 많고 월세 300만 원 이하인 커피 매장",
  "llm_interpretation": {
    "industry_candidates": [
      {"industry_code": "CS100010", "name": "커피-음료", "confidence": 0.94}
    ],
    "conditions": {
      "monthly_rent_max_krw": 3000000,
      "operating_hours": "morning"
    },
    "clarification_questions": [],
    "unsupported_conditions": []
  }
}
```

계약 검증기는 선택 지역을 authoritative 값으로 취급하고, 업종·조건 후보의 코드·단위·지원 상태·충돌 여부를 검증한다. LLM의 confidence만으로 조건을 확정하지 않는다. 자유 텍스트에 업종이 없거나 여러 업종으로 해석되면 `confirmation_required=true`로 반환한다.

## 요청 DTO

```json
{
  "sido": "서울특별시",
  "sigungu": "송파구",
  "dong": "잠실동",
  "industry_code": "CS100010",
  "special_condition_text": "월세 300만원 이하, 20평 이상, 주차 가능",
  "conditions": {
    "monthly_rent_max_krw": 3000000,
    "deposit_max_krw": null,
    "store_area_min_m2": 66.12,
    "store_area_max_m2": null,
    "parking_required": true,
    "target_customer": [],
    "operating_hours": null,
    "business_mode": null
  },
  "confirmation_required": false,
  "unsupported_conditions": []
}
```

## 검증 규칙

- 필수: `sido`, `sigungu`, `industry_code`
- 선택: `dong`, 특별조건
- `industry_code`는 CS100001~CS100010만 허용한다.
- `sido` 변경 시 `sigungu`와 `dong`, `sigungu` 변경 시 `dong`을 초기화한다.
- 지역명은 `data/영역/행정동`의 코드·명칭과 매핑한다.
- 평 입력은 `1평 = 3.3058m²`로 변환하고 원문·원단위를 함께 보존한다.
- LLM이 제안한 조건은 계약 검증 전 자동 필터로 사용하지 않는다.
- 선택형 지역값은 LLM이 변경하거나 확장하지 않는다. 여러 지역 선택을 지원할 경우 별도 `selected_regions[]` 계약으로 확장한다.

## 확인이 필요한 경우

- 업종이 두 개 이상으로 해석됨
- `20평 이하`와 `30평 이상`처럼 조건 충돌
- `강남`처럼 구·상권·역세권 중 의미가 불분명함
- 월세인지 보증금인지 단위가 불명확함

## 지원 상태

- 현재 지원: 지역, 업종, 고객층·요일·시간대 적합성
- 부분 지원: 임대료·공실률은 넓은 권역 수준
- 미지원: 개별 매물 면적·주차·층·상세주소·현재 월세·보증금·링크

미지원 조건은 버리지 않고 `unsupported_conditions`에 저장해 결과 카드의 누락 데이터로 보여준다.

## 실패·대체 경로

- LLM 입력 해석 실패: 사용자가 명시한 필수 선택값만으로 해석 가능한지 확인하고, 불가능하면 후보 생성을 시작하지 않고 재입력을 요청한다.
- LLM 장애·예산 초과: 결정론적 최소 파서 또는 확인 질문으로 폴백하며, 임의 조건을 추정하지 않는다.
- 후보 결과 설명 단계의 LLM 장애는 Evidence 값 기반 템플릿 설명으로 폴백한다.
