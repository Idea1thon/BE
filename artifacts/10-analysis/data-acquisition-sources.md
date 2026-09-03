# 필요 데이터와 확보 출처

- 작성: 2026-09-01 (웹 조사)
- 목적: `regional-characteristics-profile.md`의 inactive feature 슬롯과 `data-catalog-contract.md`의 `missing` 행을 활성화하기 위한 출처 목록
- 활성화 절차는 프로파일 6절(이식 규약)을 따른다. 각 출처는 감사(`auditing-location-data`) → catalog 승격 → 프로파일 재빌드 순.

## 좌표계 정리 (먼저 통일 필요)

| 데이터 | 좌표계 | 비고 |
| --- | --- | --- |
| 서울 상권분석서비스 영역 shp (보유) | **EPSG:5181** (GRS80 중부원점, false_northing 500000) | 이식 스크립트가 `encoding="utf-8"` 로 읽음 |
| LOCALDATA 인허가 · UQ120 도시계획 | **EPSG:5174** (Bessel 중부원점TM) | 상권 shp와 다름 → pyproj 5174→5181 재투영 (검증됨) |
| 생활인구 · 도시철도역사 · 버스정류소 | **EPSG:4326** (WGS84) | pyproj 4326→5181 (검증됨) |
| GIS건물통합정보 (`AL_D010_11_*`) | **EPSG:5186** (Korea 2000 중부원점 2010, false_northing **600000**) | `.prj` 확인. 5181 과 원점 다름 → 5186→5181 재투영 필요. dbf 인코딩 cp949 |
| 한국관광 데이터랩 | 지역(시군구) 집계, 좌표 없음 | 코드 매칭 |

→ 파이프라인에 좌표계 감지·재투영 단계 추가. 상권 폴리곤 기준(5181)으로 통일.

## P1 — 지금 이식 가능성 높음 (무료 공공데이터, grain 맞음)

### FC-06 외국인 방문 비율

| 출처 | 내용 | grain / 갱신 | 접근 |
| --- | --- | --- | --- |
| 서울 열린데이터광장 [행정동 단위 서울 생활인구(장기체류 외국인)](https://data.seoul.go.kr/dataList/OA-14992/S/1/datasetView.do) (OA-14992), [단기체류 외국인](https://data.seoul.go.kr/dataList/OA-14993/S/1/datasetView.do) (OA-14993) | KT 기반. 행정동×시간대×성·연령. 단기체류=관광 성격 | 행정동 / 시간별·일별 | 무료, API+CSV |
| 서울 열린데이터광장 [자치구 단위 생활인구(장기체류 외국인)](https://data.seoul.go.kr/dataList/OA-15440/S/1/datasetView.do) (OA-15440) | 자치구 집계 | 자치구 | 무료 |
| 한국관광공사 [빅데이터 지역별 방문자수](https://www.data.go.kr/data/15101972/openapi.do) (공공데이터포털 15101972) | 이동통신 기반 외지인+외국인 방문자수 | 시군구 / 월 | 무료 API 신청 |
| [한국관광 데이터랩](https://datalab.visitkorea.or.kr/datalab/portal/loc/getAreaDataForm.do?SGG_CD=11) | 방문자 성·연령 분포, 이동통신·카드 | 시군구 | 회원가입, 화면·다운로드 |

→ **OA-14992/14993이 최적**: 행정동 grain·시계열·무료. 상권↔행정동은 `output/crosswalks/crosswalk_trdar_dong.csv`로 연결.

### FC-53 자치구별 고용률

| 출처 | 내용 | grain / 갱신 | 접근 |
| --- | --- | --- | --- |
| [KOSIS 지역별고용조사 — 시군구 주요고용지표](https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=INH_1DA7014S_01) | **고용률**·실업률·경활률 | 시군구(서울 25구) / 반기(상·하반기) | 무료, KOSIS OpenAPI |
| [서울시 고용지표 통계](https://data.seoul.go.kr/dataList/59/S/2/datasetView.do) (서울 열린데이터광장) | 서울·자치구 고용지표 | 자치구 / 반기 | 무료 |

→ grain이 자치구까지만. 상권/행정동 배경값으로만 사용.
→ **이식 완료(2026-09-01)**: `고용률.csv`(서울 25구 × 계/성별 × 반기 2021H1~2026H1) → `data/고용률/자치구_고용률_반기.csv` (`scripts/ingest_employment_rate.py`, 자치구 코드 25/25 매칭). FC-53 partial.
→ 참고: `경제활동인구(시도)`는 다른 지표(경제활동인구 수·시도 단위)라 이식 보류.

### FC-51 재개발·정비사업

| 출처 | 내용 | grain / 갱신 | 접근 |
| --- | --- | --- | --- |
| [서울도시계획포털](https://urban.seoul.go.kr/) — **UQ120 도시계획사업** shp | 정비사업(재개발·재건축·모아타운)·역세권사업·도시개발·재정비촉진 폴리곤 + 추진단계 코드 | 사업구역 폴리곤 / 수시 | 무료 shp (EPSG:5174, dbf cp949) + 코드정의표 xlsx |
| [서울시 정비사업 정보몽땅](https://cleanup.seoul.go.kr/) — 사업장목록 | 재개발·재건축 조합 1,153건: 자치구·사업구분·진행단계·대표지번·자료공개현황 | 자치구(좌표 없음) / 수시 | 무료 .xls |

→ **이식 완료(2026-09-01)** `scripts/ingest_urban_projects.py`(UQ120 shp point-in-polygon → 상권 겹침) + `scripts/ingest_redev_associations.py`(조합 목록). FC-51·52 partial. 추진단계는 스냅샷(발표일 시계열 아님) → "예정" 단정 금지.
→ 잔여(P3): 랜드마크 단일 시설(경기장·전시관 등) 공식 개발 사건.

## P1 — SNS·뉴스 언급 (FC-41·42)

| 출처 | 내용 | 접근 | 한계 |
| --- | --- | --- | --- |
| [네이버 데이터랩 검색어 트렌드 API](https://developers.naver.com/docs/serviceapi/datalab/search/search.md) — **애플리케이션 신청 완료 2026-09-01** (`.env` 의 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`, 래퍼 `scripts/naver_datalab.py`) | 키워드그룹별 상대 검색 추세(기간·성·연령·기기) | 무료, 일 25,000회, 최대 5그룹 | **절대량 아님(구간 내 max=100 정규화)**. openapi는 **지역별 분해 불가**(웹 전용) → 지역명·업종명을 키워드로 넣어 추세만 |
| [빅카인즈 BIGKINDS](https://www.bigkinds.or.kr/) | 뉴스 기사 지역·키워드 언급 건수, 시기별·지역별 기사량, 연관어 | export 이식 완료(2026-09-02, GPT/Codex); API는 별도 승인 필요 | 뉴스만(SNS 아님). export 내부 검색어·기간 메타데이터 미포함, 행정동은 exact/통용명 매칭이라 사업구역 정밀 위치 아님 |
| [네이버 뉴스 검색 API HUB](https://api.ncloud-docs.com/docs/naver-api-hub-search-news) | 현재 시점 뉴스 검색 결과의 제목·발행일·URL·검색어 | **수동 1회 snapshot 이식 완료(2026-09-02, GPT/Codex)**; `scripts/ingest_naver_news_snapshot.py` | 쿼리당 현재 상위 `display≤100`건만 저장. 전체 일치 건수의 전수 목록 아님. 자동화·예약 갱신 없음. API 결과는 뉴스 보도량 보조 맥락이며 SNS·사업 확정·성공 outcome 아님 |
| 구글 트렌드 | 검색 관심도 | 무료 | 지역 시도 단위, 보조 |
| 썸트렌드(some.co.kr) 등 소셜 버즈 | 인스타·블로그·트위터 버즈량 | 유료 / 제휴 | 사람 승인 필요 |

→ **이식 완료(2026-09-02)**: 빅카인즈 export와 네이버 뉴스 API의 수동 snapshot을 `data/뉴스/` JSONL + manifest로 정규화했다. 원문 본문·description은 보존하지 않고, URL 중복 제거와 서울 자치구·행정동/주제 태그를 기록한다. `recommendation_pipeline.py`는 두 snapshot을 `FC-51-news`의 `evidence[]`·`context_notes`에만 연결하고 공식 도시계획사업의 대체·성공확률·등급·정렬 입력으로 사용하지 않는다. **파이프라인은 API를 호출하지 않으며 자동화·예약 갱신도 없다.** 새 시점 결과는 수동 수집 명령을 실행할 때만 교체한다.

## P0 — 출력 위치 정밀도 (지점 seed 확장 / `매물주소`)

### 지하철역 (앵커) — **역 단위 이식 완료 (2026-09-01)**

| 출처 | 내용 | 접근 | 상태 |
| --- | --- | --- | --- |
| [국가철도공단 전체 도시철도역사정보](https://www.data.go.kr/data/15013205/fileData.do) (15013205) | 전국 역사 좌표(WGS84)·노선·환승 | 무료 파일 | **이식 완료** → `data/도시철도역사/`, `scripts/ingest_subway_stations.py`. FC-07 + `nearby_anchors` 역 |
| [지하철 출입구 정보 및 주변정류장 정보](https://t-data.seoul.go.kr/dataprovide/trafficdataviewfile.do?data_id=10045) (서울교통빅데이터플랫폼, `tnSubwayEntrc.csv`) | **출입구 단위** 좌표(WGS84) + 주변 버스정류장 | 무료 CSV, 일별 갱신, BIS 제공 | **잔여 — 출구 정밀도용 1순위**. 2022-02 공개 |
| [서울교통공사 지하철 주변 주요시설 정보](https://data.seoul.go.kr/dataList/OA-15993/A/1/datasetView.do) (OA-15993) | 역별·출구번호별 주변시설 | 무료 | 잔여 (출구 POI) |
| [국가철도공단 코레일 지하철 주소데이터](https://www.data.go.kr/data/15041113/fileData.do) (15041113) | 코레일 역 지번·도로명 주소 | 무료 파일 | 역 단위(출구 아님). 이식본과 중복 |

### 지하철 개통예정·건설중 역 (FC-52 미래신호) — **자치구 계획 신호 이식 완료 (2026-09-01)**

**역별 좌표 데이터셋은 여전히 없음.** 이식한 것: 제2차 서울 도시철도망 구축계획(관보 제19878호, 2020-11-17) 11개 노선을 자치구 경유 신호로.

| 출처 | 내용 | 접근 | 상태 |
| --- | --- | --- | --- |
| 제2차 서울 도시철도망 구축계획 (관보 제19878호, [보도자료](https://news.seoul.go.kr/traffic/archives/506085)) | 강북횡단·서부·목동·면목·난곡선 등 11 노선, 자치구 경유 | 문서/이미지 | **이식 완료** → `data/도시철도역사/도시철도망계획_{노선,자치구}.csv`. 정거장 위치·개통 미확정 |
| [전국도시철도노선정보표준데이터](https://www.data.go.kr/data/15013203/standard.do) (15013203) | 노선 기·종점·역 구성·개통일자 | 무료 표준데이터 | **운영 노선만** — 이식 안 함(참고용) |
| 나무위키·위키백과 역 목록 (GTX-B·C, 동북선, 신안산선 등) | 건설중 노선별 역명·예상 위치 | 웹, 라이선스 주의 | 잔여 — 필요 시 역명 → VWorld 지오코딩 |

- 잔여(GTX·동북선·신안산선·위례신사선 등 서울 도시철도망계획 밖): 역명 수기 + VWorld 지오코딩 → `개통예정역_수기.csv`. "예정" 표기 필수(F16)

### 버스 정류장 (FC-07) — **이식 완료 (2026-09-01)**

| 출처 | 내용 | 접근 | 상태 |
| --- | --- | --- | --- |
| [서울시 버스정류소 위치정보](https://data.seoul.go.kr/dataList/OA-15067/S/1/datasetView.do) (OA-15067) | NODE_ID·ARS·정류소명·X/Y(WGS84)·정류소타입 | 무료 xlsx 월 스냅샷 | **이식 완료** → `data/버스정류장/`, `scripts/ingest_bus_stops.py`. 2026-08 스냅샷 사용 |
| [전국 버스정류장 위치정보](https://www.data.go.kr/data/15067528/fileData.do) (15067528, 국토부) | 전국 정류장ID·명·위경도·도시코드 | 무료 파일 | **이식 완료**(경계 인접 경기·인천분만) |
| [전국버스정류소표준데이터](https://www.data.go.kr/data/15096280/standard.do) (15096280) | 표준 정류소번호·명·위경도·관리기관 | 오픈API | 대체 소스 |
| 버스 노선·배차 간격 | 정류소별 경유 노선·배차 | 서울 열린데이터광장·TAGO | 잔여 — FC-07 심화용 |

### 아파트 단지 (앵커·FC-08) — **이식 완료 (2026-09-01)**

`scripts/ingest_apartment_complex.py` = K-apt 목록 + 면적 xlsx + VWorld Search 지오코딩 + GIS건물통합정보 footprint. → `data/공동주택/`.

| 출처 | 사용 | 상태 |
| --- | --- | --- |
| [공동주택 단지 목록제공 서비스](https://www.data.go.kr/data/15057332/openapi.do) (15057332, `AptListService4/getSidoAptList4`) | 서울 3,396단지 kaptCode·kaptName·bjdCode | **이식 완료** (`DATA_GO_KR_SERVICE_KEY`) |
| [공동주택 단지 면적 정보](https://www.data.go.kr/data/15073269/fileData.do) (15073269) | kaptCode 로 세대수·동수·관리비부과면적 join | **이식 완료** (`원천데이터/공동주택/공동주택_단지면적정보.xlsx`) |
| VWorld Search API (`/req/search` type=place) | `{구}{동}{단지명}` → WGS84 좌표. **89.7% 매칭** | **이식 완료** (캐시 `data/공동주택/_geocode_cache.json`) |
| [GIS건물통합정보 (서울)](https://www.data.go.kr/data/15083092/fileData.do) (15083092) | A8='02000' 공동주택 폴리곤 117,720개, **EPSG:5186→5181**, footprint 근사 | **이식 완료** (`원천데이터/공동주택/GIS건물통합정보_서울/`) |
| [공동주택 기본 정보제공 서비스](https://www.data.go.kr/data/15058453/openapi.do) (15058453) | 미구독 → VWorld Search 로 대체 | 미사용 |
| 소형 빌라·연립·다세대 경계 | K-apt 누락분 | 잔여 — 별도 소스 필요 |

### 랜드마크·일반 POI (앵커)

| 출처 | 내용 | 접근 |
| --- | --- | --- |
| 카카오맵 로컬 API (place keyword/category search) | 카테고리별 POI, 좌표, 도로명주소 | **부분 이식·격자 파일럿 완료(2026-09-02, GPT/Codex)** — `scripts/kakao_local.py`·`scripts/ingest_kakao_poi.py`·`scripts/ingest_kakao_poi_grid.py`, 잠실역 1km seed 샘플 90행. 격자 수집기는 선택 경계 `rect`·PIP·Kakao ID 중복 제거·Kakao **45문서 노출상한** 포화 4분할·커버리지 manifest를 구현했고, 잠실동 FD6/CE7은 343행·193 요청·`complete_requested_queries`를 통과했다. `context/` 출력은 자동 seed가 아니다. 일 9만·월 270만 기본 호출 가드와 manifest 예산 snapshot을 적용한다. 다른 영역의 QA 후에만 250m/500m 상세 맥락으로 확장하며, 매물·공실 데이터는 아님 |
| 네이버 지도 API (Search) | POI 검색 | 무료 쿼터 |
| 브이월드 / 국가공간정보포털 | 주요시설 공간정보 | 무료 |

### 역지오코딩 (좌표 → 도로명주소)

| 출처 | 한도 | 접근 |
| --- | --- | --- |
| [행정안전부 실시간 주소별 좌표정보 조회](https://www.data.go.kr/data/15057559/openapi.do) (15057559), [주소정보 조회](https://www.data.go.kr/data/15057017/openapi.do) (15057017) | — | 무료 API 신청, [juso.go.kr](https://www.juso.go.kr/) |
| [국토교통부 지오코더 API](https://www.data.go.kr/data/15101106/openapi.do) (15101106) | 일 40,000건 | 무료 |
| 카카오 로컬 `coord2address` | 쿼터 | 무료 |
| VWorld Geocoder API | — | 무료 |

### 개별 매물 (상가 임대) — `address_point` / `listing_url`

| 출처 | 내용 | 접근 | 비고 |
| --- | --- | --- | --- |
| [디스코 disco.re](https://www.disco.re/) | 토지·빌딩·상가 매물·실거래 | 공식 오픈 API 없음 | 제휴 또는 크롤링(약관 확인) |
| [밸류맵 valueupmap.com](https://www.valueupmap.com/) | 상가·건물 실거래·매물 | 공식 오픈 API 없음 | 동일 |
| [부동산플래닛 bdsplanet.com](https://www.bdsplanet.com/) | 상업용 매물·상권분석 | 공식 오픈 API 없음 | 동일 |
| 네이버 부동산 | 상가 임대 매물 | 비공식 | 크롤링 정책 엄격 |
| [소상공인시장진흥공단 상가(상권)정보 API](https://www.data.go.kr/data/15012005/openapi.do) (15012005) | 개별 상가업소: 상호·주소·좌표·업종 (국세청/카드사) | 무료 API | 매물 아님(영업 중 업소). 상권 경계·경쟁 파악에 유용 |

→ **개별 매물은 국가 무료 소스 없음. 민간 제휴/크롤링 → 사람 승인 게이트 필수** (약관·개인정보·크롤링 정책). 확보 전 `precision`은 `상권` 고정, F11 유지.

## P1 — 신규점포 outcome (학습모델 재검토용)

**LOCALDATA(`localdata.go.kr`)는 2026-01-25부로 공공데이터포털(data.go.kr)로 통합 개방됐다** (지방행정 인허가 195종 + 생활편의 14종). `localdata.go.kr`는 2026-04-15까지 병행 운영 후 종료 → 현재는 **data.go.kr에서 받는다**. API 형식 개방이 강화됨.

| 출처 | 내용 | grain / 갱신 | 접근 |
| --- | --- | --- | --- |
| [행정안전부_식품_일반음식점](https://www.data.go.kr/data/15045016/fileData.do) (15045016, 파일) / [OpenAPI](https://www.data.go.kr/data/15154916/openapi.do) (15154916) | **전국 약 213만 행**. 인허가일자·폐업일자·영업상태·좌표(X/Y, EPSG:5174)·업태구분명·소재지주소. 자치단체 표준 취합 | 개별 업소 / 수시(2일 전 기준 현행화) | 무료. 파일은 포털 로그인 후 다운로드, API는 서비스키 |
| [행정안전부_식품_휴게음식점](https://www.data.go.kr/data/15006730/fileData.do) (15006730) | 동일 표준(카페·분식·패스트푸드 등 휴게업) | 개별 업소 / 수시 | 무료 |
| [서울시 일반음식점 인허가 정보](https://data.seoul.go.kr/dataList/OA-16094/S/1/datasetView.do) (OA-16094) / [data.go.kr 15071760](https://www.data.go.kr/data/15071760/fileData.do) | 서울분. 단 좌표계 EPSG:2097, 갱신 불규칙 | 개별 업소 | 무료 |
| 국세청 사업자등록 상태조회 (공공데이터포털) | 폐업 여부 확인용 | 사업자번호 | 무료 |

→ **이식 완료 2026-09-01** (`scripts/ingest_food_license.py`): 서울 필터본 684,820 업소 → 좌표 5174→5181 변환(이동량 dy −305m, 변환 필수) → point-in-polygon → `data/인허가/음식점_상권분기_패널.csv`(상권 1,630 × 10업종 × 20211~20263). 업태 매핑 84%, 좌표 결측 5.7%.
→ 교차검증: 기존 `data/점포/`와 점포수 r=0.92(일치), 분기별 폐업률 r=0.11(**대체 금지** — 인허가 폐업일자는 행정 처리일 지연). `audit-only`로 분류.
→ 활용: 개별 개·폐점일 → 4분기 생존율(예 20241 개업 83.4% 생존), greenfield 진입 시점. `metric-contract` 학습모델 재검토 조건 부분 충족.
→ 잔여 missing: 매출·손익·보증금·면적(licensing 시점 `월세액`·`보증액`은 대부분 결측).

## P2 — 임대료 상권 세분 (기존 권역 데이터 보강)

| 출처 | 내용 | grain | 접근 |
| --- | --- | --- | --- |
| [한국부동산원 R-ONE 상업용부동산 임대동향조사](https://www.reb.or.kr/r-one/) | 소규모/중대형/집합 상가 임대료·공실률, **상권별** | 서울 주요 상권 / 분기 | 무료, [공공데이터포털 15069766](https://www.data.go.kr/data/15069766/fileData.do) (분기별 지역별 임대료 소규모상가) |

→ **이식 완료 2026-09-01** (`scripts/ingest_rent_trend.py` → `data/임대료/R-ONE_임대동향_분기.csv`, 3,076행): 4 상가유형 × 임대료/지수 × 서울전체·권역4·**R-ONE 상권72** × 2024Q1~2026Q2. FC-20 권역 grain(M4) 부분 완화. 현재 CSV 이식본에는 공실률이 없어 FC-21은 기존 권역 데이터를 유지한다.
→ **1차 crosswalk 완료 2026-09-02(GPT/Codex)**: `scripts/build_rone_trdar_crosswalk.py` → `output/crosswalks/crosswalk_rone_trdar.csv`·요약 JSON. R-ONE 72개 중 자동 결합 후보 52개, review 18개, `테헤란로` 미해결 1개, 서울 대상 재사용 1개. 이는 경계 중첩이 아닌 명칭·별칭 proxy이며 운영에는 `join_eligible=yes`만 사용한다.

## 우선순위 요약

| 순위 | 데이터 | 출처 | 활성화 난이도 |
| --- | --- | --- | --- |
| ~~1~~ 완료 | FC-06 외국인 장기·단기 | 서울 열린데이터광장 OA-14992/14993 → `data/외국인생활인구/` |
| ~~1~~ 완료 | 신규점포 outcome (인허가) | 공공데이터포털 15045016·15006730 → `data/인허가/` |
| ~~2~~ 완료 | FC-53 고용률 | KOSIS 지역별고용조사 → `data/고용률/` |
| ~~2~~ 완료 | FC-51·52 정비·개발사업 | 서울도시계획포털 UQ120 + 정비사업 정보몽땅 → `data/도시계획사업/` |
| ~~2~~ 완료 | FC-41·42 검색 관심도 | 네이버 데이터랩(NCP HUB) → 이식 완료 2026-09-01 (`data/네이버트렌드/`, 온톨로지 `data/ontology/`). 74호출 |
| ~~1~~ 완료 | 역지오코딩 | VWorld `VWORLD_API_KEY` → `scripts/geocode.py` 실호출 검증 |
| ~~1~~ 완료 | 지하철 역 앵커 | 국가철도공단 15013205 → `data/도시철도역사/`, `scripts/ingest_subway_stations.py`. FC-07·`precision=상권+앵커` |
| ~~2~~ 완료 | 버스 정류장 (FC-07) | 서울시 OA-15067 + 국토부 15067528 → `data/버스정류장/`, `scripts/ingest_bus_stops.py`. 계단식 스냅샷 |
| ~~3~~ 완료 | 서울 계획 도시철도 (FC-52) | 제2차 서울 도시철도망 구축계획(관보) → `data/도시철도역사/도시철도망계획_*.csv`, `scripts/ingest_subway_context.py`. 자치구 grain |
| 2 | 지하철 출구 좌표 | `tnSubwayEntrc.csv`엔 출입구 좌표 없음(연계 버스정류장 좌표) → 출입구 수만 이식. 좌표는 다른 소스 필요 |
| 3 | GTX·동북선 등 별도 계획 역 | 노선별 역명 수기 + VWorld 지오코딩. "예정" 표기 필수 |
| 3 | 버스 노선·배차 | 서울 열린데이터광장·TAGO. FC-07 심화 |
| ~~진행~~ 완료 | 아파트 단지 (FC-08) | K-apt 15057332 + 면적 15073269 + VWorld Search + GIS건물통합정보 15083092 → `data/공동주택/`, `scripts/ingest_apartment_complex.py`. 89.7% 좌표. 소형 빌라·정밀 경계는 잔여 |
| ~~3~~ 완료 | 임대료 상권 세분 | 한국부동산원 R-ONE → `data/임대료/R-ONE_임대동향_분기.csv` |
| 3 | 랜드마크 단일시설 사건 | 서울시 보도자료·공식 발표 | 수집 방식 미정 |
| — | 개별 매물 | 디스코·밸류맵·부동산플래닛 | **높음, 사람 승인** (제휴·크롤링 약관) |

## 확보 방법 (2026-09-01 환경 확인)

Claude가 이 환경에서 직접 수집한 결과:

| 경로 | 가능 여부 | 근거 |
| --- | --- | --- |
| VWorld 지오코딩 API (`api.vworld.kr`) | **가능** | `VWORLD_API_KEY` 로 이 환경에서 정방향·역방향 실호출 성공 (`scripts/geocode.py`) |
| 서울 열린데이터광장 OpenAPI `sample` 키 | 부분 가능 | `openapi.seoul.go.kr:8088` 도달, `SPOP_LOCAL_RESD_DONG` 실데이터 반환 확인. 단 **최근 2개월만**, sample 키는 행 수 제한(5~1000) |
| 서울 열린데이터광장 정식 키 필요 데이터 | 사용자 키 필요 | 전체 기간·전체 행은 발급키 필요. Claude는 계정 등록 불가 |
| 공공데이터포털(data.go.kr) API — 인허가(구 LOCALDATA)·국토부·행안부·한국관광공사 | 사용자 키 필요 | API별 서비스키 발급 필요. 구 `localdata.go.kr`는 2026-04-15 종료(→ data.go.kr 통합). 파일 다운로드는 포털 로그인만 |
| KOSIS·네이버 데이터랩·빅카인즈 API | 사용자 키 필요 | 계정 연동 키 |
| 대용량 CSV 일괄 다운로드(연도별 파일 등) | 사용자 수동 | 기존 `data/`와 동일 방식 — 포털에서 직접 내려받아 저장소에 넣기 |

**현실적 경로**: (a) 사용자가 각 포털에서 전체 파일을 내려받아 `data/`에 추가 → Claude가 감사·정규화·이식, 또는 (b) 사용자가 `data.seoul.go.kr` 키를 제공 → Claude가 최근 2개월 슬라이스로 수집 스크립트를 검증. Claude가 지금 할 수 있는 것: 수집·정규화 스크립트를 미리 작성(키는 환경변수), `sample` 키로 스키마 확인.

### API 키 관리

- 템플릿: `.env.example` (커밋). 사용자가 `cp .env.example .env` 후 값 입력. `.env`는 `.gitignore`.
- 스크립트에서 `from _env import load_env, require` (`scripts/_env.py`, 의존성 없음).
- 발급·검증 완료(2026-09-01): VWorld 지오코딩 API → `.env` 의 `VWORLD_API_KEY`. 래퍼 `scripts/geocode.py`(`to_coord`/`to_address`, 기본 CRS epsg:5181).
  - **이 환경에서 실호출 성공**: 올림픽로 300 → (208992, 445933) ↔ 역 "송파구 올림픽로 300 (신천동, 롯데월드타워)". 양방향 정상.
  - 부수 검증: 종로 사직로 133-6 정방향 좌표가 식품 인허가 원본(EPSG:5174) 대비 dy +306m → `ingest_food_license.py` 의 5174→5181 재투영이 맞았음을 교차 확인.
  - 일 4만건, 키 발급 시 등록한 도메인/IP 제한. 같은 키로 VWorld 데이터·지도 API도 사용.
- 발급·검증 완료(2026-09-01): 네이버 데이터랩 → `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`. 래퍼 `scripts/naver_datalab.py` + 무료구간 가드 `scripts/_budget.py`.
  - **원인 규명**: 2025년 검색/데이터랩 API가 **NAVER API HUB(NCP)로 이관**됨. 신규 엔드포인트 `https://naverapihub.apigw.ntruss.com/search-trend/v1/search`, 신규 헤더 `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY`. 기존 `openapi.naver.com` + `X-Naver-Client-*` 로는 401. 래퍼가 신규→레거시 순으로 시도하도록 수정 후 **실호출 성공** ("송파구 맛집" 2024-01~2026-08 상대지수 반환, 예산 1/28000).
  - 이관 유예: 2027-06-30까지 레거시 방식 병행. `NAVER_DATALAB_ENDPOINTS` 로 조정 가능.
- 미발급: 공공데이터포털 공통키, 서울 열린데이터광장, 빅카인즈 API, KOSIS. 단 빅카인즈 다운로드 export는 사용자 제공 파일을 2026-09-02 정규화·추천 RAG에 연결했다(`data/뉴스/`).
- 카카오 로컬 REST API **발급·실호출·격자 파일럿 검증 완료(2026-09-02)**: `.env`의 `KAKAO_REST_API_KEY`, 키워드 `잠실역` 3건·카테고리 `FD6`/`CE7` 각 페이지와 작은 `rect` 카테고리 호출이 성공했다. 정규화 seed 샘플은 `data/카카오POI/`에 저장한다. `scripts/ingest_kakao_poi_grid.py`로 잠실동 FD6/CE7을 실제 수집해 343행·193 요청·`complete_requested_queries`를 기록했다. 반경 evidence 연결과 다른 선택 영역·업종의 배치는 별도 QA다. 카카오 POI는 관측된 장소 seed/맥락일 뿐 매물·공실·성공 outcome이 아니다.
- 한국부동산원 R-ONE 부동산통계정보 API **발급·실호출 검증 완료(2026-09-02, GPT/Codex)**: `.env.example`의 `RONE_API_KEY`를 `scripts/rone_api.py`가 사용한다. 공식 `SttsApiTbl.do`(표 목록)와 `SttsApiTblData.do`(자료)를 JSON으로 호출했고, `T244363134858603`(중대형 상가 임대료)·`T249633134845544`(중대형 상가 공실률)의 서울 `CLS_ID=500002`, 지표 `ITM_ID=100001` 조회가 `INFO-000`으로 성공했다. 두 표 모두 2026Q2까지 반환됐다. 현재 운영 데이터는 재현 가능한 CSV 이식본을 유지하고, API 자동 갱신·공실률 CSV 이식은 별도 작업으로 남긴다. R-ONE↔서울 상권분석 1차 crosswalk는 `output/crosswalks/`에 생성됐으며 자동 결합 후보 52/72개만 사용한다. 상세 명령은 [`artifacts/handoff_rone_api.md`](../handoff_rone_api.md)·[`artifacts/handoff_rone_trdar_crosswalk.md`](../handoff_rone_trdar_crosswalk.md).

## 사람 승인 필요

- 민간 부동산 플랫폼(디스코·밸류맵·부동산플래닛·네이버 부동산) 제휴·크롤링 — 약관·개인정보·크롤링 정책
- 유료 소셜 버즈(썸트렌드 등) 계약
- 각 공공 API 이용 신청 시 서비스 목적·트래픽 등록 (사용자 계정)
- 외부 데이터 공모전 제출물 표기(출처·라이선스)
