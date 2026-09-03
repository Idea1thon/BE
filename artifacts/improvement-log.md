# 개선 기록

## 2026-09-03 LLM 입력 해석·분석 계획 구조 채택

- 실행한 요청: 와이어프레임처럼 지역은 선택형으로 지정하고 조건은 자연어로 입력하므로 LLM이 사용자 입력부터 처리하도록 변경
- 실제 결과: `artifacts/adr/ADR-002-llm-assisted-recommendation-orchestration.md` 신설. 입력 계약·RAG 계약·웹서비스 아키텍처를 LLM 입력 해석·분석 계획 → 결정론적 계약 검증·후보 엔진 → Evidence → LLM 설명 카드 흐름으로 갱신하고 `scripts/llm_runtime.py`, `scripts/llm_input_planner.py`, `scripts/llm_explanation.py`를 `recommendation_pipeline.py`에 연결
- 유지한 경계: 선택 지역·하드 조건·후보·등급·정렬·grain·출처·인과 주장 검증은 코드가 최종 권한을 가짐. LLM confidence나 관측 상관관계만으로 조건·인과관계를 확정하지 않음
- 폴백: 입력 해석 실패 시 최소 파서·확인 질문, 설명 생성 실패 시 Evidence 기반 템플릿. 호스티드 LLM 공급자·약관·데이터 반출은 사람 승인 필요
- 검증: `py_compile` PASS. 자연어 입력(업종 코드 생략) + `--source files --llm-mode offline` 실행에서 후보 5개·Evidence schema 오류 0·입력 해석/템플릿 설명 출력. 명시적 업종 기존 호출도 후보 3개·schema 오류 0. 모호한 업종은 확인 요청으로 중단. `validate_harness.py` → PASS
- 다음: `InputInterpretation`/`AnalysisPlan` 스키마 확정 → read-only 분석 도구 인터페이스 → LLM 어댑터 → 입력 해석 회귀 테스트

## 2026-08-31 초기 구성

- 실행한 요청: 승인된 청사진에 따라 입지 추천 하네스를 구성하고 불필요 데이터를 분류
- 기대한 결과: 반복 가능한 컨텍스트·역할·작업법·오케스트레이터·검증·산출물 지도
- 실제 결과: 증거 중심 후보 선정 기준으로 전체 하네스와 초기 분석 산출물 생성
- 잘된 점: 기존 Top-K의 역할을 기준선으로 제한하고 폼 입력·RAG 역할 경계를 고정
- 막힌 점: 표준 `.Codex`가 읽기 전용 `.codex`와 충돌하여 기존 `.claude` 경로 사용
- 다음 버전에서 바꿀 규칙: 실제 후보 실행 후 임계값, 데이터 커버리지, RAG Evidence 필드를 실측 결과에 맞게 보정

## 2026-08-31 운영 제외 데이터 정리

- 실행한 요청: 운영 추천에서 제외한 데이터 삭제와 디렉터리 용량 절감
- 삭제 결과: 약 360MB 절감
- 삭제 대상: 중복 ZIP, 아파트 원자료, 로컬브랜드 원자료·파생물, 기존 Top-K 결과·그래프, 읽기 가능한 `.DS_Store`
- 유지 결정: 44KB 임대료 혼합 파일은 임대료·공실률이 필요하므로 보존하고 투자수익률만 파이프라인에서 제외
- 복구성: 아파트는 Git 추적, 나머지 주요 미추적 산출물은 영구 삭제

## 2026-09-01 데이터 정합성 검사 (T02 부분 재실행)

- 실행한 요청: "현재 갖고 있는 데이터들 먼저 정합성 검사"
- 기대한 결과: `data/` 실재 CSV의 스키마·grain·기간·키·결합률·누락 실측 및 결합 가능성 판정
- 실제 결과: `data-integrity-check.md` 신규(High 4·Medium 4·Low 3·정상 6), `scripts/audit_data_integrity.py` + `output/data_integrity_report.json`, `data-inventory.md`·`data-usage-classification.md` 실측 반영
- 잘된 점: 파일명이 아닌 실제 헤더·값으로 검사해 연도파일 병합 중복(점포-상권 125만 중복키)과 2026 스키마 변경, 2025 영문 헤더, 배후지 grain 라벨 오류를 조기 발견. 계단식(상주·직장인구) vs 시계열(길단위인구) 계약 가정도 실측 재확인
- 막힌 점: git 워킹트리에 사용자의 대규모 데이터 재구성(연도 폴더화)이 있어 `git status`만으로는 데이터 손실로 오인할 수 있었음 — 실제로는 손실 없음, 재구성
- 다음 버전에서 바꿀 규칙: `candidate-selection-spec.md`에 결합 규칙 7개(분기-파일 1:1 배정, 위치 기반 로드→rename→grain 판정 순서, 커버리지 부족 null 유지)를 명시하고, 결함 주입 회귀에 "연도파일 concat 시 중복 미제거" 케이스 추가 검토

## 2026-09-01 후보 선정 명세에 결합 규칙 반영 (T04 부분 재실행)

- 실행한 요청: "candidate-selection-spec에 결합 규칙 반영"
- 실제 결과: `candidate-selection-spec.md`에 0절(데이터 준비 결합 규칙 7개 + 최신분기 재확인) 신설, 2절 하드조건에 feature table 빌드 게이트 추가, 3절 근거 차원 표에 결합 주의 열 추가, 7절 DTO에 `spatial_grain`·`feature_build` 필드, 8절 실패조건 확장, 9절 버린 대안 신설
- 잘된 점: 원천 CSV 직접 조인을 금지하고 정규화 feature table을 강제하는 형태로 명세화 → H1~H4가 런타임에 조용히 통과하지 못하고 빌드 실패로 드러남
- 남은 stale: `rag-evidence-schema.json`(grain·병합 근거 필드), `recommendation-quality-review.md`(정합성 반영 재검토), `final/recommendation-system-spec.md`
- 다음 버전에서 바꿀 규칙: 결함 주입 회귀 세트(`artifacts/evals/regression/fault-cases.md`)에 F09 "연도파일 concat 중복 미제거 → feature table 빌드 통과" = High 반려 케이스 추가

## 2026-09-01 RAG Evidence 스키마에 grain·병합 근거 반영 (T06 부분 재실행)

- 실행한 요청: "rag-evidence-schema에 grain·병합 근거 필드 반영"
- 실제 결과: 최상위 `spatial_grain`(필수), `feature_build` 객체 신설(`build_passed`=const true, `merge_key_dup_rate`=const 0, `quarters_used`, `normalizations_applied`, `grain_resolution`{method,conflicts}, `coverage`, `sources`), `source_freshness`를 데이터셋별 구조(관측종료분기·격차·갱신주기)로 강화, `missing_features`를 {feature,reason} 객체 배열로, evidence 항목에 `source_type`(observed/derived/synthetic)·`grain_is_proxy`+`proxy_note`·`quarter_file_source`·`normalization`·`seoul_percentile`·`comparison_scope` enum 추가, null 값·프록시 grain·합성 데이터에 조건부 필수(`allOf`/`if-then`) 적용
- 검증: `json.load` OK, `jsonschema.Draft202012Validator.check_schema` OK, 하네스 구조 검증 PASS
- 잘된 점: H1(중복)·H4(grain 오분류)·M2(커버리지)가 스키마 레벨에서 `const`/`conflicts=0`/`missing_reason` 필수로 강제됨 → RAG 문장이 근거 없이 grain·분기를 섞으면 스키마 검증에서 걸림
- 남은 stale: `recommendation-quality-review.md`, `final/recommendation-system-spec.md`

## 2026-09-01 지역 특성 프로파일 정식화 (사용자 틀 기반)

- 실행한 요청: 사용자가 "지역 특성 / 좋은 입지란?" 틀을 제시 → 조건부 허용으로 정식화, 결측 데이터 이식 가능하게
- 실제 결과: `regional-characteristics-profile.md` 신규 — 특성(중립 서술)과 좋은입지(판정) 분리, 22개 feature를 FC id로 정의(active 11·partial 4·inactive 9), 이식 슬롯 규약(6절), `entry_health_v1` 산식(7절, 충돌① 조건부 허용). `data-catalog-contract.md`에 고용률 행 추가 + 프로파일↔catalog 1:1 대응 명시, `metric-contract.md`에 포인터
- 설계 판단: 사용자 초안의 "공실률+점포+4대분류→하나의 값"은 ADR-001과 충돌하므로, 서수 등급(`entry_health_v1`, `score_is_predictive=false`, 산식 공개, core/enriched 변형)으로 조건부 수용. 계단식 데이터(상주·직장) "우상향" 요구는 데이터 성격상 거부하고 유동인구+폐업률 추세로 대체(FC-50)
- 이식 규약 핵심: inactive feature도 프로파일·RAG·신뢰도에 항상 포함, 소비처는 feature를 id로 참조하고 status만 읽음 → 데이터 확보 시 참조 코드 변경 없이 활성화
- 다음: `candidate-selection-spec.md` 3절을 FC id 참조로 교체, 그 뒤 QA·최종 명세

## 2026-09-01 후보 명세를 지역 특성 프로파일에 연결 (T04 부분 재실행 2)

- 실행한 요청: "candidate-selection-spec 3절을 FC id 참조로 교체"
- 실제 결과: 3절 근거 차원 표를 FC id(FC-01·10·11·30·31·32·50 등) → metric-contract 7차원 매핑으로 교체, 3-1절 status 처리(active 사용 / partial 신뢰도 하향 검토 / inactive는 대체 금지·missing_features·등급 상한), 3-2절 특성(서술)과 판정 경계 명문화, 6절 entry_health를 프로파일 7절 참조로, 7절 DTO에 `profile_ref`·`dimension_evidence`, 8절 inactive fallback
- 잘된 점: 프로파일이 "지표 정본", 후보 명세는 "차원 매핑 + 판정 로직"으로 역할 분리됨. 데이터 이식 시 프로파일만 재빌드하면 후보 명세는 id 참조라 그대로
- 남은 stale: `recommendation-quality-review.md`, `final/recommendation-system-spec.md`

## 2026-09-01 QA 재검토 + 출력 위치 범위 명확화 + 최종 통합 (T07·T08 + 범위)

- 실행한 요청: "진행" (QA → 최종) + 세션 중 사용자: 출력이 "상권 코드"면 입지 추천으로 의미 없음, 시-구-동-정확위치로 나와야 함
- QA 결과: 경계면 High 1건 발견·수정·재검증 — 후보 DTO(`profile_ref`·`dimension_evidence`)가 RAG 스키마 `additionalProperties:false`에 걸림. `jsonschema.Draft202012Validator`로 실증 후 스키마에 optional 추가, 샘플 후보 PASS 확인. Medium/Low 5건 수정, fault F09~F11 추가
- 범위 반영: `rag-evidence-schema.json`에 `location` 블록(place_name·centroid·polygon_ref·overlapping_units·precision + nearby_anchors·address_point 이식 슬롯), `candidate-selection-spec.md` 1-1절, `project-context.md`·`00-input.md`·`data-catalog-contract.md`(POI·역지오코딩 슬롯). 겹침 크로스워크(`output/crosswalks/`, 1,650 상권 전수)가 이미 있어 상권↔동↔배후지 연결은 지금 가능
- 설계 판단: ADR-001과 충돌 아님 — 매물 성공확률 예측이 아니라 "출력을 사람이 읽을 수 있게". 현재 정밀도 `상권`(장소명+좌표+동), `상권+앵커`/`매물주소`는 데이터 이식 슬롯. `precision` 필드로 항상 한계 표기, F11로 회귀 방어
- 최종: `recommendation-system-spec.md`를 통합 인덱스로 재작성. 부분 재실행 묶음(정합성→후보명세→RAG→프로파일→QA→최종+위치) 마감. 전 산출물 current
- 다음 버전에서 바꿀 규칙: 실데이터 샘플 실행 전까지 임계값·좌표계·계단식 as_of는 미검증으로 유지. 위치 정밀도는 POI/매물 데이터 감사가 선행

## 2026-09-01 결측 데이터 확보 출처 조사 (웹)

- 실행한 요청: 필요한 데이터 나열 + 출처를 웹 조사로 확보
- 실제 결과: `data-acquisition-sources.md` 신규. inactive 슬롯·catalog missing 행별로 출처·형식·grain·접근·좌표계·우선순위 정리
- 핵심 발견:
  - FC-06 외국인: 서울 열린데이터광장 OA-14992/14993 (행정동·시계열·무료) — 바로 이식 가능
  - 신규점포 outcome: LOCALDATA(localdata.go.kr) 일반·휴게음식점에 인허가일·폐업일·좌표 있음(무료). 좌표계 EPSG:5174(상권 shp 5181과 다름 → 재투영)
  - 개별 매물: 국가 무료 소스 없음(디스코·밸류맵·부동산플래닛 공식 API 없음) → 제휴/크롤링, 사람 승인 게이트
  - SNS: 네이버 데이터랩 검색어 API + 빅카인즈 뉴스(무료) — 합성 대신 실제 집계 우선
  - 좌표계: 상권 shp≈EPSG:5181, LOCALDATA=5174, 생활인구·지하철=WGS84 → 파이프라인에 재투영 단계
- `data-inventory.md` 추가필요 표를 출처 포함으로 갱신
- 다음: 우선순위 1(외국인·LOCALDATA·지하철 출입구)부터 감사 → 이식

## 2026-09-01 외국인 생활인구 이식 (장기+단기, 첫 슬롯 활성화)

- 실행한 요청: 사용자가 장기체류 + 단기체류 외국인 월별 CSV 전량 + 장기 202410 다운로드 → 확인·정규화·이식
- 입력: `LONG_FOREIGNER_DONG_*` 43개 + `TEMP_FOREIGNER_DONG_*` 43개 (2023-01~2026-07, 누락 없음). 두 세트 스키마 동일(6열, 행정동×일×24시간대). 총생활인구수=중국인+중국외(내국인 미포함)
- 실제 결과:
  - `scripts/ingest_foreign_resident_population.py` — 장기·단기 통합 처리, 파일별 인코딩 감지, 강북구 6개 별칭, 분기·월 집계
  - `data/외국인생활인구/외국인생활인구_행정동_{분기,월}.csv` (6,360 + 18,232행, ~2MB) — 장기_평균·단기_평균·외국인_합·장기야간(거주신호)·단기주간(관광신호) 등. 원천 월별은 `.gitignore`
  - 검증: 20231~20263, **20244 완전**(202410 해소), **20263만 부분**(202607), 영역-행정동 424/425(항동 없음), 장기·단기 **모두 분기 시계열 확정**
- 프로파일 반영: 슬롯 규약대로 FC-06a(거주)·FC-06b(방문) 둘 다 inactive→**partial**. active11/partial6/inactive7. "외국인/전체 비율"의 분모(내국인)는 P3 잔여
- 잘된 점: 슬롯 규약이 두 번째로 작동 — 소비처 FC id 참조라 status flip만. 첫 이식(장기)의 파일명·컬럼을 이번에 재설계(통합)했으나 규약 덕에 파급 통제됨
- 다음: QA F-case 재확인(관광 업종에서 FC-06b 우선, 20263 부분분기 게이트) → 실데이터 샘플

## 2026-09-01 LOCALDATA 출처 정정

- 사용자 지적: LOCALDATA가 공공데이터포털로 편입됨
- 확인(웹): 2026-01-25 행안부가 지방행정 인허가 195종 + 생활편의 14종을 공공데이터포털(data.go.kr)로 통합 개방. `localdata.go.kr`은 2026-04-15 병행 종료 → 현재 data.go.kr 일원화
- 정정: 신규점포 outcome 출처를 `행정안전부_식품_일반음식점`(15045016, 파일)·`_휴게음식점`(15006730), OpenAPI 15154916으로 교체. 전국 213만 행 → 서울(자치단체코드 11xxx) 필터, EPSG:5174→5181 재투영, 업태구분명→10업종 매핑 필요
- `data-acquisition-sources.md`·`data-inventory.md`·`README.md` 갱신

## 2026-09-01 식품 인허가 이식 (신규점포 outcome 부분 확보)

- 실행한 요청: 15045016·15006730(서울 필터본) 다운로드 완료 → 이식·검증
- 입력: `식품_일반음식점_서울특별시.csv`(538k, 162MB) + `식품_휴게음식점_서울특별시.csv`(147k, 47MB), 39열, cp949, 좌표 EPSG:5174
- 처리(`scripts/ingest_food_license.py`, pyproj 신규 설치): 좌표 5174→5181 변환(이동 dy −305m — 변환 안 하면 상권 오배정) → pyshp/shapely STRtree point-in-polygon → 업태 84% 10업종 매핑
- 산출: `data/인허가/음식점_상권분기_패널.csv`(13MB, 커밋 — 상권×업종×분기 영업중·개업·폐업), `음식점_인허가_서울.csv`(94MB, `.gitignore` — 업소 단위, 재생성)
- 검증: 총 685k 업소, 좌표결측 5.7%, 상권 폴리곤 밖 125k(상권분석 데이터가 서울 전역 아님), 패널 1,630/1,650 상권. 20263 부분분기
- **교차검증**: 기존 `data/점포/`와 점포수 **r=0.92**(공간분포 일치), 폐업 점포수 r=0.52, **분기별 폐업률 r=0.11**(대체 금지 — 인허가 폐업일자는 행정 처리일)
- 판정: `audit-only`. 분기별 폐업률 대체 불가, 연/다분기 누적 교차검증 + 개별 개·폐점일 기반 생존분석(20241 개업 4분기 생존 83.4%)·greenfield 판정에 사용
- `metric-contract` 학습모델 재검토: 개·폐점일·생존·greenfield 모집단 **부분 충족**, 매출·손익·비용은 여전히 결측
- 다음: 지하철 출입구(앵커), 내국인 생활인구, 정비사업

## 2026-09-01 API 키 관리 구성

- 사용자가 지오코더 API 키 발급 → VWorld 지오코딩 API(`api.vworld.kr/req/address`, 국토부/국가공간정보센터, 단일 key 양방향: getCoord 주소→좌표, getAddress 좌표→주소)
- `.env.example`(커밋) + `.env`(gitignore) + `scripts/_env.py`(의존성 없는 로더) + `scripts/geocode.py`(VWorld 래퍼, to_coord/to_address, 기본 CRS epsg:5181)
- `.env.example`: 활성 = `VWORLD_API_KEY`. 주석 슬롯 = 공공데이터포털 공통키·서울 열린데이터광장·네이버 데이터랩·빅카인즈·카카오·KOSIS
- 지하철 앵커: 사용자가 받은 파일이 "리프트 위치정보"(83행, 접근성 출입구만)라 앵커 부적합 → 역 수준은 국가철도공단 도시광역철도 역사정보(15013205, 1,073역) 권장
- 2026-09-01 후속: 사용자가 VWorld 키를 `.env.example`(커밋 대상)에 넣음 → `.env`로 이동, `.env.example` 키 비움. 커밋 전이라 노출 없음, 로테이션 불필요. `git grep` 으로 추적 파일에 키 없음 확인
- 2026-09-01 후속: 사용자가 받은 `경제활동인구(시도)`는 경제활동인구 수(천명)·시도 단위 → FC-53(자치구 고용률)에 부적합, 이식 보류. KOSIS 지역별고용조사 시군구 고용률 필요
- 2026-09-01 후속: VWorld 키 `.env` 실호출 검증 성공(양방향). `scripts/geocode.py` 작동
- 2026-09-01 후속: 네이버 데이터랩 애플리케이션 신청 → `.env.example` 활성 슬롯 `NAVER_CLIENT_ID`/`SECRET`, 래퍼 `scripts/naver_datalab.py`(검색어 트렌드, 절대량·지역분해 없음). 사용자가 `.env`에 값 입력 예정
- 2026-09-01 후속: 사용자가 네이버 키 입력 + 무료구간 엄격 제한 요청 → `scripts/_budget.py`(월 호출 카운트, `NAVER_DATALAB_MONTHLY_LIMIT` 기본 28000 < 무료 30000, 실패호출 미카운트, `.api_budget.json` gitignore). `naver_datalab.py` 매 호출 전 `check()` 후 성공 시 `commit()`
- 2026-09-01 후속: 네이버 401 원인 규명 — 2025년 검색/데이터랩 API가 NAVER API HUB(NCP)로 이관. 신규 엔드포인트 `naverapihub.apigw.ntruss.com/search-trend/v1/search` + 헤더 `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY`. `naver_datalab.py`가 신규→레거시 순 시도 + 두 헤더셋 동시 전송하도록 수정 → **실호출 성공**(예산 1/28000, 실패 401 3회는 미카운트 유지). 사용자 키는 처음부터 정상이었음

## 2026-09-01 자치구 고용률 이식 (FC-53)

- 원천: KOSIS 지역별고용조사 `고용률.csv` (서울 25구 × 계/남/여 × 반기 2021H1~2026H1, wide)
- `scripts/ingest_employment_rate.py` → `data/고용률/자치구_고용률_반기.csv` (long 825행, 자치구 코드 25/25 매칭)
- 프로파일 FC-53 inactive→partial (grain=자치구, 반기, 상권엔 자치구값 대리, `grain_is_proxy=true`)
- 이전 `경제활동인구(시도)`는 다른 지표(경제활동인구 수·시도)라 보류 — 이번엔 맞는 표
- active11/partial7/inactive6

## 2026-09-01 도시계획사업·정비사업 이식 (FC-51·52)

- 원천: 서울 도시계획포털 UQ120_도시계획사업 (UPIS_C_UQ120.shp, EPSG:5174 확인 via .prj, dbf **cp949**) + 코드정의표 xlsx / 정비사업 정보몽땅 사업장목록.xls
- `scripts/ingest_urban_projects.py`(pyshp cp949 + shapely.ops.transform 5174→5181 + STRtree 겹침) → `data/도시계획사업/도시계획사업_상권겹침.csv`(2,744행) + `_자치구요약.csv`
- `scripts/ingest_redev_associations.py`(xlrd) → `정비사업조합_목록.csv`(1,153건) + 요약. 좌표 없어 자치구 단위, shp가 공간 정본
- 결과: 2,585 사업 폴리곤(소규모정비 1015·정비사업 739·역세권 311·재정비촉진 293·기타 173·국토부 54), 1,012/1,650 상권에 겹침. 추진단계_구분(초기<조합<인가<착공<완료) 파생
- 프로파일 FC-51·52 inactive→**partial**. active11/**partial9**/inactive4. 잔여: 랜드마크 단일시설(P3), SNS(FC-41·42)
- 부수 확인: UQ120 .prj = Korean_1985_Modified_Korea_Central_Belt = EPSG:5174 → 식품 인허가와 동일 좌표계, 재투영 방식 재확인
- xlrd·openpyxl 설치

## 2026-09-01 네이버 검색 관심도 이식 (FC-41·42) + 업종 온톨로지

- 사용자 요청: FC-41=자치구·행정동, FC-42=10업종. 업종은 광범위 카테고리라 대표 음식 온톨로지 필요
- `data/ontology/업종_검색키워드.json` — 10업종 × 대표 키워드 8~13개(브랜드명 배제). 스크립트는 top_n(기본 5) 사용
- `scripts/ingest_naver_trend.py` — 요청당 대상 4그룹 + 고정 앵커("서울 맛집/카페") 1그룹. DataLab이 요청 내 정규화라 raw_ratio는 요청 간 비교 불가 → **rel_index = 대상/앵커** 만 비교 가능하게 설계
- 산출: 업종(680행)·자치구(1,700행)·행정동(28,900행, 245 통용지명→425 행정동)·_요약. 2021-01~2026-08 월. **74 API호출**(월 예산 28000)
- 행정동은 `strip_dong`으로 숫자·중점 앞 통용지명 추출(잠실2동→잠실). 본동/제N동은 신호 약함(한계 명시)
- 한계: rel_index 상대지수(절대량 아님), **계절성 미보정**(호프 여름 급등으로 6개월 기울기 +13 과대), 온톨로지 키워드 선택 민감
- 프로파일 FC-41·42 inactive→partial. **active11/partial11/inactive2** (남은: 랜드마크 단일시설, 개별 매물)

## 2026-09-01 업종 온톨로지 확장 (사용자 요청)

- 사용자: 업종이 광범위(한식·일식·양식)해서 세부 음식 온톨로지 필요, top-k 아닌 전체 정리
- 웹 조사(위키/나무위키 요리 목록, 배달앱 카테고리, 식품위생법 업태) + `data/인허가` 실제 업태구분명 분포로 `data/ontology/업종_검색키워드.json` 대폭 확장
- 구조: 업종별 `세부음식`(하위유형별 전개, 27~170개)·`검색키워드`(FC-42용 9~25개)·`인허가_업태`(→10업종 매핑)·`배달앱_카테고리`·`경계`(모호사례) + `미매핑_업태`(제외 목록)
- `ingest_food_license.py`가 온톨로지의 `인허가_업태`를 로드하도록 변경(하드코딩 제거). 매핑 84.0→84.5%, 냉면집·탕류·복어·패밀리레스트랑·간이주점·라이브카페·아이스크림 추가
- `ingest_naver_trend.py` 키 이름 `keywords`→`검색키워드`
- catalog에 `reference` 상태 신설. 프로파일 FC-42·data-integrity-check 갱신

## 2026-09-01 QA 라운드 3 + 최종 명세 갱신

- 대상: 이번 세션 이식 6건(외국인·인허가·고용률·정비사업·검색트렌드·온톨로지)의 경계면
- High 2건 발견·수정:
  - RAG 스키마 evidence `spatial_grain` enum에 자치구·권역·서울시 없음 → FC-53·FC-41자치구·FC-20/21 evidence가 스키마 검증 실패. enum + `overlapping_units.sigungu` 추가
  - `update_cadence` enum이 분기 전용(monthly·semiannual·snapshot 없음), `observed_end_quarter` 필드도 → cadence 3종 + `observed_end_period`·`is_partial_latest` 추가
  - `jsonschema`로 고용률(자치구·반기) + 네이버(서울시·월) evidence 샘플 재검증 PASS
- Medium: `feature_build.normalizations_applied` enum에 이식 정규화 7종 추가. `candidate-selection-spec` §3(FC-41·42 추가·미래신호 재작성)·§8(inactive→partial 공통 규칙) 갱신. 프로파일 §8 매핑 갱신
- fault-cases F12(계절성)·F13(인허가 폐업률 대체)·F14(grain 프록시 누락)·F15(스냅샷을 확정 표현) 추가
- 최종 명세: 이식 6건 표(스크립트·grain·경계), API 키 섹션, evidence grain 확장 반영. QA·final `current`
- Critical/High 미해결 0건. 프로파일 active11/partial11/inactive2

## 2026-09-01 미사용 파일 정리 (사용자 승인, Tier 1+2)

- 삭제: `scripts/새파일.py`(0바이트), `__pycache__/` × 3(캐시), `scripts/localbrand_*.py`·`plot_localbrand_*.py` 5개(로컬브랜드 원자료 영구삭제로 실행 불가 — `docs/data_result_3.md §19` 재현 포기)
- 이동: 루트 `scatter_age_food_sales.py`·`scatter_employment_sales.py` → `scripts/` (둘 다 재구성 전 경로 참조로 현재 실행 불가, 위치만 정리)
- 보존(사용자 판단): `*apartment*`(Git 복구 가능)·`rent_*`·`songpa_*`·`gu_*`·`seoul_*`·`scoring_*` 분석 스크립트 + `output/` = 보고서·발표 재현용
- 미삭제: `data/인허가/음식점_인허가_서울.csv` 94MB (재생성 가능하나 사용자가 유지 선택)
- scripts/ .py 62개

## 2026-09-01 Downloads 원천 데이터 정리 (사용자 요청)

- Downloads ↔ 프로젝트 완전중복: 없음(프로젝트 data/는 파생 집계, Downloads는 원천). 다만 `LONG_FOREIGNER_DONG_202301.zip`이 추출된 `.csv`와 내용 동일(해시 일치) → zip 삭제
- 86개 외국인 csv 모두 고유(중복 다운로드 없음)
- `~/Downloads/서울창업입지_원천데이터/` 생성, 하위 폴더로 이동: `외국인생활인구/`(86) · `식품인허가/`(2, 205MB) · `고용률/`(1) · `도시계획사업/`(UQ120+사업장목록) · `_미사용_참고/`(경제활동인구·지하철리프트 = 부적합 판정)
- `scripts/_raw.py` 신설 — 이식 스크립트 원천 루트 계산(`RAW_DATA_DIR` env, 기본 위 경로). `ingest_foreign/food/employment/urban/redev` 5종 `--src` 기본값 변경, 재실행 5종 OK
- `.env.example`에 `RAW_DATA_DIR` 슬롯. handoff에 원천 위치 표

## 2026-09-01 R-ONE 임대동향 이식 + 원천 폴더 Documents 이동 (사용자 요청)

- 원천 폴더 `~/Downloads/서울창업입지_원천데이터/` → `~/Documents/서울창업입지_원천데이터/` 이동. `_raw.py` 기본 경로·`.env.example`·handoff 갱신. 이식 5종 재실행 OK
- 임대동향 5개 csv(한국부동산원 R-ONE) → 원천 폴더 `임대동향/` 하위
- `scripts/ingest_rent_trend.py` 신설 → `data/임대료/R-ONE_임대동향_분기.csv`(3,076행): 4 상가유형 × 임대료/지수 × 서울전체·권역4·**R-ONE 상권72** × 2024Q1~2026Q2
- FC-20(임대료) grain 개선: 권역4 → R-ONE 상권72. M4(임대료 권역 grain 한계) **부분 완화**. 당시 이식 CSV에는 공실률이 없어 기존 `매장용빌딩...csv` 권역 유지(2026-09-02 API 공실률 표는 별도 확인, 연결 미완료)
- **잔여**: R-ONE 상권(광화문·강남대로 등) ↔ 서울 상권분석 1,650 상권 매핑 테이블 (미검증)
- `.gitignore`에 `임대동향*.csv`. catalog·inventory·data-integrity-check·프로파일 FC-20·21·data-acquisition-sources 갱신

## 2026-09-01 도시철도역사 이식 (FC-07 신설 + nearby_anchors 부분 활성)

- 실행한 요청: "전체 도시철도역사 정보도 다운로드 받았음"
- 원천: 국가철도공단 전체 도시철도역사정보(공공데이터포털 15013205) `전체_도시철도역사정보_20260630.xlsx` — 전국 1,099역, WGS84. `~/Documents/서울창업입지_원천데이터/도시철도역사/`
- `scripts/ingest_subway_stations.py` 신설 → 2 산출:
  - `data/도시철도역사/역사정보_서울.csv`(역-노선 412행: 서울 406 + 경계 인접 경기 6)
  - `data/도시철도역사/상권_역세권.csv`(상권 1,650행: 상권내 역 수·최근접 역·거리·환승 인접·인접역 JSON)
- 처리: WGS84→EPSG:5181 재투영, 상권/행정동 point-in-polygon(서울 필터), 폴리곤-점 거리(최근접 역). 노선명 표기 변형 정규화("서울 도시철도 9호선"→"9호선", 경부·경인·경원선→"1호선")
- **FC-07(대중교통 접근성) 신설·active** (G1). `nearby_anchors` 역 앵커 부분 활성 → 최근접 역 ≤500m면 `precision=상권+앵커` 도달. 1,330/1,650 상권이 500m 내 역, 891이 역세권(≤250m)
- 한계: **개통 스냅샷**(개통일·예정역 없음, 시계열 아님), 역만(버스·출구 위치 미포함) → 출구번호·호실·"@@아파트" 생성 금지
- 갱신: `rag-evidence-schema`(nearby_anchors type `역`·`line`·`transfer` 추가, precision 설명), `candidate-selection-spec`(§1-1·§3 현재수요·§7 DTO·§8), 프로파일(FC-07·§6 이식표·요약 active 10/partial 13), `data-catalog`(2행+missing 2행 수정), `data-integrity-check`(이식 감사), `.gitignore`. 회귀 F16 추가
- **잔여**: 개통예정역, 버스 정류장, 출구·아파트 단지 경계(POI), 역지오코딩(매물 데이터 대기)

## 2026-09-01 버스정류소 + 도시철도 보조 이식 (FC-07 확장 + FC-52 계획노선)

- 실행한 요청: "버스. 지하철 데이터는 모두 다운로드했음 확인할 것" + 도시철도망 구축계획 관보 이미지 제공 (아파트 단지는 다음 작업으로 분리)
- `scripts/ingest_bus_stops.py` → `data/버스정류장/{버스정류소_서울,상권_버스접근성}.csv`
  - 서울시 버스정류소 위치정보(OA-15067) 2026-08 스냅샷 11,239 + 국토부 15067528 경계 인접 519. WGS84→5181. 중복 124 제거, 가상정류장·선착장 8 제외
  - 22개 월 스냅샷 안정성 확인: 9개월 +37/−133 → **계단식**. 과거분 `_과거스냅샷/` 보관
  - **1,649/1,650 상권**이 250m 내 정류소 → 유무 이분 금지, 수·마을버스·간선(중앙차로) 수로 비교
- `scripts/ingest_subway_context.py` → `data/도시철도역사/{역출입구_요약,도시철도망계획_노선,도시철도망계획_자치구}.csv`
  - tnSubwayEntrc X/Y는 **연계 버스정류장 좌표**(출입구 아님) — 역별 출입구 수·연계 정류장 수·주변건물만. 623역 중 380 매칭
  - 관보 제19878호(제2차 서울 도시철도망 구축계획) 11개 노선 수기 테이블. **자치구 grain·계획(정거장 미확정)**. 운행개선(4호선 급행·5호선 직결)은 `노선유형`으로 분리
- 프로파일: **FC-07 철도+버스 확장**(active 유지), **FC-52에 자치구 계획 도시철도 신호 추가**(partial). 카운트 불변(active 10/partial 13)
- 갱신: `data-catalog`(6행 + 시간규칙 스냅샷 목록), `data-integrity-check`(이식 감사), `candidate-selection-spec` §3·§8, `rag-evidence-schema`(normalizations 3개, spatial_grain 설명), `data-inventory`, `data-acquisition-sources`, `.gitignore`. 회귀 **F16 확장 + F17 신설**(버스 유무 이분 반려)
- 전체_도시철도노선정보 xlsx는 운영 노선 로스터 → 참고용, 이식 안 함
- **잔여**: 지하철 출구 좌표(t-data엔 없음), 버스 노선·배차, GTX·동북선 등 별도 계획, 아파트 단지 경계(사용자 다음 작업)

## 2026-09-01 공동주택(아파트) 원천 준비 + data.go.kr 키 슬롯

- 실행한 요청: "공동주택 단지 목록 제공 서비스는 api로 가져와야하므로 env example 보여줄 것 / 면적 정보와 GIS건물 통합정보 준비 완료"
- 파일 확보(이식 대기) → `원천데이터/공동주택/`:
  - `공동주택_단지면적정보.xlsx` (15073269, 전국, 단지코드·단지명·동리·동수·관리비부과면적·주거전용면적·세대수, 주1회). 좌표 없음
  - `GIS건물통합정보_서울/AL_D010_11_20260809.*` (15083092, 서울 건물 폴리곤 695,761개, **EPSG:5186** = false_northing 600000, dbf cp949 1.26GB)
- `.env.example`: `DATA_GO_KR_SERVICE_KEY`(단일 키) 슬롯을 "활성" 섹션으로 승격 + 엔드포인트 `AptListService4`(getSido/Sigungu/Legaldong/Roadname/TotalAptList4, 일 5,000)/활용신청 안내. **이 API는 단지코드·단지명만** → 상세주소는 15058453, 좌표는 VWorld 지오코딩으로 설계
- `.gitignore`: `공동주택_단지면적정보.xlsx`·`AL_D010_*`·`GIS건물통합정보*/` (dbf 1GB+ 보호)
- 갱신: `data-acquisition-sources`(아파트 표·좌표계표에 5186 행), `data-catalog`(missing 행에 파일 확보 표기), `handoff`(원천 폴더표), `.env.example` RAW_DATA_DIR 주석
- 설계(사용자 다음 작업 `ingest_apartment_complex.py`): 15057332 `getSigunguAptList4`(서울 25구) → 단지코드·단지명 → 15058453 기본정보로 도로명주소 → VWorld 지오코딩 → GIS건물통합정보 공동주택 용도 폴리곤을 법정동+근접 dissolve → 단지 경계. 5186→5181 재투영. `nearby_anchors` 아파트단지 타입 활성

## 2026-09-02 entry_health_v1 등급 컷·가중치 확정 (승인)

- 실행한 요청: "entry_health_v1컷 설계 시작" → "전체 권고안대로 승인, 이어서 처리"
- `scripts/design_entry_health_cuts.py` → `output/entry_health/` (units·grade_by_label·weight_sensitivity·cut_candidates.json)
- **설계 중 발견**: 처음엔 상권×업종 grain(커피)으로 분위를 냈으나 **커피 기준 폐업률·개업률이 71% 0**(1,519셀 중 1,080·1,092) → 분위 무의미. **FC-10을 전 업종 통합 지역(상권/행정동) 배경 등급으로 재정의** — `상권_변화_지표` 라벨도 전 업종 통합값이라 grain 일치. 전 업종 통합 시 상권 1,650개 중 개업률 0은 343(21%)·폐업률 0은 201(12%)로 정상. 업종 신호는 FC-11·12·30이 담당
- **확정 산식**: `risk = 0.25·폐업률분위 + 0.25·(100−개업률분위) + 0.25·(100−점포증감률분위) + 0.25·라벨리스크·100` (전 업종 통합 상권/행정동, 서울 분위)
  - 잠정안 대비 ① 순개업률(개업−폐업)→개업률 단독(폐업 이중계상 제거, r −0.6→+0.24) ② 동일가중
  - 등급 컷 = 20261 서울 사분위 **동결**. 상권 `[41,51,62]` 행정동 `[42,52,61]`
  - 검증: 등급 × 상권변화지표 라벨 **단조 정렬**(HL 셀 56%→경계, LH→양호). 가중치 민감도: 대안 벡터가 경계 등급 20~40% 이동하나 2등급↑ ≤6%, 동일가중 중립 기준
  - 지점이 상권·행정동 밖 → `정보없음`. 라벨 결측 0건
- **판정**: 등급은 **반대근거 1항목**으로만(주의·경계일 때), `fit_tier` 게이트·정렬키 금지
- `sample_jamsil_coffee.py` `_tier()` 확정 산식 적용 → 잠실동·커피 16지점: 잠실역 상권 LH 양호, 잠실새내역 LL 양호, 관광특구 LL 주의, 주거 행정동 HH 주의. 추천 7→5(관광특구 지점 조건부 하향). 스키마 0 오류
- 갱신: profile §7·§9·§2·§5 FC-10·§8, `candidate-selection-spec` §6·§8 141행, `rag-evidence-schema.json`(dimension_evidence.진입건전성.entry_health_v1), `recommendation-quality-review.md`(#10 해소·후속 노트·판정 High 0), `final` 상태·흐름·승인·미검증, README·handoff. `.claude/agents/` 5개 model gpt→inherit(사용자 요청)

## 2026-09-02 QA 라운드 4 + 최종 명세 갱신

- 실행한 요청: "QA 재검토 실행하고 최종 명세 갱신"
- 검토 대상: 지점 후보 재설계(spec §1)·10건 이식·FC-01 유동밀도·FC-06a·b 근사 비율·샘플 16건
- 방식: `validate_harness.py` PASS + `jsonschema` 유효 + 샘플 candidate-evidence.json **자동 결함 검사** (F01·14·19·20·21·22 전부 통과 — 16/16)
- 발견·수정: `precision` enum 정리("상권+앵커" 폐기), spec §7에 evidence·source_freshness 필수 주석, 프로파일 §4 예시 JSON 갱신(FC-01_flow_vitality·FC-06a·FC-52b), FC-06_foreign_visitor_ratio inactive 예시 → 랜드마크로 교체
- **잔여 High 1건**: `entry_health_v1` 등급 컷·가중치 미확정 → 사람 승인. 승인 전까지 등급을 판정에 쓰지 않고 입력값만 노출
- **Medium 3**: R-ONE 상권↔상권분석 상권 매핑(비용 차원 공백), host 상권 최근접 배정 규칙(sparse 지역), 네이버 트렌드 배치 경계
- `recommendation-quality-review.md` 라운드 4로 재작성(라운드 3 부록 보존), `final/recommendation-system-spec.md`·README·handoff 갱신. Critical 0

## 2026-09-02 FC-06a 외국인 거주 비율 — 상주인구 근사 분모

- 실행한 요청: "내국인의 경우 상주인구로 분모를 대체할 수 있지 않나?"
- **결정**: FC-06a(거주형) = `장기 외국인 생활인구 / 상주인구` 근사 비율 허용. 둘 다 행정동·20261. 검증: 잠실3동 2.4%·대림2동 11.9%(방향 맞음, >100% 폭주 없음)
- **한계**: 분자=생활인구(통신 신호 시간대 평균), 분모=상권분석 상주인구(주민등록 기반) → 방법론 불일치. "근사·신호"로만, 정밀 비율은 내국인 생활인구 확보 후
- FC-06b(방문형)는 상주인구 분모 부적합(관광객/거주민 무의미) — 수준·분위 또는 유동인구 대비로
- 갱신: profile FC-06a·b·§6 이식표·§8 크로스워크, `data-catalog`(내국인 생활인구 missing→근사 대체 가능), spec §8, 회귀 **F22**, RAG 스키마 `foreign_over_resident_ratio`
- `sample_jamsil_coffee.py` 재실행: 16개 지점 전부 FC-06a evidence(잠실 일대 1.3~5.2%). 수요구성 차원에 FC-06a 추가
- FC-06b(방문형)도 추가(사용자 요청): **근사 비율 = 단기 외국인 일평균 / 유동인구 일평균(분기총÷90)** + 단기/장기 비율. 잠실6동(롯데월드) 5.6%·서울 97.6%ile, 석촌동(주거) 0.2% — 관광지 성격 신호. RAG 스키마 `short_foreign_over_flow_ratio`, F22 확장. 분자=생활인구·분모=유동인구 방법론 상이 → "신호" 표기 필수

## 2026-09-02 후보 단위 재설계 — 상권 폴리곤 → 지점(아파트·역)

- 실행한 요청: "상권에 한정하면 안 됨 / 진행해줘. 아파트+역 지점 후보로 재설계"
- 문제: 상권 폴리곤이 서울 27%만 커버(164.9/605.8 km²), 크기 20배 편차 → 폴리곤이 후보면 서울 3/4 누락 + "상권 전체 추천"이 특정 자리 아님. 상권-중심은 초기 하네스 디폴트가 굳은 것(의도적 비교 없었음)
- **재설계**: 후보 = 좌표를 가진 **지점**(아파트 단지 3,047개 + 역 412개). 상권·배후지·행정동은 "지점이 참조하는 배경 지표 grain"으로 강등
  - 지점 반경 500m 직접 계산(고유값): FC-07 역·버스, FC-08 아파트
  - 포함 상권 → 없으면 **행정동** 배경값(`grain_is_proxy`+`host_commercial_area`+`grain_notes`): FC-01·02·11·12·30~32·40·50
  - 자치구/권역: 기존 프록시(FC-20·41·53)
- 갱신: `candidate-selection-spec` §1 전면 재작성(§1-0 seed, §1-1 절차, §1-2 grain 선택, §1-3 위치 출력)·§2·§3·§7 DTO·§8·§9. `rag-evidence-schema`(candidate_type `아파트단지_인근`/`역_인근`, spatial_grain `지점`, location `anchor`/`host_commercial_area`/`point`, `dimension_evidence.grain_notes`). `00-input.md`·profile §2. 회귀 **F20**(지점에 배경값을 고유값처럼) + **F21**(상권 폴리곤을 후보로) 추가
- `scripts/sample_jamsil_coffee.py` 전면 재작성 → 지점 후보. 잠실2·3·7동(법정동 잠실동) seed 16개(아파트12+역4), RAG 스키마 통과. 추천 7·조건부 9
- QA(`40-sample/jamsil-coffee/qa-review.md`): F20·F21 통과, 행정동 fallback 동작. M-S1(입력 해석) 개선(법정동→잠실2·3·7동, 자치구 가드). 잔여 High = entry_health 컷(변함 없음). M-S3(host 최근접 배정), 파이프라인 코드화

## 2026-09-01 FC-01 정규화 결정 — 유동밀도

- 실행한 요청: "FC-01 정규화 방식 정해줘" (샘플 QA H-S1 후속)
- **결정: FC-01 = 총 유동인구 / 상권 면적(명/㎡·분기)** 를 서울·시군구 분위의 주력값. 총 유동인구는 규모 5분위 등급으로만 병기(순위 근거 금지). 상권 면적 서울 상위 3%면 `밀도_저평가_주의`(공원·호수·대형시설로 밀도 희석) → 추천 상한
- 대안 검토: (a) 총량 유지 = 잠실역<잠실새내역 반례로 기각 (b) **유동/면적** = 채택, 1차 근사로 충분 (c) 유동/도로연장 = 이론상 정확하나 도로망 데이터 미보유 → TODO (d) 구성비만 = 규모 정보 상실, FC-05가 이미 담당
- 검증(잠실 6상권): 잠실역 총량분위 74.7 → 밀도분위 7.8 (**판정 추천→조건부**), 삼전역1번 92.7→53.6, 잠실새내역 96.2→70.5(추천 유지)
- 반영: profile FC-01 재정의, spec §3 현재수요, 회귀 F19 갱신, RAG 스키마 `flow_per_area_normalize` 추가, `sample_jamsil_coffee.py` 재실행. 샘플 QA H-S1 해결
- **잔여 High**: entry_health_v1 등급 컷(H-S2, 사람 승인)

## 2026-09-01 실데이터 샘플 실행 + 점진 QA — 잠실동·커피

- 실행한 요청: "송파 잠실동·커피 샘플 실행해줘"
- `scripts/sample_jamsil_coffee.py` — `candidate-selection-spec` §0~8 + 프로파일을 손으로 실행하는 1건 프로토타입(파이프라인 코드는 아직 없음)
- 산출: `artifacts/40-sample/jamsil-coffee/{feature-table,candidate-evidence}.json + run-notes.md + qa-review.md`. 후보 6개(잠실 일대 상권), **RAG 스키마 검증 통과**(3회 수정 후 오류 0)
- 스키마 수정으로 배운 것: evidence 항목은 13개 필수 필드(metric_name·comparison_scope·quarter_file_source·normalization·interpretation·limitation…)·`additionalProperties:false`. `feature_build.quarters_used` 는 분기 배열, `grain_resolution.method` const, `coverage` 는 {matched,expected} 객체
- **점진 QA 결과** (`qa-review.md`):
  - High H-S1: `_tier()` 가 FC-01 총 유동 분위(≥70%)를 판정 positive 로 사용 → profile·spec 금지사항 위반. **실데이터 반례: 잠실역 총유동 1,100,680 < 잠실새내역 2,563,692** (길단위인구 = 폴리곤 내 도로구간 합, 면적·도로밀도 의존)
  - High H-S2: entry_health_v1 등급 컷(risk 0~3)이 임의 → 사람 승인 대기 재확인
  - Medium: 입력 "잠실동"(법정동)이 잠실4·6동(신천동) 상권까지 포함, `admin_dong` 구 행정동명 / 비용 차원 전부 R-ONE 매핑 공백 / 네이버 트렌드 송파구 rel_index 2026-07 이후 급락(배치 경계 의심)
  - 통과: grain 프록시(F14)·부분기간·null 유지·`score_is_predictive=false`·앵커 실데이터만(F11·F16·F18)·연도파일 분리(F09·F10)
- 반영: profile FC-01·spec §3 에 "총량 분위 순위 근거 금지" 명시, 회귀 **F19** 추가, `sample_jamsil_coffee.py` 커밋
- **다음**: FC-01 정규화 방식 결정 → R-ONE 상권 매핑 → entry_health 컷 승인 → 파이프라인 코드화

## 2026-09-01 공동주택(아파트) 이식 — FC-08 신설

- 실행한 요청: "키 발급했어. ingest_apartment_complex.py 만들어줘"
- `scripts/ingest_apartment_complex.py` → `data/공동주택/{아파트단지_서울,상권_아파트접근성}.csv` + `_geocode_cache.json`(커밋)
- 파이프라인: K-apt `AptListService4/getSidoAptList4`(sidoCode=11, 서울 3,396단지) + 면적 xlsx(15073269, 세대수·동수·면적 join) + **VWorld Search API**(`/req/search` type=place, `{구}{동}{단지명}` → 좌표, 8스레드, 캐시) + GIS건물통합정보(5186→5181, 세대수 비례 버퍼로 footprint 근사)
- 실제 API: 15058453(기본정보)는 미구독 → 설계 변경, VWorld Search 로 단지명 지오코딩. data.go.kr 키는 **Encoding 형태 단일 키** → URL 에 raw append(urlencode 금지)
- 커버리지: **3,047/3,396 (89.7%)** 좌표 — high 2,447/medium 531/low 69/**미매칭 349**(신축·도시형생활주택·SH임대). `_name_variants`로 (…)·동접두어·맨션·차/단지 제거 → 재시도로 83개 추가 매칭
- **1,461/1,650 상권**이 250m 내 단지. 250m내 세대수합 중앙 1,334·최대 16,135(가락)
- 프로파일 **FC-08(배후 주거단지 규모) 신설·active**(G1, 계단식). `nearby_anchors` 아파트단지 타입(+`households`) 부분 활성. 카운트 active 10→**11**
- 갱신: `data-catalog`(4행+스냅샷 목록), `data-integrity-check`(이식 감사), `candidate-selection-spec` §1-1·§3·§7·§8, `rag-evidence-schema`(households·normalizations 3개·spatial_grain), `data-inventory`·`data-acquisition-sources`·`handoff`. 회귀 **F18**(K-apt 성격·미매칭·신뢰도·근사경계 오용 반려)
- 한계: K-apt 의무관리 위주 → 소형 빌라·연립 누락. footprint 는 근사(인접 단지 스필오버·주상복합 누락). 재건축 예정 미반영
- **잔여**: 미매칭 349, 소형 주거, 정밀 경계

## 2026-09-02 카카오맵 POI 부분 이식 (GPT/Codex)

- 실행한 요청: "API key입력했음 이를통해 POI 검색할 수 있는지 검색할 것" → "진행할 것, Claude Code용 인수인계 파일 작성"
- **주체 명시**: 이 단계는 **GPT(Codex)**가 수행했다. 다음 Claude Code 실행은 `artifacts/handoff_kakao_poi.md`를 첫 문서로 읽는다.
- 변경: `.env.example`에 `KAKAO_REST_API_KEY` 슬롯을 두고, `scripts/kakao_local.py`(키워드·카테고리·coord2address)와 `scripts/ingest_kakao_poi.py`(정규화·WGS84→EPSG:5181·dedup·manifest)를 추가했다.
- 실호출 검증: 키워드 `잠실역` 3건 HTTP 200, `FD6`+`CE7` 잠실역 반경 1km 6회 호출. 90행 수집, 좌표 결측 0, Kakao id 중복 제거 0. RAG 스키마와 기존 16개 샘플도 재검증 PASS.
- 설계 경계(당시): 카카오 POI는 **선택적 관측 seed**로만 분류했다. 매물·공실·성공 outcome·ML 정답으로 사용하지 않으며, 서울 전체 커버리지로 일반화하지 않는다. 이후 같은 날 공통 후보 엔진의 `--include-poi`로 선택 연결했고, 공식 쿼터 가드와 상세 맥락 설계를 추가했다(아래 2026-09-02 후속 기록 참조).
- 남은 작업: 선택 지역 경계별 격자/사각형 배치·호출예산·캐시, 카테고리/페이지 커버리지 QA, `카카오POI_인근` candidate/RAG 경계 QA, 다른 지역·업종·greenfield 재검증, Kakao 약관·쿼터 사람 승인.

## 2026-09-02 R-ONE API 키 슬롯 추가

- 실행한 요청: "R-ONE 부동산 통계정보 api key도 발급받았어 env example에 필드 생성"
- 변경: `.env.example`에 `RONE_API_KEY=` 추가. 용도는 한국부동산원 R-ONE 임대료·임대가격지수·공실률 갱신 보조이며, 실제 키 값은 `.env`에만 둔다.
- 현재 상태: `data/임대료/R-ONE_임대동향_분기.csv` 파일 이식본은 유지한다. R-ONE API 래퍼·실호출·응답 스키마 검증은 아직 수행하지 않았다.

## 2026-09-02 R-ONE Open API 사용법 확인·실호출 (GPT/Codex)

- 실행한 요청: R-ONE 개발자 페이지 사용법 확인 및 실제 API 요청.
- 공식 개발가이드의 REST GET 방식, `KEY`, `Type=json`, `pIndex`·`pSize`, `SttsApiTbl.do`·`SttsApiTblData.do` 규칙을 확인했다.
- `scripts/rone_api.py` 추가: 통계표 목록과 통계자료 조회, 중첩 JSON 평탄화·요약 출력, 인증/일시 네트워크 오류 처리를 제공한다. 키는 `scripts/_env.py`의 `RONE_API_KEY`에서 읽고 출력하지 않는다.
- 실호출 성공: `T244363134858603` 중대형 상가 임대료와 `T249633134845544` 중대형 상가 공실률에서 서울(`CLS_ID=500002`)·해당 지표(`ITM_ID=100001`)를 조회. `INFO-000`, 각 8개 분기, 2024Q3~2026Q2 반환.
- 확인값: 임대료 54.7556→56.7081천원/㎡, 공실률 8.6563→9.6720%. 응답 필드는 `STATBL_ID`, `DTACYCLE_CD`, `WRTTIME_IDTFR_ID`, `CLS_ID/CLS_FULLNM`, `ITM_ID/ITM_NM`, `DTA_VAL`, `UI_NM`, `WRTTIME_DESC` 등이다.
- 경계(당시 API 단계): API 실호출은 검증했지만 `R-ONE 상권`과 서울시 상권분석 1,650 상권의 매핑, API 자동 갱신, 공실률의 FC-21 CSV 이식은 아직 미완료. 이후 1차 crosswalk를 별도 생성했다. 상세 인수인계는 `artifacts/handoff_rone_api.md`·`artifacts/handoff_rone_trdar_crosswalk.md`.

## 2026-09-02 R-ONE ↔ 서울 상권분석 crosswalk 생성 (GPT/Codex)

- 실행한 요청: "매핑 실시할 것"
- **주체 명시**: 이 단계는 **GPT(Codex)**가 수행했다. Claude Code는 `artifacts/handoff_rone_trdar_crosswalk.md`와 `output/crosswalks/`를 먼저 읽고 후속 연결을 진행한다.
- 변경: `scripts/build_rone_trdar_crosswalk.py` 추가. 현재 R-ONE 72개 상권명과 서울시 상권분석 SHP 1,650개 상권명을 검증하고, 명시적 alias·복합권역·도로 proxy·미해결 상태를 가진 crosswalk를 생성한다.
- 산출: `output/crosswalks/crosswalk_rone_trdar.csv` 84행 + `crosswalk_rone_trdar_summary.json`. 71/72개 R-ONE 상권에 후보가 있고, 자동 결합 후보는 52/72, review 18, `테헤란로` unresolved 1, 대상 재사용 1이다.
- 설계 경계: R-ONE과 서울시 상권분석 상권은 동일 폴리곤이 아니다. 따라서 자동 결합은 `matched + primary + target_reuse_count=1 + join_eligible=yes`만 허용하며, 값은 R-ONE 상권 grain·`grain_is_proxy=true`로 유지한다. `잠실/송파`는 복합권역 review라 샘플 비용 근거는 서울 지수 proxy로 남긴다.
- 재현: `.venv/bin/python3 scripts/build_rone_trdar_crosswalk.py`

## 2026-09-02 구 분석 프로젝트 output·docs 정리

- 실행한 요청: "현재 필요없는 output과 docs도 제거" → 확인 후 "docs/data_result 시리즈는 냅둘 것, 구 분석 프로젝트 output/docs" → 보류 2건은 "둘 다 남길 것"
- 삭제(output/, ~12MB): `output/{seoul,gu}/`, `output/rent_*.csv`(루트 53), `output/figures/{seoul,gu,rent_change,rent_combined,rent_flow,rent_match,rent_sales,rent_store}/`, `output/.DS_Store`. `output/songpa/`·`output/figures/songpa/`는 추적파일 → `git rm`(커밋 전 복구 가능). 미추적은 영구 삭제
- 삭제(docs/): `발표자료.md`, `발표자료_대본.md`, `팀보고서_정오표.md`, `스코어링_추천모델_보고서.pdf`(같은 내용 .md 보존)
- 보존: `docs/{data_result.md,data_result_2.md,data_result_3.md,PROGRESS.md,architecture/,최종_분석_보고서.md,스코어링_추천모델_보고서.md}`. `output/{change_indicator,crosswalks,entry_health}/`, `output/data_integrity_report.json`, `output/dataset_update_frequency*.csv`, `output/figures/change_indicator/` + 겹침 예시 4 png
- 보류→보존 확정(의존성): `최종_분석_보고서.md`(PROGRESS.md가 "최종 제출물"로 참조), `스코어링_추천모델_보고서.md`(`evaluating-legacy-ranking` 스킬 감사 입력)
- 손대지 않음: 구 분석 스크립트(`scripts/{seoul_*,songpa_*,rent_*}.py` — 삭제된 output의 생성기, 필요 시 재생성). `docs/data_result*.md` 본문의 삭제 경로 언급도 서술이라 유지
- 하네스 무영향: `validate_harness.py` PASS, 샘플·스키마 검증 그대로. 삭제 대상은 카카오 POI(`data/카카오POI/`) 동시작업과 무관

## 2026-09-02 구 분석 스크립트 → `scripts/phase1_analysis/` 이동

- 실행한 요청: "phase1_analysis 폴더로 이동 정리해줘"
- `scripts/` 71개 → 루트 25개(하네스) + `scripts/phase1_analysis/` 46개(대회 1~24장 분석)
- 이동: `seoul_*`(6)·`songpa_*`(9)·`gu_*`(3)·`rent_*`(11)·`plot_*`(12, `plot_dataset_update_frequency` 제외)·`scatter_*`(2)·`scoring_*`(2)·`make_combined_csv`(1). 추적 11개는 `git mv`
- 루트 유지(하네스 참조): `change_indicator_store_correlation.py`(CLAUDE.md 인용), `build_overlap_crosswalks.py`(`output/crosswalks/` 생성기), `make_gu_dong_lists.py`(웹서비스 청사진 C3), `check/plot_dataset_update_frequency.py`(데이터 감사 입력)
- ROOT 보정: 이동 스크립트 36개의 `dirname(dirname(abspath(__file__)))` → `dirname` 한 번 추가(한 단계 깊어짐). 전부 `py_compile` OK, ROOT가 저장소 루트로 재해석됨 검증
- 참조 갱신: `.claude/skills/evaluating-legacy-ranking/SKILL.md`(scoring 2줄), `docs/architecture/recommendation-web-service.md`(오프라인 전용 행), `artifacts/handoff_kakao_poi.md`(scoring 언급). `.claude/settings.local.json`의 `rent_*` 는 과거 권한 항목이라 방치
- `scripts/phase1_analysis/README.md` 신설 — 그룹·산출물·실행 주의(cwd=루트) 정리
- 하네스 무영향: import 결합 0(하네스 스크립트가 구 분석 스크립트 import 안 함), `validate_harness.py` PASS. `scoring_evaluate_all`↔`scoring_topk_recommend` 상호 import는 같은 폴더라 유지

## 2026-09-02 공통 증거 중심 후보 생성 MVP 구현 (GPT/Codex)

- `scripts/recommendation_pipeline.py`를 추가해 `sample_jamsil_coffee.py`의 잠실·커피 하드코딩을 재사용 가능한 CLI로 일반화했다.
- 입력 검증: 서울 시도, 자치구, 행정동(법정동 `잠실동` → 잠실2·3·7동 alias), 10개 업종 코드, 최소 특별조건 파싱.
- 후보 생성: 검증된 아파트·역 seed + 선택적 카카오 POI, 경계+300m, 80m dedup. 지점 상권 PIP → 같은 자치구 최근접 300m → 행정동 fallback.
- 결합: 20261 업종 점포·추정매출·유동·변화지표, 지점 반경 역·버스·아파트, `entry_health_v1`, R-ONE `join_eligible=yes` 임대가격지수 proxy. 상권/행정동 source path를 실제 선택 grain에 맞춰 기록한다.
- 결측 경계: 개별 매물·공실·성공 outcome이 없는 월세·면적·주차 조건은 필터링하지 않고 `unsupported_conditions`/`missing_features`에 기록한다. `fit_index=null`, `score_is_predictive=false`다.
- 검증: 잠실·커피 20건(`--include-poi`), 연남·커피 5건, 역삼1동·한식 5건을 실행했다. 모두 RAG schema 0 오류, 잘못된 시군구는 입력 단계에서 중단, `validate_harness.py` PASS.
- 산출물: `output/recommendation_runs/{jamsil-coffee-mvp,yeonnam-coffee-mvp,yeoksam-korean-mvp}/`. 다음은 R-ONE review 지도 QA, host 동 불일치 규칙, greenfield·POI 전역 QA, UI/백엔드 DTO다.

## 2026-09-02 FC 피처 설명력 검증 (하네스 내부 산출물)

- 실행한 요청: "파이프라인 설계는 아직 이르다 — 지역특성·좋은 입지 설명력이 부족하지 않나" → FC 피처 설명력 검증(사용자 선택)
- `scripts/build_feature_outcome_panel.py` → `output/feature_validation/panel_{trdar_industry,trdar}.csv` (상권×업종 12,204셀). FC ~20개 + outcome(인허가 churn/생존, 점포 교체)
- `scripts/analyze_feature_evidential_value.py` → 단변량 Spearman·중복도·7차원 분리도·증분 편상관·업종별 재현성. `scripts/plot_feature_evidential_value.py` → 5 그림
- **핵심 결론**: 연속 FC 중 폐업/생존에 단조 신호(|ρ|≥0.25) **0개**. 전체 FC 합산 churn rank R² 0.006→0.069(+6%p). ADR-001·Phase 1 A안 동어반복과 정합
  - **범주형은 재현**: FC-10 등급·FC-11 라벨이 `change_indicator_store_correlation`의 LL 최고·HH/LH 낮음을 이 패널에서 재현. 연속 risk 점수로는 무의미
  - 모멘텀 신호(품질 아님): FC-32 프랜차이즈비율(ρ 0.84=체인 확장 프록시), FC-10 risk(0.25)
  - 중복 3쌍(FC-06a↔06b, FC-07 역수↔역거리, FC-20↔FC-42 아티팩트), 업종별 부호 뒤집힘 9개
- **함의**: 학습형 결합 스코어(`fit_index_v1` 가중 모델) 금지 — 결합할 신호가 없음. "증거 제시기"로 유지. FC 신호 등급표(프로파일 §9-1)
- 갱신: `artifacts/10-analysis/feature-evidential-value.md` 신규, profile §5-1·§9-1, `candidate-selection-spec` §3, README·handoff
- **한계**: 매출·손익 outcome 없음 → "좋은 입지=성공" 여전히 미검증. 인허가 생존 상한 포화
- 병행 GPT MVP(`recommendation_pipeline.py`)는 이 검증 전에 만들어졌으나 `fit_index=null`·규칙 기반이라 결론과 정합. GPT MVP가 신호 없는 FC를 positive/negative 근거로 쓰는지 점검 필요

## 2026-09-02 카카오 Local 공식 쿼터 가드 + 영역 기반 상세 입지 맥락 설계 (GPT/Codex)

- 사용자 요청: Kakao 공식 무료 쿼터를 확인해 API 호출 제한을 구현하고, 영역 겹침·여러 지도 위치 앵커와 Kakao POI를 조합하는 상세 입지 설계의 타당성을 점검
- 공식 확인: 키워드 장소 검색과 카테고리 장소 검색은 각각 일 100,000건, 전체 API 무료 쿼터는 월 3,000,000건(2026-09-02 기준; 변경 가능)
- 구현: `scripts/_budget.py`를 일·월·실행당 영속 가드로 확장, `scripts/kakao_local.py`가 실제 HTTP 시도 직전 `check()`·`commit()`을 호출하도록 변경. 재시도·HTTP 오류도 보수적으로 `.api_budget.json`에 남긴다. `.env.example` 기본값은 일 90,000·월 2,700,000·실행당 500이고 무료 상한 초과 설정은 거부한다. `scripts/kakao_local.py --budget`으로 무호출 상태 확인 가능
- ingest: `scripts/ingest_kakao_poi.py` manifest에 호출 전후 예산 snapshot을 저장. `--max-requests`는 논리 페이지 상한으로 유지하되, 영속 가드가 물리 HTTP 시도 상한을 맡는다
- 설계 판단: **타당하다.** SHP의 상권·상권배후지·행정동 경계 및 PIP/겹침이 영역의 정본이고, Kakao Local은 확정된 경계의 격자·사각형·앵커 주변에서 POI 구성/밀도/최근접 생활편의를 관측하는 상세 맥락 층이다. 후보 250m·500m evidence에 쓰되, 카테고리·격자·페이지 커버리지 QA 전에는 보조 evidence만 허용한다
- 한계: Kakao POI만으로 상가 전체, 공실, 임대 매물, 월세·면적·주차·호실, 창업 성공 outcome을 보장하지 못한다. 이 필드는 별도 원천·검증이 필요하다
- 인수인계: `artifacts/handoff_kakao_poi.md`에 GPT(Codex) 변경·예산 확인 명령·Claude Code의 다음 순서를 반영했다

## 2026-09-02 카카오 Local `rect` 격자 POI 수집·커버리지 QA 구현 (GPT/Codex)

- 실행한 요청: 카카오 POI로 선택 영역의 상세 입지 데이터를 보완할 수 있도록 우선 구현
- 구현: `scripts/kakao_local.py`의 카테고리 검색에 Kakao `rect` 파라미터를 추가하고, `scripts/ingest_kakao_poi_grid.py`를 신설했다. 공통 추천 파이프라인의 시군구·법정동 alias 및 SHP를 재사용해 대상 폴리곤을 EPSG:5181 격자로 나눈다.
- 품질 경계: Kakao가 사각형 안에서 반환한 POI를 대상 폴리곤으로 다시 PIP하고 Kakao ID로 중복 제거한다. Kakao의 `pageable_count`는 최대 45문서이므로, `total_count > pageable_count`이면 4분할한다. 최소 셀 크기·요청 상한·API 오류는 `partial_*`로 manifest에 남긴다.
- 검증: 잠실동 `FD6`·`CE7` dry-run에서 500m 셀 35개·최소 70 요청 계획을 확인했다. 작은 실제 `rect` 카테고리 호출은 `total_count=8`, 1건 반환으로 성공했다. wrapper rect 계약과 PIP·포화 탐지 mock 테스트도 통과했다.
- 운영 규칙: 결과는 `data/카카오POI/context/`에 저장해 루트의 선택적 seed CSV와 자동 혼합하지 않는다. `coverage.status=complete_requested_queries`도 입력 카테고리·키워드의 관측 완결성일 뿐, 전체 상가·매물·공실·성공 outcome을 뜻하지 않는다.
- 파일럿 결과: 잠실동 FD6/CE7을 실제 수집해 343행·193 논리 요청·포화 20셀 분할·`complete_requested_queries`를 기록했다. 첫 샌드박스 DNS 실패 재시도 3회와 파일럿 두 번을 포함해 당일 Kakao Local 사용량은 386회로 영속 기록됐다.
- 다음: 다른 선택 영역·업종도 같은 dry-run·예산·커버리지 절차로 수집한 뒤, 아래의 RAG context 불변성 QA를 반복한다.

## 2026-09-02 Kakao POI context RAG 부분 재실행 (GPT/Codex)

- 실행한 요청: 다음 단계로 잠실동의 완결 POI snapshot을 후보별 250m·500m 근거로 연결하되, 지역 특성과 좋은 입지의 상위 정의를 유지
- 구현: `scripts/recommendation_pipeline.py --include-poi-context`가 요청 시도·시군구·법정동과 정확히 일치하고 `coverage.status=complete_requested_queries`인 manifest만 선택한다. CSV 행 수·POI ID·EPSG:5181 좌표를 재검증한 뒤 후보 지점 반경 안의 카테고리 수를 집계한다.
- 결과: 잠실동·커피 27개 후보 모두에 `CE7`·`FD6`의 250m/500m 관측 4개를 추가했고, RAG schema 오류는 0건이다. context 없음 실행과 비교해 후보 ID·순서·`fit_tier`·긍정 근거·반대 근거가 전부 동일했다.
- QA 수정: 초기 context 연결에서 POI 카테고리 표시명이 `상권_변화_지표` 라벨을 덮어쓰는 변수 재사용을 발견·수정했다. 실제 등급·정렬에는 영향이 없었지만 지역 특성의 중립 표기가 훼손될 수 있어 F25 회귀로 고정했다.
- 설계 판단: **지역 특성은 FC의 중립 서술**, **좋은 입지는 사용자 조건·분리된 근거·반대 근거·결측을 함께 보는 판정**이다. POI는 어느 쪽도 대체하지 않는 지점 grain의 보조 관측이다. FD6·CE7은 전체 상업시설·공실·매물·수요·성공 outcome이 아니므로 등급·정렬·성공확률에 사용하지 않는다.
- 다음: 다른 영역·카테고리를 완결 수집한 뒤 같은 비교 QA를 반복하고, UI/백엔드 DTO에는 관측값·스냅샷 시각·한계를 분리해 노출한다.

## 2026-09-02 Kakao POI context 비교 QA 반복 (GPT/Codex)

- 실행한 요청: context 비교 QA를 우선 반복
- 구현: `scripts/qa_poi_context_invariance.py`를 추가했다. 같은 요청을 `--include-poi-context` 없음/있음으로 실행하고, F24(완결 context만·후보 ID/순서·등급·긍정/반대 근거 불변), F25(FC-11/`entry_health` 라벨 불변), 지점 grain·출처·정규화·한계를 검사한다.
- 결과: 잠실동 FD6/CE7 완결 context와 동일한 특별조건에서 프로젝트 10개 업종(CS100001~CS100010)을 모두 실행했다. 업종별 후보는 27개, **10/10 PASS, 오류 0**이다. 산출물: `artifacts/evals/poi-context-invariance-2026-09-02/`, 실행 쌍: `output/recommendation_runs/poi-context-invariance-2026-09-02/`.
- 해석: 이는 한 지역·두 카테고리 snapshot에서 POI가 추천 판정에 누수되지 않았음을 보장할 뿐, 다른 지역·카테고리·시간의 상업시설 완전성이나 창업 성공을 검증한 것은 아니다.

## 2026-09-02 판정 로직에 FC 신호 등급표 반영 — `_tier()`·`recommendation_pipeline.py`

- 실행한 요청: "우선 `_tier()`와 recommendation_pipeline 수정 진행" (FC 피처 설명력 검증 결과를 판정 코드에 반영)
- 배경: `feature-evidential-value.md` §9·프로파일 §9-1에서 **연속 FC 중 폐업/생존 단조 신호 0개**로 판정됐는데, `recommendation_pipeline.py`(GPT/Codex MVP)와 `sample_jamsil_coffee.py._tier()`는 여전히 FC-01 유동밀도를 positive 근거로, 근거 수(`-len(reasons)`)를 정렬 키로 쓰고 있었다.
- 변경:
  - 근거 3분류 도입 — `reasons`(약한 배경 신호 FC-08·31·07 bus_n만), `counter_evidence`(범주 신호 FC-10 등급·FC-11 라벨 + 하드조건 + 결측), `context_notes`(신호 없음 FC-01·30·07 역거리 + 모멘텀 FC-32). `context_notes`는 `fit_tier`·정렬·`data_confidence` 미반영.
  - FC-01 유동밀도를 `reasons`/`counter`에서 완전 제거 → `context_notes`로. 면적 큰 상권의 밀도 저평가 penalty(`area_hi`)·저밀도 penalty(`low_dens`)도 판정에서 제외(둘 다 FC-01 파생 노이즈).
  - FC-31 매출은 `reasons`에 남기되 "약한 배경 신호, 과거 실적이며 신규 성공 아님" 표기 + 약한 배경 신호가 근거의 전부이면 `data_confidence` 하향.
  - 약한 배경 신호 단독 판정 금지 — `reasons` 2개 이상 + blocking 없음일 때만 `추천`(sample_jamsil `_tier`의 `len(pos)>=2` 기준을 pipeline에도 통일).
  - 정렬 키에서 `-len(reasons)` 제거 → `tier` → 반대근거 수 → `data_confidence` → `candidate_id`(안정 정렬).
  - `rag-evidence-schema.json`에 `context_notes`(optional, string[]) 추가 + `reasons`/`counter_evidence` description에 허용 FC 등급 명시.
- 검증: `sample_jamsil_coffee.py` 16건 schema 0 오류(추천 9·조건부 7), pipeline 3개 재실행(잠실 조건부 20 — 특별조건 3개 unsupported / 연남 추천 2·조건부 5 / 역삼1동 추천 15·조건부 5), `qa_poi_context_invariance.py` 10/10 PASS 0오류, `validate_harness.py` PASS.
- 결함 세트 F26 추가. `candidate-selection-spec.md` §4-1·§5 갱신.
- 잔여: `fit_index_v1` 가중치는 여전히 미확정(만들 신호 없음 — §9). host 최근접 동 불일치 규칙, R-ONE review 지도 QA는 별개.

## 2026-09-02 값 수준 QA (추정매출·점포·유동) — P2 #4

- 실행한 요청: "P2 실행" → 값 수준 이상치 점검부터 (FC-31 매출이 이번 `_tier` 수정에서 판정에 들어갔으므로 그 데이터 품질 우선)
- `scripts/qa_value_levels.py` 신설 → `output/feature_validation/value_level_qa{,_flow,_examples}.csv`·`_summary.json`. 문서 `artifacts/10-analysis/value-level-qa.md`
- 정합성 통과: 점포수·매출 음수·공백 0, 프랜차이즈 ≤ 전체·일반+FC=전체 완벽, 유동인구·면적 결측 0 (전수)
- **발견 1 (headline)**: 상권 grain 추정매출 커버리지 **53.9%**(행정동 87.3%). 일식·양식·제과점 상권은 60~67% 매출 없음. 카드매출 표본 부족 상권 제외라 구조적. → host 상권 매출 없으면 `hard_fail`→조건부 고정. **행정동 매출 fallback 권고(미반영, tier 결과 변경되므로 사람 확인)**
- **발견 2**: `당월_매출_금액` = **분기 합계**(레거시 컬럼명). 분위·정렬엔 영향 없으나 절대값 3배 과대. → evidence 단위 `원/점포·월`→`원/점포·분기`, 서술 명시 (pipeline·sample 반영)
- **발견 3**: 점포당매출 극소 이상치 30셀(< ₩10만/분기, 0.3%) — 표본 1~2건 아티팩트. → `< ₩30만/분기` 결측 취급 (pipeline·sample 반영)
- **발견 4**: greenfield 84셀(명시적 0, 처리됨), 개폐업률 [0,100] 밖 8셀(표시 주의)
- 검증: `sample_jamsil_coffee` 16건 schema 0, pipeline 3 재실행, `qa_poi_context_invariance` 10/10, `validate_harness` PASS
- 결함 F27 추가. `candidate-selection-spec.md` §0-6 갱신
- 다음: P2 #3 (네이버 트렌드 전년 동월 계절성 보정)

## 2026-09-02 네이버 검색트렌드 계절성·이상치 분해 — P2 #3

- 실행한 요청: "P2 실행" → #4 값 수준 QA 완료 후 #3 계절성 보정
- `scripts/analyze_naver_seasonality.py` 신설 → `output/feature_validation/naver_seasonality{,_factors}.csv`·`_summary.json`, 그림 `output/figures/feature_validation/naver_seasonality.png`. 문서 `artifacts/10-analysis/naver-search-seasonality.md`
- 방법(scipy 없이): 중심12MA 대비 비율의 월별 중앙값 → 계절지수 / 탈계절 시계열 / 이상치(rel > 2.5×직전12중앙값 or raw_ratio ≥ 90) / surge_active / YoY(clean)
- **발견 1**: 10개 외식업 검색 관심도는 **완만한 계절성**만(진폭 1.2~1.6x, 봄 3~4월 peak·여름 7~8월 trough 공통). raw 6개월 기울기가 8/10 업종에서 음수인데 탈계절은 전부 양수 → **F12의 정체는 "계절 저점으로 내려가는 걸 추세 하락으로 오독"**
- **발견 2**: **호프-간이주점 2026 여름 급등은 계절성 아님** — raw_ratio가 데이터랩 상한 100 도달(202607), 2021~2025 7월엔 없던 패턴. `surge_active=true` → 추세 판단 불가. 실제 트렌드인지 파이프라인 문제인지 202609~ 로 확인
- **발견 3**: 행정동 grain은 rel_index 0.02~0.32 소신호라 노이즈 큼 → FC-41은 자치구 grain 우선
- **반영**: profile FC-41·42 `formula_when_active`(robust_slope12·surge_active·YoY clean·자치구 우선), spec §8·§3, 결함 F12 재작성. **파이프라인 코드 변경 없음**(FC-41/42는 `partial`이라 `_tier`에 미연결, §9-1 "신호 없음")
- 검증: 스크립트 py_compile OK, 그림 렌더 확인, `validate_harness` PASS
- P2 잔여: #1 내국인 생활인구(다운로드 필요), #2 상권 매출 행정동 fallback(사람 승인), #5 Kakao 다른 지역(GPT 영역)

## 2026-09-02 상권 매출 미제공 처리 — fallback 안 함, 조건부 사유 명확화

- 실행한 요청: "결정: fallback 안 함, 조건부 사유 명확화 진행" (P2 #4 발견 1 후속)
- 배경: 상권×업종 추정매출 커버리지 54% → 행정동 fallback으로 96% 회복 가능하나, 사용자가 "fallback 넣으면 신뢰도 떨어지는 거 아니냐" 지적. 검토 결과 맞음:
  1. 표본 부족 = "소규모 시장" 신호인데 넓은 grain으로 채우면 그 정보 소실
  2. 매출 없는 상권은 대개 골목상권 → 행정동 평균으로 대표 시 체계적 과대평가
  3. tier 인플레이션 — "검증 불가"가 "양호"로 둔갑, 동어반복 스코어의 변종
  4. FC-31은 이미 "약한 배경 신호"라 proxy로 채울 이득 작음
- **결정: 다른 grain 매출 fallback 안 함.** 대신 `sales_thin_market`(host 상권에 점포는 있는데 매출 없음) 케이스를 만들어 `조건부 검토` 유지 + `counter_evidence`·`missing_features`·evidence `missing_reason`을 "결측"에서 **"추정매출 미제공 — 카드거래 표본이 추정 임계치 미만(소규모 시장 가능성), 매출 검증 불가"**로 명확화 (`recommendation_pipeline.py`, `sample_jamsil_coffee.py`)
- 검증: 중랑구 면목본동 일식(매출 매칭 4/12) → 조건부 7·주의 5, 사유 문구 확인. `sample_jamsil_coffee` 16건 schema 0, pipeline 3 재실행, `qa_poi_context_invariance` 10/10, `validate_harness` PASS
- `value-level-qa.md` 발견 1·spec §0-6-1·결함 F27 갱신

## 2026-09-02 격자 생성 좌표 근거 + 후보 seed 연결 (사용자 요청)

- 실행한 요청: 개별 상가 매물 크롤링(불법·비확장) 대신, 공개 데이터 파생 맥락을 격자 생성 좌표에 붙인 `project_generated` 근거 레코드를 만들고 후보 seed에 **추가**. 스키마 + 생성기 둘 다.
- 신규 스키마: `artifacts/20-method/generated-evidence-schema.json` (`gen-evidence-v1`). `evidence_origin=project_generated`·`is_synthetic_anchor=true`·`listing_url/address_point=null` 강제, `limitations[0]`은 "생성 좌표이며 실제 매물·점포 아님", `metric_definitions`로 모든 지표 역추적, `generator` 블록에 스크립트·파라미터·입력 파일.
- 신규 생성기: `scripts/generate_gridpoint_evidence.py` — 선택 지역을 EPSG:5181 격자(기본 100m)로 나눔 → PIP → **반경 150m 영업 중 음식점 인허가 ≥3** 상업 필터 → 역·아파트 seed 80m dedup → 좌표별 metrics(반경 500m 역·인허가·아파트, 250m 버스; POI context 있으면 FD6/CE7). 산출 `output/generated_evidence/<region>/{gridpoint_evidence.jsonl, manifest.json, seeds.json}`. 잠실동: 격자 497 → 상업필터 130 → dedup 117 레코드, schema 0.
- 파이프라인 연결: `recommendation_pipeline.py --include-generated-points <seeds.json|dir>` → `load_generated_seeds` → 기존 seed에 추가(priority 3, 80m dedup). `build_candidate`가 `synthetic`이면 `candidate_type=생성지점_격자`·`spatial_grain=precision=지점(생성)`·`candidate_id=GEN-PT-…`·`anchor.type=생성지점`·`synthetic_anchor=true`·`place_name="…(격자 생성 좌표 · 실제 매물·점포 아님)"`·`context_notes[0]`에 생성 고지+evidence_id. `listing_url/address_point=null`.
- 스키마: `rag-evidence-schema.json`에 `candidate_type=생성지점_격자`·`anchor.type=생성지점`·`spatial_grain/precision=지점(생성)`·`synthetic_anchor` 추가 + `allOf` 조건부(생성지점 → synthetic_anchor·null listing 강제). 결함 F28.
- 검증: 잠실동 `--include-generated-points` 30후보(생성 23·아파트 5·역 2) schema 0. 기존 4 mvp 재실행 tier 분포 불변·schema 0. `sample_jamsil_coffee` 16건 schema 0. `validate_harness` PASS(generated-evidence-schema를 REQUIRED에 추가).
- 미결: 격자 좌표가 대부분 추천으로 나옴(잠실 30/30) — `_tier` 임계값(주의 조건 강화 A+B)은 사용자가 데이터 가져온 뒤 재검토로 보류 중. 생성 evidence의 `active_food_license_count`를 후보 evidence 배열에 접을지 미정.

## 2026-09-02 격자 생성 좌표 — 서울 25개 자치구 전체 확장 (사용자 요청)

- 실행한 요청: "송파구에만 제한하지 말 것. 전체 구에 약 10개씩 생성"
- `generate_gridpoint_evidence.py` 재작성:
  - `--all-seoul`: 서울 25개 자치구 각각 생성 + `output/generated_evidence/_seoul_index.json`
  - 인허가 로더를 서울 전역 1회 파싱 + `shapely.STRtree`로 반경 질의 O(log n)화 (154,694 영업 중 점 → 25구 32초)
  - `--per-region-limit`(기본 10): 상업 필터 + seed dedup 후 **최원점 표본추출**(farthest-point sampling)로 지리적으로 퍼진 K개
  - 역·버스·아파트 리스트를 지역 bbox로 먼저 좁힘
- 실행 결과: **25구 × 10 = 250 레코드, schema 0 오류**. 구별 격자 1000~4700 → 상업 필터 830~2230 → dedup → 최원점 10.
- 파이프라인 정렬 버그 수정: `--limit` 절단이 `candidate_id` 문자열 정렬(APT<GEN<STN) 편향으로 생성지점을 통째로 잘라냄 → 같은 tier 안에서 `candidate_type` 라운드로빈 인터리브 후 절단. spec §5 갱신.
- 검증: 마포구 `--include-generated-points` — 생성지점 10개 전부 후보로 빌드(추천 2·조건부 4·주의 4, 지역 특성 반영). limit 25 시 인터리브로 타입 혼합. 기존 4 mvp·sample 재실행 tier 불변·schema 0. `validate_harness` PASS.
- 참고: 구 전역 격자라 생성지점이 빈 지역에도 찍혀 주의/조건부로 갈림 — tier 시스템이 정상 차별화. `송파구-잠실동/`(동 레벨 테스트 산출)은 `송파구-전체/`로 대체됨(잔존).

## 2026-09-02 FC-42 현재 관심도 배수를 YoY로 교체 (사용자 요청)

- 실행한 요청: "FC-42 current_lift를 yoy_clean_recent_mean으로 교체"
- 배경: GPT가 연결한 FC-42의 `current_lift_vs_all` = 최근 3개월 ÷ 전체 관측 평균(2021~2026). 커피처럼 5년째 우상향인 계열은 항상 >1(1.75배)이라 "관심도 높음"이 장기 성장만 반영.
- 변경 (`recommendation_pipeline.py` `load_naver_industry_attention`): `current_lift_vs_all` 제거 → `yoy_clean_recent_mean`(전년 동월 대비, 이상치 월 제외 — `analyze_naver_seasonality.py` 산출) 사용. 상태 라벨 "전년 동월 대비 관심도 상승/하락/유사"(≥1.2 / ≤0.85 / else). `surge_active`면 "미검증 급등"이 우선. context_notes·evidence 문구·normalization 태그·`rag-evidence-schema.json` enum 갱신.
- 검증: 커피 "전년 동월 대비 1.314배(계절 정합) — 상승", 호프 "미검증 급등"(YoY 1.836 표시하되 라벨은 surge). 4 mvp 재실행 schema 0·tier 불변, sample 16건 0, `validate_harness` PASS. profile FC-41/42·spec §3·§8·quality-review·naver-search-seasonality 갱신.

## 2026-09-02 격자 합성 후보 seed — 명칭·10업종 metric·tier 캡 (사용자 요청)

- 실행한 요청: "1(tier 상한)·2(명칭·provenance) 진행, 업종을 10개로 늘려라". 배경: 사용자·GPT 감사 모두 "개별 상가 데이터가 아니라 조건부 합성 후보 seed"로 정정.
- **① tier 캡**: `recommendation_pipeline.py` — `synthetic_anchor=true` 후보는 `추천` 상한을 `조건부 검토`로 캡(실제 임대 가능 호실 미확인). 반대근거에 "합성 격자 좌표 — 실제 임대 가능 상가·호실 미확인이므로 추천 상한은 조건부". `rag-evidence-schema.json` allOf에 `fit_tier: {not: {const: "추천"}}` 강제.
- **② 명칭**: `generated-commercial-dataset-audit.md` → `generated-gridpoint-seed-audit.md`. 공식 명칭 "공개 데이터 파생 격자 합성 후보 seed + 접근성/경쟁 맥락". README·handoff·data-usage-classification·data-inventory·spec §1-0 갱신.
- **③ 10개 업종**: `generate_gridpoint_evidence.py` — `active_{code}_license_count_500m`를 CS100001~010 전부 산출(이전엔 CS100010 하나 하드코딩). `--industry-code` 인자 제거. `generator.params`에 `industries`·`industry_metric_scope`·`commercial_filter_basis` provenance 기록. `INDUSTRIES` 상수. `Counter(food_r)`로 업종별 집계.
- 재생성: all-seoul 250 레코드 schema 0, 잠실동 117 schema 0. 각 레코드 metrics 16개(기본 6 + 업종 10).
- 검증: 마포구 생성지점 10 → 조건부 6·주의 4(추천 0). 종로구 limit 40 → 생성지점 5개 조건부로 노출(스파스 지역 densification). 4 canonical mvp·sample 재실행 schema 0·real-seed tier 불변. `validate_harness` PASS.
- 잔여: `gridpoint_evidence.jsonl` metrics RAG 연결 여부(현재 audit-only), 행정동 전수, 원자료 checksum.

## 2026-09-02 gridpoint_evidence.jsonl → 후보 RAG evidence 연결 (사용자 요청)

- 실행한 요청: "지금 연결 실시" (감사 §다음조치 #3 — 이전엔 audit-only로 보류)
- `recommendation_pipeline.py`:
  - `load_generated_seeds`가 `seeds.json` + `gridpoint_evidence.jsonl`을 함께 읽음(`evidence_id` 매칭). seed에 `gen_evidence`·`gen_evidence_path` 부착.
  - `build_candidate`(synthetic + gen_evidence): evidence[]에 `active_{요청업종}_license_count_500m`·`active_food_license_count_500m` 2개 item(`spatial_grain=지점`·`source_type=derived`·source `data/인허가/음식점_인허가_서울.csv` + 생성 jsonl·`normalization=radius_license_count_by_industry`·interpretation·limitation에 "경쟁 규모 · fit_tier·정렬 미반영"). `context_notes`에 10개 업종 브레이크다운.
  - `feature_build.coverage.gridpoint_generated_evidence`·`source_freshness.gridpoint_license_context`·`sources` 반영. 매칭 실패 시 `missing_features` 기록.
  - **역·버스·아파트 metrics는 재사용 안 함** — 파이프라인이 실행 시점 원자료로 재계산(정본). 인허가 경쟁 metric만 생성 레코드에서.
- `rag-evidence-schema.json` normalization enum에 `radius_license_count_by_industry`. `SUPPORTED_INDUSTRY_NAMES` 상수 추가.
- 검증: 종로구 limit 40 → 합성 후보 5개에 인허가 evidence·context 연결, schema 0. 잠실동 `--include-generated-points` 30후보(합성 15) schema 0. 4 canonical mvp·sample 재실행 불변. `validate_harness` PASS.
- audit 문서 "추천 파이프라인 연결 상태" 갱신(미연결 → 연결 완료), spec §1-0.

## 2026-09-02 gridpoint RAG 연결 — 검토 지적 3건 수정 (GPT/Codex 검토 후속)

- 검토(`recommendation-quality-review.md`)에서 조건부 통과 + 4건 지적. 코드 3건 수정:
- **① 상대경로 provenance**: `load_generated_seeds`가 입력 경로를 `ROOT` 기준 resolve 후 `relative_to(ROOT)`(실패 시 원문). 상대·절대 입력 모두 `gridpoint_evidence.jsonl`이 `feature_build.sources`에 기록. 종로구 상대경로 실행으로 확인.
- **② run-manifest source_paths**: `run-manifest.json` top-level `source_paths`가 모든 후보 `feature_build.sources` 합집합 포함(생성 JSONL·인허가·R-ONE 등). 이전엔 pipeline `source_paths` + poi만.
- **③ 문서 상태 불일치**: `generated-gridpoint-seed-audit.md`·`data-inventory.md`·`data-usage-classification.md`·`README.md`의 "RAG 미연결/audit-only" → "반경 인허가 경쟁 metric은 evidence·context_notes에 연결(등급·정렬 미반영)"으로 통일. `gridpoint_evidence.jsonl` 판정 `audit-only` → `conditional`.
- **④ UI/백엔드 DTO 미검증**: 미해소 — handoff 다음실행 #14로 추적(CLI만 검증 가능).
- 검증: 4 canonical mvp + gridpoints run + sample 재실행 schema 0, `validate_harness` PASS. review 경계면 이슈 1·2·3 해소 표기.

## 2026-09-02 빅카인즈 시설·개발 뉴스 snapshot → 추천 RAG 연결 (GPT/Codex)

- `scripts/ingest_bigkinds_news.py` 신설: 다운로드된 `서울시 랜드마크 시설 계획20240101-20260901.xlsx`를 외부 패키지 없는 XLSX parser로 읽고 `data/뉴스/` JSONL + manifest로 정규화.
- 2,616행 → 분석제외 71행·URL 중복 23행 제거 → 2,522행. 본문은 저장하지 않음. 검색어·기간은 export 내부 필드가 없어 파일명/CLI 추정으로 manifest에 명시하고 원본 SHA-256 기록.
- `recommendation_pipeline.py`에 `NewsCatalog`·자치구/행정동 exact·통용명 alias 매칭 추가. `FC-51_뉴스_시설개발_자치구기사량`·`FC-51_뉴스_시설개발_행정동기사량`을 후보 `evidence[]`와 `context_notes`에 연결. `FC-51-news`는 미래신호의 중립 보조 맥락.
- 뉴스는 공식 도시계획사업·추진단계·수요·성공 outcome이 아니므로 `reasons`·`counter_evidence`·`fit_tier`·정렬에 사용하지 않음. `--no-news-context`로 비교 QA 가능.
- 문서 갱신: `data/뉴스/README.md`, `artifacts/handoff_bigkinds_news.md`, data inventory/acquisition, regional profile, candidate spec, RAG schema, final spec, `artifacts/30-review/recommendation-quality-review.md`.
- 검증: 잠실 19·연남 7·잠실 생성격자+POI 30 후보 schema 0; 뉴스 포함/제외 비교에서 ID·순서·등급·긍정/반대 근거·위치·신뢰도 변경 0. 구조 하네스 PASS.
- 잔여: query metadata 사람 대조, BigKinds API 자동 갱신, 공식 사업 단위 crosswalk, alias coverage·다른 검색어/지역 QA.

## 2026-09-02 네이버 뉴스 현재 시점 수동 snapshot → 추천 RAG 연결 (GPT/Codex)

- `scripts/ingest_naver_news_snapshot.py` 신설. Naver API HUB 뉴스 endpoint를 두 검색어(`서울시 랜드마크 시설 계획`, `서울시 재개발 계획`)로 수동 1회 호출하고 `data/뉴스/naver_news_snapshot.jsonl` + manifest를 생성했다.
- 현재 snapshot: 2026-09-02 21:58:13 KST, `display=100`·`sort=date`, 원시 200행 → URL exact 중복 14행 제거 → 186행. 제목·발행일·URL·파생 태그만 보존하고 description/body는 저장하지 않는다.
- `recommendation_pipeline.py`에 BigKinds·Naver snapshot 다중 catalog 로더를 연결했다. 네이버는 `FC-51_네이버뉴스_시설개발_자치구기사량`·`FC-51_네이버뉴스_시설개발_행정동기사량`으로 `evidence[]`·`context_notes`에만 연결하며 `reasons`·`counter_evidence`·`fit_tier`·정렬·예측모델에는 사용하지 않는다.
- **자동화 없음:** pipeline은 API를 호출하지 않고 JSONL snapshot만 읽는다. 새 결과는 수동 ingest 명령으로만 교체한다. `.env.example`에 endpoint·예산 필드와 이 운영 원칙을 기록했다.
- 문서 갱신: `data/뉴스/README.md`, `artifacts/handoff_naver_news_snapshot.md`, `artifacts/README.md`, `data-inventory.md`, `data-acquisition-sources.md`, `regional-characteristics-profile.md`, `candidate-selection-spec.md`, `final/recommendation-system-spec.md`, `recommendation-quality-review.md`.
- 검증: 잠실동 19 후보 네이버·빅카인즈 coverage 19/19, `schema_error_count=0`; 뉴스 포함/제외 ID·순서·등급·긍정/반대 근거·위치·신뢰도 변경 0; `validate_harness.py` PASS.
- 잔여: 기사 보도량과 공식 도시계획사업의 사업 단위 crosswalk, 다른 지역·검색어의 alias coverage. 뉴스 자동화·예약 갱신은 운영하지 않음.

## 2026-09-02 네이버 뉴스 검색어 단일화 (GPT/Codex)

- 사용자 요청에 따라 네이버 뉴스 snapshot을 기존 2개 검색어에서 `서울시 재개발` 단일 검색어로 교체했다.
- 현재 수동 snapshot은 원시·정규화 100행이며, `query_labels`도 단일 값만 가진다. 기본값(`DEFAULT_QUERIES`)과 수동 재실행 문서도 동일하게 단일 검색어로 고정했다.
- 파이프라인의 뉴스 사용 범위는 변경하지 않았다. 저장된 네이버 snapshot을 `FC-51-news` 중립 RAG 근거로만 읽고, 자동화·예약 갱신·등급·정렬 반영은 하지 않는다.

## 2026-09-03 뉴스 지역 매칭 텍스트 축소 (검토 지적 #1 수정, Claude Code)

- 실행한 요청: `data/뉴스` 권한 복구 후 뉴스 연결 재검토 → `recommendation-quality-review.md` 기록 + 발견 #1(지역 매칭 정밀도) 수정.
- 문제: `_find_terms`가 무경계 부분문자열 매칭이고 빅카인즈 `키워드`·`특성추출`·`위치`·`분류`·`기관`을 전부 지역 매칭에 사용 → 곁다리 지명 오탐(능동 5·항동 7·묵동 21·번동 21·길동 44건 상당수가 해당 행정동과 무관). 네이버도 `title+description` 매칭. 판정·정렬 미반영이라 등급은 불변이나 evidence 카드 숫자가 부풀려짐.
- 수정: 빅카인즈 지역 매칭 = 제목 부분문자열 + `위치` 필드 토큰 정확일치(`_match_tokens` 신규). `키워드`·`특성추출`·`분류`·`기관`은 지역 매칭 제외(주제·서울 scope 판정엔 유지). 네이버 = 제목만. `evidence[]` interpretation·limitation, 두 manifest `region_matching`, `data/뉴스/README.md` 갱신. 네이버 ingest `--query` help stale 문구도 수정.
- 재생성: 빅카인즈 재-ingest(2522행 불변), 네이버 JSONL 지역 태그 제목 기준 in-place 재계산. 오탐 능동 5→1·항동 7→1·길동 44→7. 진성 신호 유지(삼성동 220→212, 송파구 132→131). 잠실·연남 news on/off judgment 변경 0·정렬 동일·schema 0. 캐논 뉴스 run 4종 + no-news QA 재생성. `validate_harness.py` PASS.
- 잔여: 묵동 15·번동 13은 빅카인즈 `위치` 오추출 토큰이라 우리 쪽에서 제거 불가 — limitation 명시. `jamsil-coffee-news-both-mvp`는 없어진 네이버 쿼리 snapshot 기반이라 stale로 남김. #2~#6(토픽 no-op·freshness 미추적·row_count 합산·`generated_by` 하드코딩)은 기록만.
- 결함 케이스: `fault-cases.md`에 F29(뉴스 오용) 추가.

## 2026-09-03 뉴스 snapshot 최신성 추적 (검토 지적 #3 수정, Claude Code)

- 실행한 요청: #3(news freshness) 추적 추가.
- 정정: `source_freshness`(후보별)에 `bigkinds_news`·`naver_news_snapshot` 항목은 이미 있었으나(초기 진술의 위치 착오), 값이 정적이라 수동 snapshot의 경과일·stale 신호가 없었다.
- 수정 (`recommendation_pipeline.py`·`rag-evidence-schema.json`): `news_snapshot_age()` + `NEWS_SNAPSHOT_STALE_DAYS=45`. `observed_end_period` 기준 실행일까지 경과일로 `snapshot_age_days`·`is_stale` 계산. `source_freshness`에 `retrieved_at_utc`·`snapshot_age_days`·`is_stale`·`stale_threshold_days` 추가, `update_cadence="manual_snapshot"`. stale이면 `context_notes` 1줄(판정·정렬 미반영) + `coverage-summary.json` `news_context.any_stale`. 스키마 `update_cadence` enum + 신규 4필드 허용.
- 검증: 현재 snapshot(age 1~2일) `is_stale=false` schema 0. 임계 0 강제 시 stale 경로 정상 schema 0. news on/off judgment 변경 0. `validate_harness.py` PASS. 캐논 뉴스 run 재생성. F29에 stale 오용 항목 추가.

## 2026-09-03 뉴스 검토 지적 #2·#4·#5 수정 (Claude Code)

- 실행한 요청: #2(토픽 필터 no-op·metric 이름 과장)·#4(row_count 합산) 수정.
- #2: metric 이름 `FC-51_뉴스_시설개발_{자치구,행정동}기사량` → `FC-51_뉴스_{자치구,행정동}기사량`(빅카인즈·네이버 4종). `NewsCatalog.topic_match_count` + `news_context_for_candidate`가 `snapshot_total`·`topic_match_rate` 반환. evidence·context_notes를 "시설·개발·정비 주제 snapshot(N건, 주제적합률 P%) 중 {지역} 매칭 M건 · 주제태그 {상위4}"로 재작성 — `topic_match_rate≥0.95`면 "주제로 걸러낸 부분집합 아님" 명시. `coverage-summary.json`에 source별 `topic_match_count`·`topic_match_rate` + `any_topic_filter_effective`.
- #4: `news_context` 최상위 합산 `raw_row_count`·`normalized_row_count` 제거 → `row_counts_note`로 대체. source별 수는 `sources[]`·`source_freshness`에 유지.
- #5: 네이버 ingest `--query` help "기본 2개" → 실제 1개(이전 커밋에서 이미 수정, 문서 반영).
- 검증: 잠실·연남·역삼1동 news on/off judgment 변경 0·정렬 동일·schema 0. `validate_harness.py` PASS. `sample_jamsil_coffee` 16건 0오류. 캐논 뉴스 run 7종 재생성. **남은 것: #6(`generated_by` 하드코딩)만 기록.**

## 2026-09-03 상권 유형 분류기 — 신도시형 vs 골목상권형 (미래가치 존속가능성 재정의 1단계, 사용자 요청)

- 배경: 기능요구서(REQ-LOC) 검토 중 미래가치 존속가능성 재정의 필요. 사용자 가정 — 상주·직장인구는 배후 상권이 성숙(골목형)한지 미성숙(신도시형)한지에 따라 존속 신호로서 의미가 반대. FC-03/04 단변량 신호가 약하고 업종별 부호 뒤집힌 이유가 이 confounding일 가능성.
- 산출: `scripts/build_site_typology.py` → `output/site_typology/typology_{상권,행정동}.csv` + manifest + figures. `artifacts/10-analysis/site-typology.md`.
- 방법: **가중합산 아님 — 8개 축(영업개월·개업률·인구당점포·점포밀도·인구당매출·프랜차이즈·단지규모·K-apt세대수) 서울 3분위 → 신도시/골목 방향 투표 + 변화지표 LL/HH·HL ±1표. 순표차 ≥2면 라벨.** 상권은 서울시 구분 '골목상권'(1,090)만 판정, 발달상권·전통시장·관광특구는 별도.
- 결과(20261): 상권 골목상권형 347 / 혼합형 415 / 신도시형 272 / 배후주거_희박 45. 행정동 110/145/106/64.
- 사니티: 신도시형 비중 강서(마곡) 0.49·강동(고덕) 0.37 상위, 종로 0.05·용산 0.07 하위 — 실제 서울 상권 발달 패턴과 일치.
- 데이터 이슈 해결: 서울시 상주인구 파일 `아파트_가구_수`가 전 상권 0 → 반경 250m K-apt(`상권_아파트접근성.csv`)로 대체.
- 라벨 이름 주의: '신도시형' = 계획신도시만이 아니라 재개발·저개발 주거지 등 "상권 미성숙" 패턴 통칭. destination 상권(성수카페거리 등)은 이 축과 안 맞음(소수, 혼합형이 흡수).
- **다음(2단계)**: 상권 유형별 층화로 상주·직장인구 수준 ↔ 인허가 1년 생존·폐업률 조건부 실측. 통과 시 존속가능성 근거 승격, 아니면 "가정(미검증)" 유지. 파이프라인 연결은 그 뒤.

## 2026-09-03 상권 유형별 인구↔존속 조건부 검증 (2단계) — 가설 미지지

- 산출: `scripts/analyze_typology_survival.py` → `output/feature_validation/typology_survival_*`. `site-typology.md` 2단계 절.
- 방법: 상권 pooled, 유형 층화 Spearman(상주·직장인구 pctl ↔ churn_ratio·close_r_store·surv_1y·net_growth). 저인구 상권 outcome degeneracy는 표본필터(opens≥5·n_cohort≥10·food_store_n≥20)로 제거. scipy 없어 |ρ|≥0.2를 신호로.
- 결과: **(1)** baseline에서 신도시형이 4개 outcome 모두 나쁨(churn 1.12 vs 골목 1.06, 폐업률 2.88 vs 1.94, 생존율 88.9 vs 91.8, 순증 −7.1 vs −3.8) — **그러나 자치구 통제 시 신도시형이 더 나쁜 자치구는 7/18, within median 차 −0.044** → 대부분 자치구 구성 효과(신도시형이 강서·관악·동작 등 존속 나쁜 구에 몰림). **(2)** 12개 층화 상관 중 |ρ|≥0.2는 1개(골목형 상주인구↔close_r_store ρ+0.217 = 가설과 반대 방향). 직장인구는 어디서도 신호 없음. **(3)** 유형이 인구↔존속 관계를 조건화하지 않음.
- **결론**: 사용자 가정 미지지. 상주·직장인구 수준을 존속 근거로 승격 안 함(`context_notes` 전용, 파이프라인 미연결 유지). 상권 유형 라벨도 counter_evidence 승격 안 함 — 배경 서술로만. `feature-evidential-value.md` §9 FC-03/04를 "서술만 — 존속 근거 승격 불가"로, 프로파일 §8-2·§8-1 갱신.
- 순환성 주의: 유형 분류 축(영업개월·개업률) 일부가 churn류 outcome과 원천 공유. surv_1y·net_growth는 독립이며 거기서도 자치구 통제 시 유형 순효과 약함.
- 산출물 보존: `typology_상권.csv`는 배경 서술·향후 매출 outcome 확보 시 재분석에 재사용.

## 2026-09-03 팀 공유 문서 — 기능요구서 입지추천 파트 ↔ 하네스 대응 (사용자 요청)

- 산출: `docs/입지추천_기능요구사항_하네스대응.md`. `docs/architecture/README.md`에 링크 추가.
- 내용: 「기능별 요구사항 정의서 v0.2」의 REQ-HQ-16~20·REQ-LOC-01~10·REQ-DATA 일부를 하네스와 대조. ① 하네스 동작 요약(입력→지점 후보→3분류 판정) ② REQ ID별 대응표(🟢지원/🟡조정필요/🔴갭) ③ 확정 설계 결정 6건 + 각 근거 문서(D1 가중합산 폐기·D2 지점 후보·D3 매출 fallback 금지·D4 뉴스/POI context 전용·D5 계단식 증가율 금지·D6 상주·직장인구 존속 근거 승격 안 함) ④ 알려진 한계 4건(매물주소 없음=미해결 갭·매출 커버리지 54%·성공 outcome 부재·미래가치 검증 신호 1개) ⑤ 미결 팀 결정 항목(기능요구서 §9 매핑) ⑥ 팀이 만들 것 ⑦ 정본 계약 문서 포인터.
- 핵심 메시지: 명세의 "가중 합산 입지 점수"(REQ-LOC-01)와 하네스 evidence-first 접근이 정면 충돌 → 미결 #1을 "가중합산 폐기, 근거 판정으로 재정의"로 합의하는 것이 선행 과제. "정확한 매물 주소"(REQ-HQ-20)는 격자 합성 좌표로 **드러냈을 뿐 채우지 못한 갭**(미결 #7).

## 2026-09-03 서빙 파이프라인 PostgreSQL 연결 (`--source db` 기본, Claude Code)

- 실행한 요청: "서빙경로에 db 연결". `recommendation_pipeline.py` 가 `data/` CSV·shp 를 직접 읽던 것을 Docker PostgreSQL(`ideaton-db`, 55432) 조회로 전환. `build_candidate` 이하 판정·근거·스키마 로직은 미변경 — 로더 반환 자료구조를 100% 동일하게 유지.
- **DB로 전환한 것**(스코어링 입력): 영역 폴리곤(`location.area`), 분기 팩트 8종(`load_scope_index` → `store/sales/flow_quarter` + 상권변화지표는 `context.metric_snapshot` 피벗), 전 업종 지역 배경(`build_environment` → 신설 `location.area_store_totals`).
- **두 모드 모두 파일 유지**(경량 스냅샷·파생·보조 맥락): seed(아파트·역·POI), 반경 지표(역·버스·아파트), 네이버 트렌드/계절성, 뉴스, 임대료·R-ONE crosswalk, POI 컨텍스트 manifest, 생성 격자. DB `anchor_point` 는 CSV 대비 자체 dedup(역 412→411 등)이 있어 파일 그대로 두는 게 정합적.
- 신규: `db/001_location_schema.sql` 에 `location.area_store_totals`(전 업종 합계, 66,465행); `migrate_postgres.py` `load_area_store_totals`(연도폴더 중복 시 **분기 연도와 같은 폴더 우선** tie-break — `build_environment` 의 `data/점포/{연도}년/` 규칙과 일치); `scripts/serving_db.py`(psql 서브프로세스 `COPY … TO STDOUT CSV`); `recommendation_pipeline.py` `FileSource`/`DbSource` + `--source {db,files}` + `--db-url`. `run-manifest.json` 에 `data_source` 블록.
- **버그 수정(파생)**: `all_flow_density` 를 `pct()`(bisect_left) 에 넘기기 전 정렬하지 않아 FC-01 유동밀도 서울 분위가 소스 행 순서에 의존했음 → `sorted()`. `context_notes`·`evidence.seoul_percentile` 만 영향(FC-01 은 판정·정렬 미반영), `fit_tier` 불변. files 모드 기존 산출물과의 차이는 이 한 줄뿐.
- 검증: A/B(`--source files` vs `--source db`) 잠실·연남·역삼1동·강남전체·종로전체·관악, `--include-poi`·`--no-news-context`·`--include-generated-points` 포함 **`candidates.json` 바이트 동일**. `entry_health_v1`·`fit_tier`·`reasons`·`counter_evidence` 전부 일치. DB 중단 시 `PipelineError` 로 명확 실패 + `--source files` 정상. `validate_harness.py` PASS. `.env` 에 `DATABASE_URL`(55432, gitignore), `.env.example` 갱신.
- `DATABASE_URL` 은 `migrate_postgres*.py` 도 함께 읽는다(psql_args 가 이미 우선 사용) — 이제 이식·서빙 모두 Docker 대상.
- `DbSource._provenance`: 근거 `source_path` 는 원천 파일이 있으면 그 경로(files 모드와 바이트 동일), 없으면 `"…*.csv (PostgreSQL 적재본)"` 논리 참조로 폴백 — 대용량 분기 CSV 를 제외한 배포 환경에서도 `--source db` 동작.
- 잔여: 뉴스 manifest·rone 상권 grain·naver 계절성의 DB 적재(완료 시 파일 의존 0), API 전환 시 psql 서브프로세스 → `psycopg` + 풀. `output/recommendation_runs/` 캐논 산출물은 별도 뉴스 커밋 이후 stale 상태 — 재생성은 별도 작업.

### 2026-09-03 (이어서) 서빙 파일 의존 완전 제거 — `--source db` 는 데이터 파일 0 (Claude Code)

- 실행한 요청: "이미 data 는 다 Docker 에 담았는데 왜 파일을?" → 남은 6개 파일 로더를 DB 로 전환.
- 신규 테이블 4개 + `migrate_postgres_context.py` 적재 함수: `context.news_manifest`(뉴스 수집 메타 blob, 2행), `context.rent_index`(R-ONE grain 보존 — 상권/권역/서울전체, 2,932행), `context.naver_seasonality`(계절성 파생 전체, 459행 중 업종 10), `context.anchor_snapshot`(아파트·역·버스·카카오 POI **원본 CSV 행 verbatim, 파일 순서 보존**, 16,033행 = CSV 행수 정확히 일치).
- `DbSource` 에 `seeds`·`radius_points`·`naver_attention`·`news_catalogs`·`rent`·`crosswalk` 추가. `anchor_point` 는 spatial join 용 dedup 이라 seed·반경은 `anchor_snapshot` 을 읽어 "CSV 행 순회" 를 재현.
- `NAVER_INDUSTRY_NAMES` 상수(트렌드 CSV 업종명 "한식음식점" ↔ 코드). `_naver_attention_compute`·`merge_seeds`·`_points_from_rows` 로 파일/DB 공통 코어 추출.
- 파생 수정: `load_naver_industry_attention` 이 `naver_seasonality` 파일 경로를 provenance 에서 제거(현재 산출 무기여) → 두 모드 `source_paths` 일치.
- 검증: A/B(`--source files` vs `--source db`) 10종 — 잠실·연남·역삼1동·강남전체·종로전체·서교동·`--include-poi`·`--include-poi-context`·`--include-generated-points`·`--no-news-context` — **`candidates.json` 바이트 동일**. `--source db` 는 `data/` 디렉터리 없이 동작 확인(rag-evidence-schema.json + `--include-*` 옵션 파일만 예외).
- 병행 작업 감지: 같은 시각 `scripts/llm_{input_planner,explanation,runtime}.py` 신설 + `recommendation_pipeline.py` 가 이를 import(LLM 입력 해석·설명 레이어). 본 DB 작업과 충돌 없이 병합됨(`--llm-mode offline` A/B 통과). **PR 은 이 병행 편집이 정리된 뒤 진행 필요.**
