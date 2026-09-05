# 입지 추천 데이터 스키마

작성일: 2026-09-03
작성 주체: GPT(Codex)
목적: Claude Code가 원천 파일을 다시 전수 탐색하지 않고도 데이터의 단위·키·기간·출처·추천 사용 경계를 이해하고 재현할 수 있도록 하는 정규화 계약

기계 판독용 레지스트리는 [`data-schema.json`](data-schema.json)이다. 이 문서는 개별 행 검증용 JSON Schema가 아니라 **원천 데이터셋 레지스트리와 결합 계약**이다. 원천 CSV 컬럼명은 그대로 보존하고, 추천 단계에서만 아래 정규화 개념을 사용한다.

## 0. 저장소 실행 환경

논리 스키마는 로컬 원본과 Docker 팀 공유 미러에서 동일하게 유지한다.

| 대상 | PostgreSQL | 접속 | 용도 | 비고 |
| --- | --- | --- | --- | --- |
| 로컬 원본 | 18.4 (Postgres.app) | `127.0.0.1:5432/ideaton` | 개인 적재·검증 | 현재 `trust` 인증. 외부 공개 금지 |
| Docker 팀 공유 미러 | 16.14 | `<Docker 호스트 LAN/VPN IP>:55432/ideaton` | 팀원 공용 조회·개발 | PostGIS 3.6.4·pgvector 0.8.2, `ideaton_pgdata` volume |

Docker 미러는 로컬 원본의 정적 복사본이며 자동 동기화되지 않는다. 물리 구성·복원·방화벽 규칙은 [`docs/docker-postgres.md`](../../docs/docker-postgres.md)를 따른다. 추천 파이프라인은 DB 동등성 QA와 읽기 어댑터 연결 전까지 Parquet/DuckDB snapshot을 기본 읽기 저장소로 유지한다.

## 1. 3계층 구조

```text
원천 스냅샷(data/)
  └─ source manifest / 인코딩 / 원본 컬럼 보존
       ↓ 정규화·감사
정규화 계층(canonical)
  ├─ area_dim             : 상권·상권배후지·행정동 공간 정본
  ├─ industry_dim         : 프로젝트 10개 업종·세부음식 온톨로지
  ├─ metric_quarter       : 분기별 업종·지역 지표
  ├─ background_snapshot  : 업종 없는 지역 배경값
  ├─ anchor_point         : 역·아파트·Kakao POI 관측 지점
  ├─ poi_snapshot         : Kakao Local 관측 POI
  ├─ plan_snapshot        : 도시계획·정비·철도계획 스냅샷
  └─ news_snapshot        : 뉴스 메타데이터 스냅샷
       ↓ 후보 판정
candidate + RAG Evidence
  └─ artifacts/20-method/rag-evidence-schema.json
```

원천 데이터와 API 결과는 추천 실행 중 인터넷에서 다시 호출하지 않는다. API/공식 파일은 별도 이식 작업으로 스냅샷화하고, 추천 파이프라인은 저장된 스냅샷과 manifest만 읽는다.

## 2. 공통 타입

| 필드 | 타입/허용값 | 규칙 |
| --- | --- | --- |
| `spatial_unit_type` | `commercial_area` | `hinterland` | `admin_dong` | `sigungu` | `region` | `point` | 공간 단위를 섞지 않는다. `hinterland`는 상권배후지다. |
| `spatial_unit_code` | string | 선행 0을 보존한다. 숫자로 변환하지 않는다. |
| `spatial_unit_name` | string/null | 명칭만으로 결합하지 않고 코드·공간 crosswalk를 우선한다. |
| `industry_code` | string/null | 기본 분석 대상은 `CS100001`~`CS100010`; 상세 음식명은 온톨로지에서 전개한다. |
| `period` | `YYYYQn` | `YYYYMM` | `YYYYHn` | ISO date/null | 관측기간. `retrieved_at`과 혼동하지 않는다. |
| `period_type` | `quarter` | `month` | `half_year` | `snapshot` | 실제 시계열과 계단식 스냅샷을 구분한다. |
| `value` | number/string/object/null | 결측은 0으로 대체하지 않는다. `null`이면 `missing_reason`을 남긴다. |
| `source_type` | `official_file` | `official_api_snapshot` | `observed_poi_snapshot` | `observed_news_metadata` | `project_generated` | `derived` | 계산·생성값은 관측값처럼 표시하지 않는다. |
| `observed_at` | date/datetime/null | 실제 수집·기준일. 분기 코드보다 최신성 판단에 우선한다. |
| `grain_is_proxy` | boolean | 다른 공간 단위나 권역값을 대리 결합하면 `true`다. |
| `data_quality` | `verified` | `conditional` | `audit_only` | `partial` | `missing` | 값의 신뢰도와 추천 사용 경계를 함께 기록한다. |

## 3. 정규화 테이블

### 3.1 `area_dim` — 공간 정본

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `area_id` | Y | `spatial_unit_type:spatial_unit_code` 내부 안정 키 |
| `spatial_unit_type`, `spatial_unit_code`, `spatial_unit_name` | Y | 상권·상권배후지·행정동과 원천 코드·명칭. 배후지 원천 컬럼은 `상권배후지_구분_코드`라는 레거시 명칭이지만 값은 개별 배후지 코드다. |
| `sigungu_code`, `sigungu_name` | 조건부 | 자치구 연결 |
| `admin_dong_code`, `admin_dong_name` | 조건부 | 행정동 연결 |
| `centroid_x_5181`, `centroid_y_5181`, `area_m2` | 조건부 | 영역 파일의 좌표·면적 |
| `geometry_path`, `crs` | Y(영역) | SHP 경로와 `EPSG:5181` |

현재 공간 정본은 상권 1,650개, 상권배후지 1,071개, 행정동 425개다. 세 공간 단위는 같은 피처로 합산하지 않는다.

### 3.2 `industry_dim` — 업종·음식 온톨로지

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `industry_code`, `industry_name` | Y | 서울시 서비스 업종 코드·명 |
| `food_terms`, `search_keywords`, `license_categories` | 조건부 | 세부 음식·네이버 검색어·인허가 업태 매핑 |
| `boundary_note`, `ontology_version` | Y | 중복 집계와 업종 경계 추적 |

정본은 [`data/ontology/업종_검색키워드.json`](../../data/ontology/업종_검색키워드.json)이다. 세부 음식명을 공식 업종으로 오인하거나 매출·점포 행을 중복 합산하지 않는다.

### 3.3 `metric_quarter` — 분기 업종·지역 지표

권장 논리 키:

```text
(period, spatial_unit_type, spatial_unit_code, industry_code, metric_name)
```

| 필드 | 설명 |
| --- | --- |
| `period` | 원천 `기준_년분기_코드` |
| `spatial_unit_type`, `spatial_unit_code`, `spatial_unit_name` | 원천 공간 단위 |
| `industry_code`, `industry_name` | 업종 차원. 유동인구·변화지표처럼 업종이 없으면 null |
| `metric_name`, `value`, `unit` | 지표명·값·단위. 월·연 단위로 임의 변환하지 않음 |
| `source_path`, `source_column` | 원천 파일과 원천 컬럼 |
| `source_type`, `observed_at`, `data_quality`, `missing_reason` | 출처·최신성·품질·결측 사유 |

핵심 원천 매핑:

| 정규화 지표 | 원천 경로 | 실제 원천 컬럼 예 | 상태·사용 |
| --- | --- | --- | --- |
| 점포 수·개업·폐업·프랜차이즈 | `data/점포/` | `전체_점포_수`, `개업_율`, `폐업_률`, `프랜차이즈_점포_수` | `core`; 2021Q1~2026Q1, 연도 파일 정규화 필요 |
| 추정 매출·건수·시간/요일/고객 구성 | `data/추정매출/` | `당월_매출_금액`, `당월_매출_건수` 및 세부 컬럼 | `core`; 2021Q1~2026Q1. 결측과 0 구분 |
| 유동인구 | `data/길단위인구/` | `총_유동인구_수`, 시간대·연령·성별 컬럼 | `core`; 3개 공간 단위, 실제 분기 시계열 |
| 상주인구 | `data/상주인구/` | `총_상주인구_수` 및 구성 컬럼 | `conditional`; 최신 수준만 사용, 분기 추세 금지 |
| 직장인구 | `data/직장인구/` | `총_직장_인구_수` 및 구성 컬럼 | `conditional`; 최신 수준만 사용, 분기 추세 금지 |
| 상권변화지표 | `data/상권변화지표/` | `상권_변화_지표`, `운영_영업_개월_평균`, `폐업_영업_개월_평균` | `conditional`; 전체 업종 배경 라벨, 업종별 지표 아님 |
| 음식점 인허가 패널 | `data/인허가/음식점_상권분기_패널.csv` | `영업중_수`, `신규개업_수`, `폐업_수`, `개업률`, `폐업률` | `audit_only`; 점포 교차검증·생존분석 전용 |

### 3.4 `background_snapshot` — 업종 없는 배경값

업종 차원이 없는 데이터는 업종 지표와 같은 행에 복제하지 않고 별도 배경 사실로 저장한다.

| 도메인 | 키 | 기간/기준 | 주요 값 | 사용 경계 |
| --- | --- | --- | --- | --- |
| 외국인 생활인구 | `period + admin_dong_code` | 2023Q1~2026Q3, 최신 부분분기 | 장기·단기 외국인, 주야·국적 구성 | 행정동 배경; 상권은 crosswalk 경유 |
| 자치구 고용률 | `half_year + sigungu_code + sex` | 2021H1~2026H1 | `고용률` | 자치구 proxy; 동·상권 직접값 아님 |
| 임대료·공실률 원본 | `quarter + region` | 2021Q1~2025Q4 | 임대료·공실률·수익률 | 권역 배경; 주소별 월세·현재 공실 아님 |
| R-ONE 임대동향 | `quarter + R_ONE_상권 + 상가유형 + 지표` | 2024Q1~2026Q2 | 임대료·임대가격지수 | `join_eligible=yes`만 자동 결합; proxy 유지 |
| 계획·정비사업 | `project_id` 또는 `sigungu + 유형 + 단계` | 2026 snapshot | 사업 유형·추진단계·겹침 면적 | 공식 상태 snapshot; 미래 성공·확정 사건 아님 |

### 3.5 `anchor_point` — 후보를 설명하는 관측 지점

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `anchor_id`, `anchor_type`, `place_name` | Y | 아파트 `단지코드`, 역 `역번호`, Kakao `poi_id`, 생성 지점 `GEN-PT-*` |
| `x_wgs84`, `y_wgs84` | 관측 지점 필수 | 위도·경도 계열 |
| `x_5181`, `y_5181` | 조건부 | 거리·PIP 계산용 변환 좌표 |
| `sigungu_code`, `admin_dong_code` | 조건부 | PIP 또는 원천 연결 결과 |
| `source_path`, `source_type`, `observed_at` | Y | 관측 출처·기준일 |
| `synthetic_anchor`, `coordinate_confidence` | Y | 생성 격자 여부와 좌표 신뢰도 |

원천 매핑:

- 역: `data/도시철도역사/역사정보_서울.csv` — 412행, 역·노선·환승·WGS84/5181 좌표
- 버스: `data/버스정류장/버스정류소_서울.csv` — 반경 문맥용 지점. 노선·배차 간격 없음
- 아파트: `data/공동주택/아파트단지_서울.csv` — 3,396단지, 세대수·좌표·지오코딩 신뢰도
- Kakao POI: `data/카카오POI/` — 잠실역 90행과 잠실동 FD6/CE7 context 343행
- 생성 격자: `output/generated_evidence/` — 실제 상가·매물·주소가 아닌 프로젝트 생성 좌표

역·정류장은 후보 주변 접근성 근거로 쓸 수 있으나 존재만으로 사업 성공을 뜻하지 않는다.

### 3.6 `poi_snapshot` — Kakao Local

현재 원천 CSV의 실제 컬럼은 다음과 같다.

```text
source, source_type, poi_id, place_name, category_name,
category_group_code, category_group_name, phone, road_address_name,
address_name, x_wgs84, y_wgs84, x_5181, y_5181, place_url,
distance_m, search_mode, search_query, search_center_x, search_center_y,
search_radius_m, retrieved_at_utc
```

필수 검증 필드는 `poi_id`, `place_name`, `category_group_code`, 좌표, `search_mode`, 검색 조건, `retrieved_at_utc`다. `complete_requested_queries`는 입력 카테고리·키워드 페이지 순회 완료일 뿐 서울 상가 전체·공실·매물 전체를 뜻하지 않는다.

### 3.7 `plan_snapshot` — 도시계획·정비·철도계획

| 하위 도메인 | 식별·공간 필드 | 주요 필드 | 주의 |
| --- | --- | --- | --- |
| 도시계획사업 상권 겹침 | `상권_코드 + 사업장명 + 사업유형 + 추진단계` | `겹침_면적`, `겹침_상권비율`, `생성일` | 2,585사업의 상권 PIP 파생; 단계는 snapshot |
| 정비사업 조합 | `자치구 + 사업구분 + 진행단계` | `건수` | 좌표 없는 자치구 집계 |
| 도시철도망 계획 | `노선명` 또는 `자치구` | 기점·종점·계획기간·상태 | 미개통·정거장 위치 미확정 |

계획 데이터는 지역 특성의 보조 근거다. 계획이 개통, 사업 완료, 수요 증가 또는 성공 outcome으로 이어진다고 확정하지 않는다.

### 3.8 `news_snapshot` — 뉴스 메타데이터

뉴스 본문은 저장하지 않는다. 공통 최소 필드는 다음과 같다.

```text
source, source_type, news_id, published_date, publisher, title, url,
seoul_scope, sigungu_tags, dong_tags, topic_tags, topic_match,
query_label, search_period, retrieved_at_utc
```

BigKinds에는 `categories`, `location_raw`, `institution_raw`, `keywords`, `feature_terms`가 추가되고, 네이버에는 `result_rank`, `query_metadata_verified`가 추가된다.

- BigKinds: 2,522건, `2024-01-01~2026-09-01` 검색 스냅샷
- Naver News: 100건, 검색어 `서울시 재개발`, `2026-09-02` 수동 snapshot, `display=100`, `sort=date`

뉴스 기사량은 `FC-51-news`의 보조 `evidence/context_notes`에만 넣는다. 공식 사업 상태·실제 수요·공실·성공을 의미하지 않으며 `reasons`, `counter_evidence`, `fit_tier`, 정렬에는 사용하지 않는다.

## 4. 후보·Evidence 출력 계약

상세 필드의 정본은 [`artifacts/20-method/rag-evidence-schema.json`](../20-method/rag-evidence-schema.json)이다.

```text
candidate_id
  ├─ anchor: 관측 지점 또는 synthetic anchor
  ├─ location: 좌표·행정동·호스트 상권·겹침 공간
  ├─ dimension_evidence: 수요·경쟁·비용·접근성·지역변화·관심도 차원
  ├─ evidence[]: 값·단위·출처·기간·원 grain·proxy 여부
  ├─ reasons / counter_evidence
  ├─ missing_features
  └─ data_confidence / fit_tier
```

필수 원칙:

1. `candidate_id`는 성공확률이나 예측 점수가 아니다.
2. `evidence[].source_path`와 `source_period`가 없으면 관측 근거로 출력하지 않는다.
3. 다른 grain의 값을 붙이면 `grain_is_proxy=true`와 `proxy_note`를 함께 기록한다.
4. `source_type=project_generated`이면 `synthetic_anchor=true`, `precision=지점(생성)`, `listing_url=null`, `address_point=null`을 강제한다.
5. API가 없거나 부분 수집이면 `missing_features`에 남기고 빈 값을 0으로 바꾸지 않는다.

## 5. 결합 키·공간 결합

### 5.1 시계열 결합

```text
(기준_년분기_코드, 공간단위 코드, 서비스_업종_코드)
```

업종 없는 데이터는 `서비스_업종_코드=null`인 배경 테이블로 둔다. 결합 전후 행 수·키 중복률·결측률을 manifest 또는 QA 결과에 기록한다.

### 5.2 공간 결합

- 영역 정본: `data/영역/` SHP/CSV, `EPSG:5181`
- 지점 → 상권/배후지/행정동: PIP 또는 명시된 거리 규칙
- 상권 → R-ONE: `output/crosswalks/crosswalk_rone_trdar.csv`; `join_eligible=yes`만 자동 결합
- 행정동 → 상권: 문자열 조인이 아니라 영역·코드 crosswalk 사용
- Kakao rect/grid: 대상 경계 내부 좌표만 보존
- 후보 반경: 역·버스·아파트·POI 거리 계산은 `EPSG:5181`

many-to-many가 발생하면 자동 merge를 중단하고 crosswalk 또는 공간 중첩 규칙을 명시한다.

## 6. 최신성·품질

| 데이터군 | 기간/기준 | 시간 해석 |
| --- | --- | --- |
| 점포·매출·유동·상권변화 | 2021Q1~2026Q1 | 실제 분기 시계열 |
| 외국인 생활인구·인허가 패널 | 2023Q1~2026Q3 | 최신 부분분기 표시 |
| 상주·직장인구 | 표기상 2021Q1~2026Q1 | 계단식 갱신; 수준만 사용 |
| 버스·역·아파트 | 2026 snapshot | 존재·규모·접근성 문맥; 분기 모멘텀 금지 |
| 임대료·공실률 | 권역 2021Q1~2025Q4, R-ONE 2024Q1~2026Q2 | 권역/조사상권 proxy |
| 검색·뉴스 | 월계열 또는 수집일 snapshot | 최신 관심도·기사량 context; 계절 보정·단일 급등 가점 금지 |

실행 시 요청 분기와 각 데이터의 관측 종료기간을 비교한다. 최신 행의 분기 코드보다 실제 수집일·스냅샷 상태를 우선한다.

## 7. 현재 표현할 수 없는 것

- 개별 상가의 임대 가능 호실, 보증금, 월세, 전용면적, 권리금, 주차, 현재 공실
- 실제 SNS 게시물 전수·도달량·감성·검색 의도
- 특정 후보의 미래 성공·수익·손익·생존확률 label
- 지하철 출구별 좌표·유동·배차 간격 및 계획 노선의 확정 정거장 위치
- 소형 빌라·연립을 포함한 전체 주거 세대
- 상권배후지 버전의 상권변화지표

미확보 필드는 임의 생성하지 않고 `missing_features` 또는 `unsupported`로 반환한다.

## 8. Claude Code 재개 시 시작점

1. 이 문서와 [`data-schema.json`](data-schema.json)을 먼저 읽는다.
2. 데이터 출처·API 상태는 [`artifacts/handoff_data_sources.md`](../handoff_data_sources.md)를 읽는다.
3. 추천 입력·후보·근거 계약은 [`../20-method/rag-evidence-schema.json`](../20-method/rag-evidence-schema.json)과 `.claude/context/data-catalog-contract.md`를 읽는다.
4. 실제 실행은 `scripts/recommendation_pipeline.py`를 사용하며, 추천 중 API를 호출하지 않는다.
5. 데이터 변경 후 원천 manifest, `data-inventory.md`, `data-usage-classification.md`의 기간·커버리지·사용 분류를 함께 갱신한다.
