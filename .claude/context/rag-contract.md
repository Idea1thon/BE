# RAG 근거 계약

RAG는 후보를 새로 발명하거나 순위를 학습하지 않는다. 후보 선정 단계가 만든 구조화 결과를 읽고 사용자가 이해할 수 있는 근거 설명을 생성한다.

LLM은 사용자 입력 이후 분석 계획을 제안하거나 읽기 전용 분석 도구를 호출할 수 있지만, LLM의 분석 계획·조건 해석·가설은 계약 검증과 Evidence 생성 전까지 확정값이 아니다. 관측 패턴은 기본적으로 기술적 사실 또는 연관성으로만 표현하고, 인과관계는 별도 식별 설계와 QA를 통과한 경우에만 허용한다.

## 후보 근거 객체

```json
{
  "candidate_id": "string",
  "candidate_type": "commercial_area|hinterland|admin_dong|property",
  "region": {"sido": "서울특별시", "sigungu": "string", "dong": null},
  "industry_code": "CS100010",
  "fit_tier": "추천|조건부 검토|주의",
  "fit_index": null,
  "score_is_predictive": false,
  "data_confidence": {"level": "high|medium|low", "reasons": []},
  "reasons": [],
  "counter_evidence": [],
  "missing_features": [],
  "source_freshness": {},
  "evidence": [],
  "listing_url": null
}
```

각 `evidence`는 `metric_name`, `value`, `unit`, `comparison_scope`, `comparison_value` 또는 `percentile`, `period`, `spatial_grain`, `source_path`, `interpretation`, `limitation`을 포함한다.

## 생성 규칙

- 추천 이유와 반대 근거를 모두 작성한다.
- 값이 없으면 `missing_features`에 이유를 쓴다.
- 근거 문장은 후보 객체의 실제 값으로 역추적 가능해야 한다.
- 한 문장에 서로 다른 공간 단위의 수치를 섞지 않는다.
- `성공한다`, `수익이 보장된다`, `정확도가 높다` 같은 확정 표현을 금지한다.
- 정확한 주소와 매물 링크는 검증된 외부 매물 데이터가 있을 때만 채운다.
- 합성 SNS 데이터는 `source_type=synthetic`과 생성일·생성 규칙을 표시한다.
- **지역 특성**은 FC 프로파일의 중립 관측값이고, **좋은 입지**는 사용자 업종·하드 조건·분리된 근거·반대 근거·결측을 결합한 판정이다. RAG는 이 둘을 바꾸거나 새 점수로 합치지 않는다.
- Kakao POI는 요청 영역과 일치하고 `coverage.status=complete_requested_queries`인 `context/` manifest가 있을 때만 지점 반경 관측으로 넣는다. `spatial_grain=지점`, 스냅샷 시각·카테고리·PIP·중복 제거 출처를 남긴다.
- POI 수는 관측된 공급·생활 맥락이며 지역 특성 FC·수요·공실·성공 outcome이 아니다. `fit_tier`, 정렬, 긍정 이유·반대 근거를 단독으로 바꾸지 않는다.

## 검색 단위

후보 Evidence JSON, 지표 정의, 데이터 품질, 공식 이벤트 문서를 분리한다. 수치를 고를 때는 벡터 유사도보다 후보 ID·업종·분기·공간 단위 메타데이터 필터를 먼저 적용한다.
