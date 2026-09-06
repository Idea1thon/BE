# 추천 DB 데이터와 RAG 검색 범위

이 문서는 `dev` 기반 코드의 스키마·적재 스크립트·`DbSource`를 대조한 설계 목록이다. 실제 DB에 접속하지 않았으므로 테이블 존재, 적재 행 수, 최신 기간, 운영 권한을 확인한 결과가 아니다. 아래의 “기존 반영”은 이번 확장 전 추천 파이프라인의 조회 경로를 뜻한다. 새 검색 도구의 최종 지원 목록은 `recommendation/rag_tools.py`의 허용 목록과 쿼리를 기준으로 확인한다.

“DB 전체를 검색 대상으로”는 추천 서비스에 정의된 원천 데이터 계열을 검색 카탈로그에 포함한다는 뜻이다. 모든 테이블·열을 한꺼번에 LLM에 전달하거나 LLM이 임의 SQL을 실행한다는 뜻은 아니다. 다른 서비스의 사용자·계정·운영보고서 테이블은 이 추천 DB 조사 범위에 포함되지 않는다.

## 데이터 계열별 매핑

| 데이터 계열 / DB 관계 | 기존 후보·설명 반영 | 검색 확장 시 필요한 처리 |
| --- | --- | --- |
| 매출 `location.sales_quarter` | `scope_index()`가 선택 업종·분기 매출 합계를 읽어 상권 지표에 사용. 기존 RAG에도 포함 | 지역·업종·기간을 함께 반환. 요일·주말·세부 매출은 전체 JSON 복사 대신 정의된 지표와 단위를 선택 |
| 점포 `location.store_quarter` | 전체·프랜차이즈 점포 수 사용. 기존 RAG에도 포함 | 개업·폐업 등 추가 열은 집계 정의와 분모를 구분하여 노출 |
| 유동인구 `location.flow_quarter` | 총유동인구와 공간 면적 기반 지표에 사용. 기존 RAG에도 포함 | 총량과 밀도를 구분. 인구 유형을 상주·직장·외국인과 혼합하지 않음 |
| 상권변화 `context.metric_snapshot` | `change_indicator_code/name`을 피벗하여 사용. 기존 RAG에도 포함 | 코드와 명칭의 대응, 분기·공간 범위를 보존 |
| 전체 업종 개폐업 `location.area_store_totals` | `environment()`가 현재·직전 분기의 전체·개업·폐업 점포를 읽음 | 업종별 통계와 구별하여 검색. 분기 결측을 0으로 바꾸지 않음 |
| 인허가 집계 `location.permit_quarter` | `DbSource`의 추천 후보 조회에서 직접 사용하지 않음 | 상권·업종·분기별 영업·신규·폐업 수와 원천 비율, `quarter_status`를 반환. 다른 폐업률 산식과 합치지 않음 |
| 인허가 사업체 `location.permitted_establishment` | `DbSource`에서 직접 사용하지 않음 | 선택 지역·업종의 허가·폐업 상태 검색. 날짜·좌표·원천 유형을 보존하고 식별되지 않은 공간 행을 임의 귀속하지 않음 |
| 역·버스·아파트 `location.anchor_point`, `context.anchor_snapshot` | 실제 seed·반경 계산은 `anchor_snapshot` 원본 행을 읽음. 정규화된 `anchor_point`와 서빙 역할이 다름 | 공간 조건을 적용한 시설 검색. 좌표 신뢰도·합성 앵커 여부를 보존. 두 테이블의 같은 시설을 중복 근거로 세지 않음 |
| POI `context.poi_snapshot`, `anchor_snapshot`의 `kakao_poi` | 일부 POI는 seed로 반영. `load_completed_poi_context()`는 별도 파일 경로. `poi_snapshot` 직접 검색은 없음 | 지역·카테고리·수집시각 기반 검색. 원천 장소 URL·검색 반경을 보존하고 POI 존재를 입점 가능 매물로 해석하지 않음 |
| 건물 `context.commercial_building` | 건물 seed, 용도·면적·층수·연식·공간 귀속을 읽음 | 선택 지역 건물 검색. 건물 스냅샷과 공간 결합 유형을 보존하고 중복 스냅샷 정책을 명시 |
| 건물대장 `context.building_register`, `commercial_building_link`, `building_floor_use` | 주소·건물명·상업층 존재 여부 일부만 결합. 한 지번에 복수 건물이면 단일 건물 승격을 막음 | 주차·승강기·층별 용도 등 상세 검색. 기존 복수 건물 미해결 규칙을 유지하고 건물 전체 면적을 개별 점포 면적으로 사용하지 않음 |
| 건물 집계 `context.commercial_building_area_summary` | DDL에 정의된 materialized view. 기존 `DbSource`에서 직접 조회하지 않음 | 상권별 건물 특성 검색에 사용 가능. 스냅샷과 집계 단위를 보존. 원천 건물과 집계 수치를 같은 수준으로 취급하지 않음 |
| 임대·공실 `context.rent_index` | 소규모상가의 임대가격지수·공실률을 최신 행 기준으로 읽음. `area_crosswalk`로 R-ONE 상권 대리 결합 | 다른 상가 유형·지표도 검색 대상에 포함. 지수·천원/㎡·비율을 구별하고 대리 상권과 실제 선택 지역을 함께 표기 |
| 검색 관심도 `context.metric_snapshot`의 `naver_rel_index` | 선택 업종의 월별 상대지수를 읽어 계산 | 월 단위와 상대지수임을 보존. 서울/지역 관심도를 건물 수요로 해석하지 않음 |
| 검색 계절성 `context.naver_seasonality` | 테이블은 정의되어 있으나 DB 경로 `naver_attention()`은 `season = {}`로 처리 | 업종·자치구·행정동 단위로 제한하여 추가 검색. 파생 계절성·급등 지표를 실매출로 해석하지 않음 |
| 상주·직장·외국인 `context.population_snapshot` | `population()`이 데이터셋별 정해진 `as_of` 파티션을 읽어 조립. 일부 상권·행정동 결합은 crosswalk 사용 | 데이터셋·공간 단위·기간별 검색. 외국인 관측일수와 평균, 겹침 기반 대리 결합을 보존. 파티션 간 기간이 다를 수 있음을 명시 |
| 도시계획·정비사업 `context.plan_snapshot` | `urban_project_overlap`, `redevelopment_association` 두 유형을 `urban_plan.load_from_db()`에서 읽음 | 계획 유형·추진 단계·관측일·겹침 범위를 검색. 계획을 준공·영업 중 시설로 표현하지 않음 |
| 계획 도시철도 `context.subway_network_plan` | `urban_plan`이 DB 우선, 부재 시 파일로 읽음. 자치구별 계획 노선 배경으로 반영 | 자치구와 노선 레코드를 결합하여 검색. 계획 노선의 자치구 포함을 후보 반경 내 역 존재로 바꾸지 않음 |
| 뉴스 `context.news_snapshot` | `news_catalogs()`가 지역·주제 태그를 읽어 집계. 제목·본문 기반 질의 검색은 하지 않음 | 지역·기간·주제별 제목·URL·발행처 검색. 날짜와 출처를 포함하며 기사 내용을 검증된 행정 사실이나 인과관계로 승격하지 않음 |

## 원천 검색과 구분할 관계

| 관계 | 역할 |
| --- | --- |
| `location.area` | 선택 공간과 코드·명칭·폴리곤의 기준. 다른 데이터에 적용할 지역 필터·공간 결합에 사용 |
| `location.industry` | 업종 코드·명칭 사전. 검색 파라미터 검증과 설명에 사용 |
| `location.area_crosswalk` | 공간 연결·대리 결합 기준. `join_eligible`, 관계 유형 및 겹침 정보를 유지 |
| `meta.dataset_file`, `meta.dataset_run` | 원천 파일과 적재 실행 메타데이터. 출처·신선도·가용성 확인용이며 독립적인 상권 관측값이 아님 |
| `context.news_manifest` | 뉴스 검색 조건·기간·수집 품질 메타데이터. 기사 원천과 함께 사용 |
| `evidence.recommendation_run`, `evidence.candidate` | 추천 요청·계산 결과. 과거 모델 산출물을 새 원천 관측으로 재투입하면 근거가 순환하므로 원천 검색에서 구분 |

## 구현·검증 기준

1. 도구는 질문에 필요한 데이터 계열을 고르고, 서버가 테이블·열·지역·기간·업종과 행 수 상한을 결정한다. 데이터 계열마다 월·분기·스냅샷 날짜가 다르므로 단일 분기 문자열을 모든 테이블에 적용하지 않는다.
2. 검색 결과는 근거 ID, 출처 테이블·레코드, 관측 기간, 공간 단위, 업종, 단위와 결측·대리 결합·계획 상태를 보존한다. 숫자가 없는 행에 0이나 현재 날짜를 만들지 않는다.
3. 새로 검색한 지역 배경은 인용 가능한 설명 근거로 제공한다. 후보 점수·등급에 반영하려면 별도로 정의한 계산·검증 경로를 거친다.
4. 선택적 테이블 부재와 조회 0건은 해당 계열의 가용성 결과로 드러낸다. 그 결과를 모든 DB 데이터가 정상 적재됐다는 의미로 표시하지 않는다.
5. 모의 쿼리 테스트는 SQL 범위 제한·정규화·출처 계약만 검증한다. 운영 배포 전 실제 스키마 및 열 호환성, 읽기 권한, 데이터 기간, 선택 지역의 결과와 쿼리 비용을 별도로 확인해야 한다.

## 조사 근거

- `services/recommendation-api/db/000_location_schema.sql`
- `services/recommendation-api/db/001_missing_serving_tables.sql`
- `services/recommendation-api/scripts/ingest_population.py`
- `services/recommendation-api/scripts/ingest_subway_plan.py`
- `services/recommendation-api/recommendation/pipeline.py`: `DbSource`, `run_pipeline`
- `services/recommendation-api/recommendation/urban_plan.py`: `load_from_db`, `_load_subway_plan_from_db`

인구와 계획 도시철도 테이블의 DDL은 000/001이 아니라 각각의 적재 스크립트에 있다. 000/001만 적용한 환경에서 해당 테이블이 있다고 가정하면 안 된다.


## 구현된 질문별 보조 검색

현재 SQL 허용 차원은 매출·점포·유동·상권변화와 임대가격지수·공실률·직장인구다. 질문에서 선택한 차원만 최종 후보 상권(최대50개)에서 읽는다. 임대·공실은 유일한 적격 R-ONE 권역의 최신 소규모상가 관측, 직장인구는 요청 분기 이하 최신 관측을 사용하며, 기존 매출·점포·유동·변화는 요청 분기를 사용한다. 복수 임대 권역·테이블 미적재·조회 오류는 임의 값이나 0으로 대체하지 않는다.

출처 목록 전체는 감사용으로 보존하고 설명 모델에는 질문 관련 출처 총 최대16개와 필수 경고만 제공한다. 정규화 SQL을 출처가 붙은 배경 문장으로 서버 렌더링하며, 선택 후보 사이의 동일 지표 관측 차이는 별도 비교표와 카드에 연결한다. 개별 매물 월세·실제 점심 방문량은 이번 검색으로 확인되지 않는다. 자세한 응답·기간·비교 불가 계약은 `services/recommendation-api/docs/rag-grounded-explanations.md`를 따른다.
