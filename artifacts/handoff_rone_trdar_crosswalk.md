# R-ONE 상권 ↔ 서울시 상권분석 crosswalk 인수인계

> 작성자: **GPT(Codex)**  
> 작성일: **2026-09-02**  
> 목적: R-ONE 임대동향의 상권명과 서울시 상권분석서비스 상권 폴리곤을 결합하기 전, 재현 가능하고 검토 가능한 연결표를 제공한다.

## 결론

1차 crosswalk는 생성했지만, R-ONE 조사권역과 서울시 상권분석 상권은 원천 경계와 grain이 동일하지 않다. 따라서 이 결과는 **공간 중첩 정답표가 아니라 명칭·별칭 기반 proxy 연결표**다.

- R-ONE 상권: 72개
- 서울시 상권분석 상권 SHP: 1,650개
- crosswalk 행: 84개
- 하나 이상의 대상 후보가 있는 R-ONE 상권: 71/72
- `join_eligible=yes` 자동 결합 후보: 52/72
- 사람 검토 대상: 18개 상권(`review`), 대상 재사용 1개(`TRDAR_CD=3120179`)
- 미해결: `테헤란로` 1개

## 산출물

- 생성기: `scripts/build_rone_trdar_crosswalk.py`
- 연결표: `output/crosswalks/crosswalk_rone_trdar.csv`
- 요약: `output/crosswalks/crosswalk_rone_trdar_summary.json`

재생성:

```bash
.venv/bin/python3 scripts/build_rone_trdar_crosswalk.py
```

생성기는 현재 `data/임대료/R-ONE_임대동향_분기.csv`의 R-ONE 상권 목록과 `data/영역/상권/서울시 상권분석서비스(영역-상권).shp`의 1,650개 상권명을 검증한다. 매핑 사전의 누락·오래된 키·존재하지 않는 대상명이 있으면 중단한다.

## 매핑 방법

`mapping_method`는 다음 중 하나다.

- `exact_name`: 양쪽 상권명이 동일
- `explicit_alias`: 역·시장·대학·관광특구 등의 명시적 명칭 차이
- `station_components`: R-ONE 역세권 축약명을 서울시 출구/주변 상권 후보로 분해
- `composite_components`: `신촌/이대`, `잠실/송파` 같은 복합권역을 구성요소로 보존
- `road_proxy`: `강남대로`, `도산대로`, `양재말죽거리` 같은 도로권역의 대표 상권 proxy
- `unresolved`: 방어 가능한 대상이 없어 강제 연결하지 않음

각 행의 `mapping_role=primary`는 대표 후보, `component`는 복합권역의 구성요소다. 복합권역의 각 대상에 R-ONE 임대료가 별도로 관측되었다고 해석하지 않는다.

## 운영 결합 규칙

자동 결합은 아래 조건을 모두 만족하는 행에만 허용한다.

```text
mapping_status=matched
mapping_role=primary
target_reuse_count=1
join_eligible=yes
```

`review`, `component`, 대상 재사용, `unresolved` 행은 운영 feature에 R-ONE 값을 자동 부착하지 말고 `missing_reason` 또는 검토 상태로 남긴다. 특히 `테헤란로`를 `강남역`으로 대체하지 않는다.

`join_eligible=yes`여도 R-ONE 값의 공간 grain은 여전히 `R-ONE 상권`이다. 후보 카드에는 `grain_is_proxy=true`, `proxy_note`를 함께 기록하고, “해당 서울시 상권의 임대료”나 “특정 주소의 월세”로 표현하지 않는다.

## 후속 작업

1. 후보 파이프라인이 `crosswalk_rone_trdar.csv`를 읽고 `join_eligible=yes`만 사용하도록 연결한다.
2. `review` 18개와 양재역 대상 재사용 건을 지도에서 사람이 확인한다. 확인 근거가 생기면 매핑 사전과 요약을 갱신한다.
3. 복합권역을 여러 후보에 복제해야 하는 경우, 임대료를 분배하거나 합산하지 말고 “권역 proxy”로만 노출한다.
4. R-ONE 공실률 API 표의 CSV 이식·정의 비교가 끝난 뒤에도 동일한 crosswalk 규칙을 적용한다.
5. host 상권 최근접 규칙(M-S3)과 R-ONE crosswalk를 혼동하지 않는다. 전자는 지점 후보의 배경 상권 배정이고, 후자는 R-ONE 조사권역을 서울 상권분석 상권에 귀속하는 비용 근거 proxy다.

## 검증 명령

```bash
.venv/bin/python3 scripts/build_rone_trdar_crosswalk.py
.venv/bin/python3 -m py_compile scripts/build_rone_trdar_crosswalk.py
git diff --check
```

실제 매핑을 운영 추천에 투입하기 전에는 `artifacts/30-review/recommendation-quality-review.md`의 잔여 QA와 사람 승인 상태를 확인한다.
