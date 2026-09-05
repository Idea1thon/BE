# 서울 창업 입지 추천 시스템 명세

- 최종 갱신: 2026-09-03 (와이어프레임 반영: LLM 입력 해석·분석 계획 + 결정론적 계약 검증·후보 엔진 + Evidence 기반 설명; QA 라운드 4 및 기존 MVP 유지)
- 구성 산출물: `00-input.md`, `10-analysis/*`, `20-method/*`, `30-review/recommendation-quality-review.md`
- 이 문서는 통합 인덱스다. 각 항목의 정본은 링크된 산출물이다.

## 상태

| 영역 | 상태 |
| --- | --- |
| 하네스·데이터 사용·지역 특성·후보 명세·RAG 스키마 정합성 | 사용 가능 (2026-09-02 QA 라운드 4 통과 — 지점 후보 재설계 4문서 일관, 스키마·자동 결함 검사 통과. Critical 0) |
| LLM 입력·분석 계획 경계 | 설계 채택·구현 전 검토 필요 — LLM은 자유 텍스트 해석·분석 계획·설명만 제안하고, 계약 검증·후보·등급·인과 주장 게이트는 코드가 담당. 상세: `artifacts/adr/ADR-002-llm-assisted-recommendation-orchestration.md` |
| 지역 특성 데이터 커버리지 | FC 행 24개 — **active 11 / partial 13**, 개념 슬롯 inactive 2 (외국인·인허가·고용률·정비사업·검색트렌드·R-ONE 임대동향·도시철도역사·버스정류소·**공동주택(K-apt)** 이식). 카카오 POI는 **FC 피처가 아닌** 선택적 seed·지점 반경 상세 관측 |
| 실제 후보 생성 파이프라인 | **MVP 통과·운영 전 검증 필요.** `scripts/recommendation_pipeline.py`가 지점 seed·grain fallback·entry_health·R-ONE `join_eligible=yes`·FC-42 현재 관심도 context·RAG schema 검증을 공통화했다. 잠실·연남·역삼 3개 입력 및 잠실동 POI context 연결 실행이 schema 0 오류다. 후보 단위는 지점(아파트·역, 선택적 카카오 POI)이며, POI context는 잠실동 10개 업종 반복 QA에서 후보·등급·근거·FC-11 라벨 불변을 검증했다. FC-42도 `fit_tier`·정렬·`reasons`·`counter_evidence`에는 미반영한다. Critical 0 / High 0. 잔여는 R-ONE review 지도 QA·host 동 불일치·값 수준·greenfield·DTO |
| 출력 위치 정밀도 | **`precision=지점`** (후보 = 아파트 단지·역 좌표, 2026-09-02). 앵커명 "인근" + 좌표 + 참조 상권. 출구 좌표·상가 호실·`매물주소`는 미확보 |
| 공모전 제출·배포 | 사람 승인 필요 |

## 제품 흐름

1. 사용자가 시도·시군구·(선택)행정동을 고른다.
2. 10개 업종(CS100001~CS100010) 중 하나 + 특별조건 텍스트를 입력한다. → `input-and-condition-contract.md`
3. LLM이 자유 텍스트에서 업종·조건·분석 계획 초안을 만들고, 계약 검증기가 애매·충돌·미지원 조건을 확정한다. 면적·주차·상세 월세는 `unsupported_conditions`.
4. 정규화 feature table을 빌드한다. → `candidate-selection-spec.md` 0절 (결합 규칙 7개). 빌드 실패 시 중단.
5. **선택 경계(+300m) 안의 지점 seed(아파트 단지·역·검증된 POI)를 후보로 만든다.** 80m 병합. 업종 점포 0도 `greenfield`로 유지. → `candidate-selection-spec.md` §1 (2026-09-02 재설계 — 상권 폴리곤 아님)
6. 지점별로 지표를 계산한다: **FC-07 역·버스·FC-08 아파트 = 지점 반경 500m 직접**, FC-01·11·30~32 = 포함 상권 → 없으면 행정동 배경값(`grain_is_proxy`). → `regional-characteristics-profile.md` §2
7. 사용자 업종·조건과 결합해 근거 차원별로 판정하고 `추천`/`조건부 검토`/`주의`로 분류한다. → `candidate-selection-spec.md` 3~5절
8. 후보 위치를 **시>구>동>앵커명 "인근">좌표>참조 상권(host)**으로 출력한다(`location`, `precision=지점`). → `candidate-selection-spec.md` §1-3
9. 분석 결과와 Evidence를 검증한 뒤 LLM/RAG가 이유·반대 근거·누락·최신성을 설명한다. → `rag-evidence-schema.json`, `ADR-002`
10. 검증된 매물 데이터가 있을 때만 `address_point`·`listing_url`을 채운다.

## 현재 MVP 실행 경로 (2026-09-02, GPT/Codex)

```bash
.venv/bin/python3 scripts/recommendation_pipeline.py \
  --sido 서울특별시 --sigungu 송파구 --dong 잠실동 \
  --industry-code CS100010 \
  --special-condition-text '월세 300만원 이하, 20평 이상, 주차 가능' \
  --include-poi --out output/recommendation_runs/jamsil-coffee-mvp --limit 20
```

`request.json`, `candidates.json`, `coverage-summary.json`, `run-manifest.json`, `run-notes.md`를 생성한다. 매물·공실·성공 outcome이 없는 조건은 `unsupported_conditions`/`missing_features`로 보존하며, `fit_index=null`, `score_is_predictive=false`를 유지한다. 구현·실행 주체는 **GPT(Codex)**다.

## LLM 입력 파이프라인 시도 브랜치 (2026-09-03)

브랜치 `codex/llm-input-analysis-pipeline`에서 `scripts/recommendation_pipeline.py`를 다음 경로로 확장했다.

```text
지역 선택 + 자연어 입력
→ LLM 입력 해석·분석 계획(설정이 없으면 결정론적 최소 파서)
→ 결정론적 계약 검증
→ 읽기 전용 데이터 분석
→ 후보·Evidence 생성·스키마 검증
→ LLM 설명 카드(실패 시 템플릿)
```

기존 `--industry-code`·`--special-condition-text` 호출은 호환되며, 와이어프레임형 호출은 `--user-input`을 사용한다. 출력에는 `input_interpretation`, `analysis_plan`, `explanations.json`, `explanation_mode`, `degraded`를 포함한다. 현재 기본 환경에는 LLM 자격증명이 없어 오프라인 폴백으로 실행하며, hosted API 연결은 `LLM_API_URL`·`LLM_API_KEY`·`LLM_MODEL` 설정 후 `--llm-mode auto`에서 활성화된다.

## 데이터 정합성 (2026-09-01 확정)

`data-integrity-check.md` 정본. 결합 파이프라인 필수 규칙:

1. 점포·추정매출 연도 폴더는 20251~20254를 중복 수록 → 분기-파일 1:1 배정, 2026 파일은 20261만 (H1)
2. 위치 기반 읽기 → rename(영문헤더·건수컬럼·점포수 스키마) → grain 판정 → 키 부여 (H2·H3·M1)
3. grain은 컬럼명 아닌 경로+코드 소속으로 판정 (H4)
4. 파일별 인코딩 감지 (M3)
5. 커버리지 부족(추정매출 73·상주 13·직장 7 상권)은 null 유지, 0 대체 금지 (M2·L1)
6. 임대료는 별도 로더 2종: `매장용빌딩...csv`(권역 grain, 2행 헤더 — 현재 FC-21 운영 소스) + `R-ONE_임대동향_분기.csv`(서울전체·권역4·R-ONE 상권72, 2024Q1~, FC-20). R-ONE API에는 2024Q3~ 공실률 표도 있으나 CSV 이식·정의 비교 전이다. R-ONE↔상권분석 1차 crosswalk는 생성됐고, `join_eligible=yes`만 자동 결합 후보로 사용한다(M4).
7. 상권변화지표 배후지 요청 시 상권값 대리 + grain 표시

검증(정상): 단일파일 시계열 키 중복·null 0, 영역 코드 중복 0, 계단식(상주·직장) vs 시계열(유동·매출·점포·변화지표) 재확인, 상권 커버리지 영역과 100%(점포·유동·변화지표).

## 지역 특성 프로파일

`regional-characteristics-profile.md` 정본. FC feature 24행 (active 11 / partial 13, 개념 슬롯 inactive 2).

- **사용자 정의 매핑**(profile §8): 지역 특성 8항목 + "좋은 입지 = 현재 선호/수요 + 미래가치" → FC feature. 특성(중립 서술)과 판정을 분리, 프로파일은 서술만.
- **후보가 지점이므로**(§2): FC-07 역·버스·FC-08 아파트는 지점 반경 500m 직접 계산(고유값). 나머지는 포함 상권 → 없으면 행정동 배경값(`grain_is_proxy`).
- FC-01 = 유동밀도(유동/상권면적) 분위, 총량은 규모 등급만(F19). FC-06a = 장기 외국인/상주인구, FC-06b = 단기 외국인/유동 일평균 — 둘 다 "근사·신호"(방법론 상이, F22).
- `entry_health_v1`(FC-10): **전 업종 통합 지역(상권/행정동) 배경 진입 환경 등급** `{양호/보통/주의/경계}`. 산식 `0.25·폐업률분위 + 0.25·(100−개업률분위) + 0.25·(100−점포증감률분위) + 0.25·라벨리스크·100`, 컷 = 20261 서울 사분위 동결(상권 41/51/62·행정동 42/52/61). 확정·승인 2026-09-02(`entry-health-v1-cut-design.md`). `score_is_predictive=false`, 판정에선 **반대근거 1항목**(게이트 아님). 업종 신호는 FC-11·12·30.

### 2026-09-01~02 이식 10건 (스크립트·경계)

| feature | 데이터 | grain | 경계 |
| --- | --- | --- | --- |
| FC-06a·06b 외국인 거주·방문 | `data/외국인생활인구/` + `상주인구`·`길단위인구` 행정동 | 행정동 | 20263 부분분기. **FC-06a = 장기/상주인구, FC-06b = 단기/유동일평균** — 생활인구÷타 방법론이라 "근사·신호"(F22). 정밀 비율은 내국인 생활인구 |
| 식품 인허가 패널 (FC-12 교차검증·생존분석) | `data/인허가/` (15045016·15006730, 684k업소) | 상권(PIP, 5174→5181) | **audit-only** — 분기폐업률 점포데이터 대체 금지(r=0.11). 점포수 r=0.92 |
| FC-53 자치구 고용률 | `data/고용률/` (KOSIS 지역별고용조사) | 자치구·반기 → 상권 대리 | 반기 단위, 상권 고유값 아님 |
| FC-51·52 정비·개발사업 | `data/도시계획사업/` (UQ120 shp + 정비사업 정보몽땅) | 상권(폴리곤 겹침) | **스냅샷** — 추진단계, 사건일 시계열 아님. "예정" 단정 금지 |
| FC-41·42 검색 관심도 | `data/네이버트렌드/` (네이버 데이터랩 NCP HUB) + `data/ontology/업종_검색키워드.json` | 자치구·행정동통용지명·업종(월) | 최신월·최근 3개월 **원계열** 관심도와 계절 국면을 context로 표시. `robust_slope12`는 분석용이며 후보 가점·등급·정렬에 사용하지 않음. 진행중 월 `is_partial_latest` |
| FC-51-news 시설·개발 뉴스 | `data/뉴스/` (빅카인즈 export + 네이버 뉴스 수동 검색 정규화 JSONL·manifest) | 자치구·행정동명 매칭 | 빅카인즈 2024-01~2026-09 snapshot + 네이버 2026-09-02 현재 검색 snapshot. 분석제외·URL 중복 제거 후 기사량만 `evidence[]`·`context_notes`에 표시. 네이버는 쿼리당 현재 상위 결과만 사용하고 자동화·예약 갱신하지 않음. 공식 사업 상태·성공 outcome·등급·정렬을 대체하지 않음 |
| 업종 온톨로지 확장 | `data/ontology/업종_검색키워드.json` (reference) | — | `ingest_food_license.py`·`ingest_naver_trend.py` 양쪽 의존 → 변경 시 둘 다 재생성 |
| FC-20 R-ONE 임대동향 | `data/임대료/R-ONE_임대동향_분기.csv` (R-ONE 부동산통계, 5파일) + `scripts/rone_api.py` + `output/crosswalks/crosswalk_rone_trdar.csv` | 서울전체·권역4·R-ONE 상권72·분기 | 소·중대형·집합·오피스 4유형 × 임대료(천원/㎡)·임대가격지수. **R-ONE 상권 ≠ 상권분석 상권** — 1차 명칭 proxy crosswalk 생성(자동 결합 후보 52/72, review 18, 미해결 1, 대상 재사용 1). API에는 2024Q3~ 공실률 표도 확인됐으나 현재 CSV 이식·FC-21 연결은 미완료. 2024Q1 시작 |
| FC-07 대중교통 접근성 + `nearby_anchors` 역 | `data/도시철도역사/{역사정보_서울,상권_역세권,역출입구_요약}.csv` + `data/버스정류장/{버스정류소_서울,상권_버스접근성}.csv` (`ingest_subway_stations.py`, `ingest_bus_stops.py`, `ingest_subway_context.py`) | 역-노선 412 / 버스정류소 11,792 / 상권 1,650 (WGS84→5181, PIP + 폴리곤-점 거리) | **개통·계단식 스냅샷**. 역만·정류소만(버스 노선·배차·출구 좌표 없음) → 출구번호·호실 생성 금지(F16). 버스 250m 정류소 1,649/1,650 상권 → 수·유형 비교(F17). **지점 반경 500m 직접 계산(FC-07 고유값)** |
| FC-52 계획 도시철도 | `data/도시철도역사/도시철도망계획_{노선,자치구}.csv` (관보 제19878호 2020-11-17, `ingest_subway_context.py`) | 11 노선 / 자치구 19 (자치구 grain) | **2020년 계획·정거장 위치·개통 미확정**. "예정역"·"확정" 금지, 운행개선(급행·직결) 제외. GTX·동북선 등 별도 계획 미포함 |
| FC-08 배후 주거단지 + `nearby_anchors` 아파트 | `data/공동주택/{아파트단지_서울,상권_아파트접근성}.csv` + `_geocode_cache.json` (`ingest_apartment_complex.py`) | 단지 3,396 / 상권 1,650 (K-apt 15057332 + 면적 15073269 + VWorld Search + GIS 15083092 5186→5181) | **K-apt 주1회 스냅샷**, 의무관리 위주(소형 빌라·연립 누락). 지오코딩 **89.7%**만(미매칭 349 제외), `geocode_신뢰도` 무시 금지(F18). footprint 근사(정밀 경계 아님). **지점 반경 500m 세대수 직접 계산(FC-08 고유값)**, 아파트 지점은 후보 seed |

- 잔여 결측(inactive/슬롯): 랜드마크 단일시설 사건, 개별 매물, 내국인 생활인구(외국인 비율 분모), 출구 좌표·정밀 아파트 경계, 소형 빌라·연립, 버스 노선·배차, GTX·동북선 등 별도 계획, SNS 버즈. 뉴스는 보도량 보조 snapshot만 이식되었고 사업 단위 교차검증은 잔여. 뉴스 자동화·예약 갱신은 하지 않는다.

## 운영 데이터 allowlist

- 기본: 영역(+shp), 점포, 추정매출, 길단위인구
- 조건부: 상권변화지표 라벨, 상주인구·직장인구 수준, 공실률(권역), R-ONE 임대동향(서울·권역·R-ONE 상권72, 분기), 겹침 크로스워크, 외국인생활인구(장기·단기), 자치구 고용률(반기), 도시계획사업(상권 겹침), 네이버 검색 트렌드(월), 빅카인즈·네이버 뉴스 보도량 snapshot(자치구·행정동 보조), 도시철도역사(역사정보·상권 역세권·출입구 요약), 버스정류소(정류소·상권 버스접근성), 도시철도망계획(자치구), 공동주택(아파트단지·상권 아파트접근성)
- audit-only: 식품 인허가 패널(점포수 교차검증·생존분석·greenfield)
- reference: `data/ontology/업종_검색키워드.json` (업태 매핑·검색키워드)
- 이식 대기: 랜드마크 단일시설, 내국인 생활인구, 지하철 출구 좌표, 소형 빌라·연립, 개별 매물, SNS 버즈, 뉴스 사업 단위 교차검증
- 제외 유지: 투자수익률 컬럼, 계단식 분기 증감, 상권변화 연속 gap 주력값, 기존 Top-K 결과
- 정본: `data-usage-classification.md`, `data-catalog-contract.md`

## API 키 (`.env`, gitignore)

| 키 | 용도 | 상태 |
| --- | --- | --- |
| `VWORLD_API_KEY` | 지오코딩 양방향 (`location.address_point`) `scripts/geocode.py` | 발급·실호출 검증 |
| `NAVER_CLIENT_ID`/`SECRET` | 데이터랩 검색 트렌드 (FC-41·42) `scripts/naver_datalab.py` | 발급·실호출 검증. NCP HUB 이관 대응. 무료 30k/월 가드(`scripts/_budget.py`, `.api_budget.json`) |
| `DATA_GO_KR_SERVICE_KEY` (단일 키·Encoding) | 공동주택 단지목록 API (15057332, `AptListService4/getSidoAptList4`) — 서울 3,396단지 kaptCode·kaptName. 좌표는 VWorld Search 지오코딩 | **발급·실호출 검증**. `ingest_apartment_complex.py`. URL 에 raw append(urlencode 금지) |
| `KAKAO_REST_API_KEY` | 카카오 로컬 장소 키워드·카테고리 검색 (`scripts/kakao_local.py`, `ingest_kakao_poi.py`, `ingest_kakao_poi_grid.py`) | **발급·실호출 검증(2026-09-02)**. 잠실역 1km seed 샘플 90행과 작은 `rect` 카테고리 호출을 검증했다. 격자 수집기는 경계 PIP·ID 중복 제거·페이지 포화 분할·커버리지 manifest를 제공한다. `KAKAO_LOCAL_*_LIMIT`의 영속 가드: 기본 일 90,000·월 2,700,000·실행당 500, 공식 무료 상한 일 100,000·월 3,000,000을 넘는 설정은 거부. 관측 맥락용이며, 매물·공실 아님 |
| `RONE_API_KEY` | 한국부동산원 R-ONE 부동산통계정보 API — 임대료·임대가격지수·공실률 갱신 보조 (`scripts/rone_api.py`) | **발급·실호출·응답 스키마 검증 완료(2026-09-02, GPT/Codex)**. 현재 FC-20·21 운영 입력은 CSV 이식본이며 API 자동 갱신·공실률 연결은 미완료. R-ONE 상권↔상권분석 1차 crosswalk는 `output/crosswalks/`에 생성됐고 자동 결합 후보만 사용 |

부분 이식·실호출 검증: 카카오 로컬 REST API(`KAKAO_REST_API_KEY`) — 잠실역 1km POI seed 샘플 90행, 작은 `rect` 카테고리 실호출 성공. 잠실동 FD6/CE7 격자 파일럿은 343행·193 요청, 포화 셀 20개 분할 뒤 `complete_requested_queries`를 통과했다. `--include-poi-context`는 해당 완결 manifest를 후보별 250m/500m RAG 관측으로 연결한다. 이 값은 지점 grain의 중립 맥락이며 FC·`fit_tier`·정렬·성공 outcome에는 미사용이다. 다른 선택 영역 수집은 잔여.
미발급(주석 슬롯): 서울 열린데이터광장, 빅카인즈, KOSIS.

## 핵심 API 객체

| 객체 | 정본 |
| --- | --- |
| 요청 (폼) | `input-and-condition-contract.md` — `sido/sigungu/dong/industry_code/special_condition_text` + 구조화 `conditions` |
| 후보 판정 | `candidate-selection-spec.md` 7절 DTO — `location`, `profile_ref`, `dimension_evidence`, `fit_tier`, `feature_build` |
| RAG Evidence | `rag-evidence-schema.json` — `location`(precision·crosswalk·앵커 슬롯), `feature_build`, evidence 항목 출처·grain·정규화 |

출력 위치(2026-09-02 지점 후보): `location.anchor`(seed 아파트/역) + `place_name`(앵커명 "인근") + `point`(좌표) + `host_commercial_area`(업종·유동 배경값 상권, `relation: 포함|최근접`). `precision=지점`. `nearby_anchors` = 반경 500m 내 다른 역·단지. 출구번호·역/아파트 동·호수·상가 호실·`매물주소` 생성 금지(F11·F16·F18). 지점에 붙은 상권/행정동 배경값은 `grain_is_proxy=true`+`grain_notes`(F20).

evidence `spatial_grain`: 상권(FC-07·FC-08 포함)/배후지/행정동/**자치구**(FC-53·FC-41·FC-52)/**권역**(FC-21 공실률; FC-20은 R-ONE 상권72 또는 권역)/**서울시**(FC-42)/개별매물. 후보(상권)와 다르면 `grain_is_proxy=true`+`proxy_note` 필수(회귀 F14). `update_cadence`: quarterly/monthly/semiannual/stepwise/snapshot. `is_partial_latest`로 진행중 기간 표시.

## 구현 우선순위

1. 지역·업종 폼 + 입력 parser + 법정동↔행정동↔지점 매핑 (`영역-행정동`; 잠실동 → 잠실2·3·7동 사례)
2. 정규화 feature table 빌더 (0절 규칙, `scripts/audit_data_integrity.py` + 이식 스크립트의 rename·재투영 재사용)
3. **지점 seed 수집·병합** (`data/공동주택/아파트단지_서울.csv` + `data/도시철도역사/역사정보_서울.csv` + 선택적 `data/카카오POI/`, 경계+300m, 80m 병합) — `sample_jamsil_coffee.py` 로직 일반화
4. 지점별 grain 부착 (포함 상권 PIP → 없으면 행정동, `host_commercial_area`) + 프로파일 빌더 (active 11 + partial 13)
5. FC-07·08 지점 반경 500m 직접 계산 / 나머지 배경값 + `grain_notes`
6. 근거 차원 판정 + Evidence JSON (grain 프록시·`is_partial_latest`·`source_type`·`evidence[]`·`source_freshness` 준수)
7. `location` 조립 (`anchor`·`place_name`·`host_commercial_area`) + 후보 카드 + RAG 설명
8. ~~entry_health_v1 컷 사람 승인~~ 완료(2026-09-02). `scripts/design_entry_health_cuts.py` 로직을 파이프라인에 편입(전 업종 통합 상권/행정동 진입 환경 등급)
9. `join_eligible=yes` R-ONE crosswalk 연결은 MVP에 반영 완료. 카카오 POI 경계별 `rect` 격자 수집기·PIP·포화 분할·커버리지 QA를 구현했고, 잠실동 FD6/CE7은 `complete_requested_queries`를 통과했다. 해당 완결 스냅샷은 후보별 250m/500m RAG 관측으로 이미 연결했으며, context 유무 비교에서 등급·정렬·긍정/반대 근거가 변하지 않음을 확인했다. 잔여는 다른 선택 영역·업종의 배치와 동일 QA이며, review 지도 QA·host 최근접 규칙도 남아 있다. POI는 매물·공실을 대체하지 않는다
10. 잔여 이식: 내국인 생활인구, 랜드마크 사건, 지하철 출구 좌표, 소형 빌라·연립, 매물, 뉴스 사업 단위 교차검증
11. 신규점포 매출·손익 outcome 확보 후 학습모델 재검토

## 사람 승인 필요

- `fit_index_v1` 가중치·등급 컷, FC-05 유형 경계 (`entry_health_v1`은 2026-09-02 확정)
- 후보 등급 운영 임계값과 UI 문구 `추천` 사용 여부
- 외부 부동산·뉴스·POI 데이터 공급자 약관, 개별 매물 제휴·크롤링
- 합성 SNS 데이터의 데모·제출 표시 방식
- 프로파일 별도 JSON 스키마 분리 여부
- 2026 파일의 중복 2025 분기 물리 삭제 등 `data/` 정리
- 공모전 제출·배포·외부 발송

## 미검증 영역

- 설계 샘플과 공통 MVP 실행이 있음. `output/recommendation_runs/`에서 잠실·연남·역삼 3개 입력을 실행했고 모두 schema 0 오류. QA 라운드 4 통과, 잔여 High(entry_health 컷) 2026-09-02 해소. 도로연장 정규화(FC-01 정밀화), greenfield·대규모 지역/업종 전수 QA는 잔여
- FC-41 행정동 통용지명 매핑 정확도 (본동·제N동·중점 포함 동)
- 상주·직장인구 계단식 as_of, **R-ONE 상권72 ↔ 상권분석 상권 crosswalk의 지도 검증**(`output/crosswalks/`; 자동 결합 후보 52/72, 운영 MVP는 eligible만 결합)
- 값 수준 이상치 (음수 매출, 0 vs 결측, 개·폐업률 범위)
- 영역 좌표계 실제 코드 확정 (인허가·UQ120 = EPSG:5174 확인됨)
- 후보 단위: 지점(아파트·역) 재설계 완료. 카카오 POI는 잠실역 1km seed 샘플과 잠실동 FD6/CE7 실제 격자 수집·250m/500m RAG 관측 연결까지 검증했다. 다른 선택 경계·카테고리의 수집 완결성, POI seed 우선순위, 지점 반경 임계값(500m) 튜닝은 미검증
- host 상권 최근접 배정(M-S3): 상권 폴리곤 sparse 지역에서 옆 동 상권에 붙는 경우 규칙 미확정
- FC-07 버스·FC-08 아파트: 250m 내 대상이 대부분 상권에 존재(버스 1,649/1,650, 아파트 1,461/1,650) → 수·세대수 규모의 분포·분위 유효성 확인 필요
- FC-08: geocode 미매칭 349개(신축·SH임대·도시형생활주택)와 K-apt 미포함 소형 주거의 영향 범위
- FC-52 계획 도시철도: 2020년 계획의 현재(2026) 진행 상태 미반영(자치구 신호로만)
- 잔여 결측: 랜드마크 단일시설, 내국인 생활인구, SNS 버즈, 뉴스 사업 단위 교차검증, 지하철 출구 좌표, 버스 노선·배차. 뉴스 자동화·예약 갱신은 운영 범위에 포함하지 않음
- 신규점포 실제 매출·손익 outcome (개·폐점일·생존은 인허가로 확보)
- `entry_health_v1` enriched(공실률) 변형 — R-ONE 공실률 CSV 이식·정의 비교 및 `join_eligible` crosswalk 연결 후. 가중치 사용자 피드백 재보정 시 `v2`. 실제 웹 백엔드 DTO ↔ 본 계약

## 다음 재실행 진입점

- ~~후보 생성 파이프라인 코드화~~ — 완료(`scripts/recommendation_pipeline.py`, 2026-09-02 GPT/Codex). 잠실·연남·역삼 실행·schema 검증 완료
- R-ONE `review` 대상 지도 QA 및 host 최근접 동 불일치 규칙 확정(FC-20 비용 차원은 MVP에서 eligible만 자동 결합; `review`인 잠실/송파 복합권역은 proxy fallback)
- 네이버 트렌드 배치 경계 정규화 재검토(FC-41 송파구 2026-07+ 값 급락)
- 상세 맥락 확장: 잠실동 FD6/CE7은 `--include-poi-context`로 후보 주변 250m·500m 중립 관측 evidence까지 완료했다. 다음 선택 경계·카테고리는 `scripts/ingest_kakao_poi_grid.py` 수집 → `complete_requested_queries` 확인 → 동일 비교 QA 순으로 추가한다. `context/`는 seed와 분리한다. 지하철 출구 좌표, 검증된 매물
- 잔여 슬롯: 내국인 생활인구(FC-06 비율 완성), 뉴스 사업 단위 교차검증, 랜드마크 사건, R-ONE crosswalk review·공실률 연결(FC-20/21), GTX·동북선 등 별도 계획(FC-52 보강)
