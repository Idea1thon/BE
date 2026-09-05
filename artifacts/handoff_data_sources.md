# 데이터·API 출처 인수인계

> 기준일: 2026-09-03 (Asia/Seoul)  
> 작업 주체: **GPT(Codex)**  
> 목적: Claude Code가 저장소 전체를 다시 탐색하지 않고 현재 보유 데이터, 외부 출처, API 키·호출 상태와 사용 경계를 이어받도록 한다.

## 1. 현재 결론

- `data/`는 서울시·공공기관·민간 API의 **로컬 스냅샷과 파생 집계본**이다. 추천 실행 시 외부 API를 실시간 호출하지 않는다.
- 추천 파이프라인은 `scripts/recommendation_pipeline.py`가 로컬 CSV/JSONL과 사전 계산된 crosswalk를 읽어 증거 중심 후보를 만든다. API 수집은 `scripts/ingest_*.py`, `scripts/*api.py`의 별도 작업이다.
- 후보는 상권 폴리곤 자체가 아니라 좌표를 가진 아파트·역 중심의 지점이다. 카카오 POI와 격자 생성점은 선택적 보조 seed/context이며 매물·공실·성공확률이 아니다.
- `VWORLD_API_KEY`, `DATA_GO_KR_SERVICE_KEY`, `NAVER_CLIENT_ID/SECRET`, `RONE_API_KEY`, `KAKAO_REST_API_KEY`는 `.env`에 설정된 상태다. 키 값은 이 문서와 로그에 기록하지 않는다.
- 도로명주소 좌표제공 API는 확인만 완료했다. `JUSO_COORD_API_KEY`는 미발급이며, 배포 URL/IP를 확보한 뒤 신청해야 한다. 신청서에는 URL/IP와 신청인 정보가 필요하므로 현재 자동 신청·제출하지 않았다.

## 2. 현재 보유 데이터

상세한 파일별 품질 수치와 결합 위험은 [`10-analysis/data-inventory.md`](10-analysis/data-inventory.md), 사용 분류는 [`10-analysis/data-usage-classification.md`](10-analysis/data-usage-classification.md), 원천별 조사 링크는 [`10-analysis/data-acquisition-sources.md`](10-analysis/data-acquisition-sources.md)를 기준으로 한다.

### 2-1. 핵심 분석 데이터

| 데이터 | 현재 경로·규모 | 공간 grain / 업종 | 기간·상태 | 추천 사용 경계 |
| --- | --- | --- | --- | --- |
| 영역 | `data/영역/` — 상권 CSV·SHP, 상권배후지 CSV·SHP, 행정동 CSV·SHP | 상권 1,650 / 배후지 1,071 / 행정동 425, 업종 없음 | 정적 경계 | 폼 지역 필터, PIP, 겹침 crosswalk의 공간 정본 |
| 점포 | `data/점포/` — 2021~2026 연도별 18파일 | 상권·배후지·행정동, 업종 구분(타깃 10개 포함) | 2021Q1~2026Q1 분기 | 업종별 점포수·개업·폐업·프랜차이즈의 주력 근거. 연도파일 정규화 후 사용 |
| 추정매출 | `data/추정매출/` — 2021~2026 연도별 18파일 | 상권·배후지·행정동, 업종 구분 | 2021Q1~2026Q1 분기 | 수요·매출 구성·요일·시간·고객군. 상권 커버리지 1,577/1,650이므로 결측과 0 구분 |
| 길단위인구 | `data/길단위인구/` — 3파일 | 상권·배후지·행정동, 업종 없음 | 2021Q1~2026Q1 분기 | 유동 수준·시간/요일·연령/성별 구성. 분기 변화 근거로 사용 가능 |
| 상주인구 | `data/상주인구/` — 3파일 | 상권·배후지·행정동, 업종 없음 | 2021Q1~2026Q1 표기지만 계단식 갱신 | 최신 수준의 주거 배경만 조건부 사용. 분기 추세·성장률 금지 |
| 직장인구 | `data/직장인구/` — 3파일 | 상권·배후지·행정동, 업종 없음 | 2021Q1~2026Q1 표기지만 계단식 갱신 | 최신 수준의 직장 배경만 조건부 사용. 분기 추세·성장률 금지 |
| 상권변화지표 | `data/상권변화지표/` — 상권·행정동 2파일 | 상권·행정동, 전체 업종 통합 | 2021Q1~2026Q1 | 서울 공식 LL/LH/HL/HH 배경 위험 신호. 업종별 지표로 확장하지 않음; 배후지 버전 없음 |

### 2-2. 입지·보조·외부 스냅샷 데이터

| 데이터 | 현재 경로·규모 | 원천·기준 시점 | 상태와 사용 |
| --- | --- | --- | --- |
| 음식점 인허가 패널 | `data/인허가/음식점_상권분기_패널.csv` | 행안부·공공데이터포털 15045016·15006730 서울 필터, 2021Q1~2026Q3 | `audit-only`. 개·폐점일 기반 생존/greenfield 보조. 점포수는 교차검증됐지만 폐업률 대체 금지 |
| 외국인 생활인구 | `data/외국인생활인구/` 2파일 | 서울 열린데이터광장 OA-14992(장기)·OA-14993(단기), 2023Q1~2026Q3 | 행정동 외국인 배경·근사 비율. 2026Q3 부분분기 표시 |
| 자치구 고용률 | `data/고용률/자치구_고용률_반기.csv` | KOSIS 지역별고용조사, 2021H1~2026H1 | 자치구 배경값. 상권·동에 직접적인 동 단위 값으로 표현하지 않음 |
| 임대료·공실률 원본 | `data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv` | 한국부동산원 상업용부동산 임대동향조사, 2021Q1~2025Q4, 권역 | 권역 비용 배경. 특정 주소 월세·현재 공실로 사용하지 않음 |
| R-ONE 임대동향 이식본 | `data/임대료/R-ONE_임대동향_분기.csv` + `output/crosswalks/` | 한국부동산원 R-ONE, 2024Q1~2026Q2, R-ONE 상권 72·권역·서울 | 임대료/임대가격지수 보조. crosswalk `join_eligible=yes`만 자동 결합, review는 보류; CSV에는 공실률 미이식 |
| 도시계획·정비사업 | `data/도시계획사업/` 4파일 | 서울 도시계획포털 UQ120 + 정비사업 정보몽땅, 2026 스냅샷 | 재개발·정비·역세권 사업의 지역 배경. 추진단계 스냅샷이지 미래 성공·확정 사건이 아님 |
| 지하철 역 | `data/도시철도역사/역사정보_서울.csv`, `상권_역세권.csv` | 국가철도공단 15013205, 2026-06-30 스냅샷 | 후보 anchor 및 FC-07. 출구 좌표·배차·예정역 위치는 없음 |
| 지하철 보조·계획 | `역출입구_요약.csv`, `도시철도망계획_노선.csv`, `도시철도망계획_자치구.csv` | 서울교통 데이터·제2차 서울 도시철도망 구축계획(2020-11) | 출입구 수·자치구 계획 신호. 정거장 확정 위치로 표현 금지 |
| 버스정류소 | `data/버스정류장/` 2파일 | 서울시 OA-15067 2026-08 + 국토부 15067528 인접권 | FC-07 수·유형 및 접근성. 노선·배차 간격 없음 |
| 아파트 단지 | `data/공동주택/아파트단지_서울.csv`, `상권_아파트접근성.csv`, `_geocode_cache.json` | 국토부 K-apt 15057332, 면적 15073269, GIS 15083092, VWorld Search | 3,396단지, VWorld 좌표 89.7%. 후보 anchor·500m 세대수; 의무관리 위주라 소형 빌라·연립 누락 |
| 카카오 POI | `data/카카오POI/` — 잠실역 90행, 잠실동 context 343행 + manifest | Kakao Local REST API, 2026-09-02 스냅샷 | 잠실 파일럿만 부분 이식. 선택적 seed/context. 입력 카테고리의 완결이지 서울 상가 전체 100%가 아님 |
| 네이버 검색 트렌드 | `data/네이버트렌드/` 3 월계열 + 요약, `data/ontology/업종_검색키워드.json` | Naver DataLab/NCP HUB, 2021-01~2026-08 | 10업종·구·동 상대지수. 최신월·최근 3개월 원계열은 RAG context; 계절 보정·단일 급등은 가점/등급/정렬 금지 |
| 뉴스 스냅샷 | `data/뉴스/` BigKinds 2,522행 + Naver 100행 | BigKinds export(2024-01~2026-09)와 Naver News API 현재 검색 `서울시 재개발`(2026-09-02) | 제목·일자·URL·파생 지역/주제 태그만 보존. 공식 사업 상태·성공·수요·공실이 아니며 RAG `evidence/context_notes` 보조만 |
| 격자 생성 근거 | `output/generated_evidence/` — 서울 25구 250개 + 잠실동 별도 | 공개 인허가·교통·아파트를 100m 격자에 집계한 `project_generated` | 실제 상가·매물·주소가 아닌 합성 좌표. `synthetic_anchor=true`, `precision=지점(생성)`, `listing_url/address_point=null`; 추천 등급 상한 조건부 |

### 2-3. 참조·결합 산출물

- `data/ontology/업종_검색키워드.json`: 10개 독립 업종의 세부 음식·검색어·인허가 업태 매핑 참조표.
- `output/crosswalks/`: 상권↔행정동·배후지 및 R-ONE proxy 결합표. 겹침 관계는 면적가중 다중소속으로 보존한다.
- `data/*manifest.json`, `output/*/run-manifest.json`: 수집시각·검색조건·원본 행수·중복·커버리지·실행 입력을 추적한다.
- `artifacts/10-analysis/feature-evidential-value.md`: 미래 정답/성공 outcome 부재와 피처 설명력 감사 결과. 운영 성공확률 모델의 근거로 사용하지 않는다.

## 3. API·외부 출처 연결 상태

상태 의미: `실호출·이식`은 현재 키로 API 호출해 로컬 산출물을 만든 것, `실호출·스냅샷`은 API를 수동 호출해 결과를 저장한 것, `파일 이식`은 API 키 없이 받은 공식 파일을 정규화한 것, `미발급/미연결`은 현재 추천 파이프라인에 연결하지 않은 것이다.

| 출처/API | 공식 요청 경로·자료 | 환경변수 | 현재 상태 | 로컬 결과·코드 |
| --- | --- | --- | --- | --- |
| 서울시 상권분석서비스 | 상권·배후지·행정동 영역/점포/매출/인구/변화지표 공식 파일 | 없음 | 파일 이식·핵심 | `data/`, 정규화·감사 `scripts/` |
| 공공데이터포털·국토부 K-apt | `AptListService4/getSidoAptList4`, 서비스 15057332 | `DATA_GO_KR_SERVICE_KEY` 설정 | 실호출·이식 완료 | `data/공동주택/`, `scripts/ingest_apartment_complex.py` |
| VWorld 지오코딩/Search | `/req/address`, `/req/search` | `VWORLD_API_KEY` 설정 | 정방향·역방향 실호출 검증 완료 | `scripts/geocode.py`, 아파트 좌표 캐시 |
| 서울 열린데이터광장 | OA-14992·OA-14993 외국인, OA-15067 버스정류소 | `SEOUL_OPENAPI_KEY` 미설정 | 공식 파일 스냅샷 이식 | `data/외국인생활인구/`, `data/버스정류장/` |
| 공공데이터포털·행안부 식품 인허가 | 15045016 일반음식점, 15006730 휴게음식점 | 별도 서비스키를 현재 런타임에 사용하지 않음 | 공식 파일 이식·패널 집계 | `scripts/ingest_food_license.py`, `data/인허가/` |
| KOSIS 지역별고용조사 | 시군구 주요고용지표 | `KOSIS_API_KEY` 미설정 | CSV 이식 완료 | `data/고용률/` |
| 서울 도시계획포털·정비사업 정보몽땅 | UQ120 SHP, 사업장목록 XLS | 없음 | 공식 파일 이식 완료 | `data/도시계획사업/` |
| 국가철도공단 | 도시철도역사 15013205 | 없음 | 공식 XLSX 이식 완료 | `data/도시철도역사/`, `scripts/ingest_subway_stations.py` |
| 국토부 전국 버스정류장 | 15067528 파일 | 없음 | 경계 인접 자료만 이식 | `data/버스정류장/` |
| Naver DataLab/NCP HUB | `https://naverapihub.apigw.ntruss.com/search-trend/v1/search` | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 설정 | 실호출·74회 이식 완료 | `scripts/naver_datalab.py`, `data/네이버트렌드/` |
| Naver News API HUB | `https://naverapihub.apigw.ntruss.com/search/v1/news` | 동일 Naver 키 설정 | 2026-09-02 현재 시점 수동 1회 snapshot. 자동화 없음 | `scripts/ingest_naver_news_snapshot.py`, `data/뉴스/naver_news_snapshot*` |
| BigKinds | 뉴스 검색 export; API는 별도 승인 | `BIGKINDS_API_KEY` 미설정 | 사용자 제공 export 정규화 완료 | `scripts/ingest_bigkinds_news.py`, `data/뉴스/bigkinds_news_*` |
| 한국부동산원 R-ONE | `https://www.reb.or.kr/r-one/openapi/SttsApiTbl.do`, `SttsApiTblData.do` | `RONE_API_KEY` 설정 | 표 목록·임대료·공실률 실호출 검증. 운영은 CSV 스냅샷 유지 | `scripts/rone_api.py`, `data/임대료/`, `handoff_rone_api.md` |
| Kakao Local REST | keyword/category/coord2address API | `KAKAO_REST_API_KEY` 설정 | 실호출·잠실역/잠실동 파일럿 완료. 쿼터 가드 적용 | `scripts/kakao_local.py`, `data/카카오POI/` |
| Kakao JavaScript 지도 | 지도 타일·마커 표시용 | `KAKAO_JAVASCRIPT_KEY` 미설정 | 화면 표시용으로 미연결. REST POI와 별도 | 백엔드 POI 수집에는 불필요 |
| 도로명주소 좌표제공 팝업 API | `https://business.juso.go.kr/addrlink/addrCoordUrl.do`; 신청 화면 [`jstCoordApiPop`](https://business.juso.go.kr/jst/jstCoordApiPop) | `JUSO_COORD_API_KEY` 미발급 | 배포 URL/IP 확보 전 신청 보류. 승인키는 검색 API와 별도. 현재 VWorld가 주소·좌표 역할을 수행 | `.env.example` 슬롯만 추가; 별도 래퍼·추천 연결 전 |

## 4. 추천 사용 분류

| 분류 | 현재 항목 | 운영 의미 |
| --- | --- | --- |
| `core` | 영역, 점포, 추정매출, 길단위인구 | 후보의 지역·업종·수요·경쟁 근거. 결합 전 연도파일 정규화 필수 |
| `conditional` | 상주/직장인구, 외국인, 임대료·R-ONE, 도시계획·정비, 역·버스·아파트, Kakao POI, Naver trend/news, 고용률 | 지역 특성·입지 맥락·반대 근거. grain·최신성·스냅샷 한계를 카드에 표시 |
| `audit-only` | 음식점 인허가 패널 | 개·폐점일/생존 분석과 점포수 교차검증만. 폐업률을 서울시 점포 데이터 대신 사용하지 않음 |
| `project_generated` | 격자 생성 seed/evidence | 데모·공간 후보 보조. 실제 장소명·주소·매물처럼 표시하지 않음 |
| `exclude/missing` | 개별 상가 임대매물·현재 공실·월세/보증금·면적/주차, 실제 SNS, 성공 outcome/수익·손익, 내국인 생활인구 분모, 지하철 출구 좌표·배차, 소형 빌라·연립 전수 | 현재 추천이 확정하거나 생성할 수 없다. `unsupported`/`missing_features`로 남긴다. |

## 5. 현재 API 키 상태와 다음 작업

### 설정된 키

실제 값은 출력·문서·커밋에 넣지 않는다.

```text
DATA_GO_KR_SERVICE_KEY   SET
VWORLD_API_KEY           SET
NAVER_CLIENT_ID          SET
NAVER_CLIENT_SECRET      SET
RONE_API_KEY             SET
KAKAO_REST_API_KEY       SET
```

### 미설정·보류

```text
SEOUL_OPENAPI_KEY        미설정 (현재는 공식 파일 스냅샷 사용)
KOSIS_API_KEY            미설정 (현재는 CSV 사용)
BIGKINDS_API_KEY         미설정 (export 사용)
JUSO_COORD_API_KEY       미발급 (배포 URL/IP 필요)
KAKAO_JAVASCRIPT_KEY     미설정 (지도 화면에만 필요)
```

주소기반서비스를 도입할 때의 순서는 다음과 같다.

1. 서비스 배포 URL 또는 호출 IP를 확정한다.
2. [주소정보 API 연계 신청 화면](https://business.juso.go.kr/jst/jstCoordApiPop)에서 `좌표제공·팝업 API`를 신청한다. 업체/시스템/URL(IP)과 신청인 정보가 필요하므로 사용자가 직접 입력·본인인증·제출한다.
3. 승인된 좌표제공용 `confmKey`를 로컬 `.env`의 `JUSO_COORD_API_KEY`에 입력한다.
4. 팝업 API는 프론트엔드의 `returnUrl` callback에 맞춰 적용한다. `entX/entY`는 UTM-K(GRS80)이므로 내부 EPSG:5181 또는 WGS84로 변환하는 별도 로직을 검증한다.
5. 실제 매물 주소가 아닌 후보 anchor 주소 보강에만 사용하고, API 승인키가 생겼다고 매물·공실 데이터가 생기는 것으로 해석하지 않는다.

## 6. 진입 파일

1. 이 문서
2. [`artifacts/README.md`](README.md)
3. [`10-analysis/data-inventory.md`](10-analysis/data-inventory.md)
4. [`10-analysis/data-usage-classification.md`](10-analysis/data-usage-classification.md)
5. [`10-analysis/data-acquisition-sources.md`](10-analysis/data-acquisition-sources.md)
6. [`scripts/recommendation_pipeline.py`](../scripts/recommendation_pipeline.py)
7. API별 상세: [`handoff_kakao_poi.md`](handoff_kakao_poi.md), [`handoff_naver_news_snapshot.md`](handoff_naver_news_snapshot.md), [`handoff_rone_api.md`](handoff_rone_api.md)

이 문서의 출처·상태·사용 경계 기록은 **GPT(Codex)가 2026-09-03에 작성했다.**
