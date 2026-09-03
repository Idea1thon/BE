# 추천 품질 검토 — 라운드 4 (2026-09-02)

> **후속(2026-09-02 same day)**: 라운드 4 잔여 High(#10 `entry_health_v1` 컷) 해소. `entry-health-v1-cut-design.md` 작성·승인 → FC-10을 **전 업종 통합 지역 배경 등급**으로 확정(업종×상권 grain은 커피 기준 71% 0-inflated이라 폐기), 동일가중 0.25×4, 20261 사분위 동결 컷. `sample_jamsil_coffee.py` `_tier()`가 확정 산식 사용. **현재 Critical 0 / High 0.**

검토 대상: `candidate-selection-spec.md`(§1 지점 후보 재설계), `rag-evidence-schema.json`(location 재구조), `regional-characteristics-profile.md`(FC 24, §2 지점 grain, §8 사용자 정의 크로스워크), `40-sample/jamsil-coffee/`(실데이터 샘플), `data-integrity-check.md`(이식 감사), `data-catalog-contract.md`.
방식: `validating-recommendation-evidence` 스킬 + `validate_harness.py` + `jsonschema` + 결함 주입 F01~F25 + 샘플 출력 자동 검사.

## 공통 MVP 후속 QA (2026-09-02, GPT/Codex)

`scripts/recommendation_pipeline.py`를 실행해 설계 샘플을 공통화한 뒤, 잠실·커피(20건, `--include-poi`), 연남·커피(5건), 역삼1동·한식(5건)을 검증했다.

- 3개 실행 모두 `build_passed=true`, RAG schema 오류 0건, `fit_index=null`, `score_is_predictive=false`.
- 잠실은 host 상권 20/20, R-ONE 상권별 rent 5/20(나머지는 서울 지수 proxy)로 기록됐다.
- 연남은 host 상권 2/5이며 나머지는 선택 경계 인접 seed의 행정동 배경값 fallback으로 기록됐다. 이를 숨기지 않고 `host_commercial_area`, `grain_is_proxy`, `missing_features`, coverage에 남긴다.
- 잘못된 시군구 입력은 후보를 만들지 않고 종료한다. 특별조건의 월세·면적·주차는 매물 데이터 부재로 unsupported를 유지한다.
- 현재 판정은 규칙·근거 기반 MVP이며 성공확률 모델이나 운영 Top-K가 아니다. 다음 QA는 greenfield·POI 전역 배치·값 이상치·host 동 불일치다.

## FC-42 현재 검색 관심도 연결 QA (2026-09-02, GPT/Codex)

사용자 요청에 따라 `scripts/recommendation_pipeline.py`가 `data/네이버트렌드/업종_검색트렌드_월.csv`의 업종 검색 관심도를 후보 Evidence에 연결했다.

- 후보에 기록하는 값은 최신월 `rel_index`와 최근 3개월 **원계열** 평균, **전년 동월 대비 배수(`yoy_clean_recent_mean`, 계절 정합·이상치 제외)**, 계절 국면이다. 계절 보정 `robust_slope12`는 비계절성 지속 여부를 점검하는 분석용 값으로만 남기고 후보 출력·가점에는 사용하지 않는다. (2026-09-02: '전체 관측 평균 대비 배수'는 장기 우상향 편향이 있어 YoY로 교체.)
- 계절 피크는 실제 현재 관심일 수 있으므로 `계절성 피크월`로 표시한다. `surge_active=true`이면 `미검증 급등`으로만 표시한다.
- FC-42는 업종 전체 서울시 검색 관심도라 후보 지점·상권 검색량이 아니다. `context_notes`와 `evidence`에만 두며 `reasons`, `counter_evidence`, `fit_tier`, 정렬, `fit_index`에 반영하지 않는다.
- 잠실·커피와 잠실·호프-간이주점 실행 모두 RAG schema 오류 0건. 커피는 현재 관심도 맥락, 호프는 2026년 여름 급등이 `미검증 급등`으로 기록되는 것을 확인했다.
- `qa_poi_context_invariance.py`에서 CS100009·CS100010을 context 없음/있음으로 반복 비교한 결과 **2/2 통과**, 후보·등급·긍정/반대 근거 불변이다.

## Kakao POI context 부분 재실행 QA (2026-09-02, GPT/Codex)

잠실동·커피 입력에 대해 `data/카카오POI/context/카카오_로컬_POI_격자_송파구-잠실동_manifest.json`의 `complete_requested_queries` 스냅샷(FD6·CE7, 343건)만 연결해 27개 지점 후보를 다시 생성했다.

- 27/27 후보에 반경 250m·500m별 `CE7` 카페 수와 `FD6` 음식점 수, 총 4개 **지점 grain 중립 관측**을 기록했고 RAG schema 오류는 0건이다.
- context 선택은 시도·시군구·법정동이 요청과 정확히 같고, CSV 행 수·POI ID·좌표가 manifest와 일치하는 경우로 제한한다. 다른 영역·`partial_*` 결과는 사용하지 않고 `missing_features`·coverage에 남긴다.
- context 있음/없음 실행을 비교해 후보 ID·순서, `fit_tier`, 긍정 근거, 반대 근거와 FC-11 변화지표 코드가 모두 동일함을 확인했다. 따라서 POI는 지역 특성(FC)이나 좋은 입지의 판정 입력이 아니라, 사람이 읽는 주변 공급·생활 맥락의 보조 관측이다.
- 카카오 FD6·CE7은 선택 카테고리 스냅샷일 뿐 전체 상업시설·공실·매물·수요·창업 성공 outcome을 대표하지 않는다. POI 밀도로 순위·성공 가능성을 추론하지 않는다.
- 반복 QA: `scripts/qa_poi_context_invariance.py`로 잠실동의 프로젝트 10개 업종(CS100001~CS100010)을 같은 특별조건으로 각각 context 없음/있음 실행했다. **10/10 통과**, 업종마다 후보 27개이며 F24의 후보 ID·순서·등급·긍정/반대 근거와 F25의 FC-11·`entry_health` 라벨 변화는 0건이다. 산출물은 `artifacts/evals/poi-context-invariance-2026-09-02/`와 `output/recommendation_runs/poi-context-invariance-2026-09-02/`에 보존한다.

## 라운드 3 이후 변경 (검토 사유)

- FC-01 유동밀도 재정의(총량 순위 금지), FC-06a·b 근사 비율(장기/상주, 단기/유동), FC-07 철도+버스, FC-08 배후 주거단지, FC-52 계획 도시철도
- 이식 3건: R-ONE 임대동향, 도시철도역사+버스+도시철도망계획, 공동주택 K-apt
- **후보 단위 재설계: 상권 폴리곤 → 지점(아파트·역)**. 상권·행정동은 배경 지표 grain으로 강등
- 실데이터 샘플: 송파구 잠실동·커피, 지점 16개
- 회귀 F16~F25 추가

## 요약

- **하네스 구조·스키마**: `validate_harness.py` PASS. `rag-evidence-schema.json` Draft 2020-12 유효. 샘플 16건 **스키마 오류 0**.
- **자동 결함 검사 (샘플 출력)**: F01·F14·F19·F20·F21·F22 전부 통과 — 아래 표.
- **High 0건 (2026-09-02 갱신)**: 라운드 4의 잔여 High(`entry_health_v1` 컷)는 설계·승인 완료 — `entry-health-v1-cut-design.md`. `_tier()`가 확정 산식(전 업종 통합, 동일가중, 사분위 동결) 사용, 등급은 반대근거 1항목.
- **Medium**: ① R-ONE↔상권분석 1차 crosswalk의 18개 review·대상 재사용 1건 지도 QA가 남음(eligible 운영 연결은 MVP 반영) ② host 상권 최근접 배정이 상권 sparse 지역에서 옆 동 상권에 붙음(M-S3, 자치구 가드만) ③ 행정동 배경값일 때 유동밀도가 대부분 `밀도_저평가_주의`.
- **사람 승인 필요**: `fit_index_v1` 가중치·컷, FC-05 유형 경계, 후보 등급 운영 임계값, 외부 API·매물 약관, 공모전 제출.

## 자동 결함 검사 결과 (샘플 candidate-evidence.json, 16건)

| ID | 검사 | 결과 |
| --- | --- | --- |
| F01 | `score_is_predictive` = false, `fit_index` = null | 통과 (16/16) |
| F19 | FC-01 = 유동밀도 evidence, 총량 metric 없음 | 통과 |
| F20 | 상권/행정동/자치구 배경값(유동밀도·점포당매출·변화지표·고용률·외국인비율)에 `grain_is_proxy=true` + `proxy_note` | 통과 (각 16/16) |
| F21 | `spatial_grain` 전부 `지점`, `candidate_type` ∈ {아파트단지_인근, 역_인근} | 통과 (commercial_area 후보 0) |
| F22 | 외국인 근사 비율 evidence `limitation`에 방법론 상이("생활인구 ÷ 타 방법론") 명시 | 통과 (16/16, FC-06a·b 각) |
| — | 지점 반경 고유값(역·아파트·버스 43건) `grain_is_proxy=false` | 통과 |
| — | host 상권 없는 지점(2건)은 `host_commercial_area=null` + `missing_features`에 기록 | 통과 |

## 상세 검증

| # | 기준 | 판정 | 근거 |
| --- | --- | --- | --- |
| 1 | 후보 단위가 상권 폴리곤이 아닌 지점 | 통과 | spec §1 재설계. 샘플 후보 전부 `APT-`/`STN-` id, `precision=지점`. 상권 폴리곤은 `host_commercial_area`로만 참조 |
| 2 | 지점 반경 고유값 vs 상권/행정동 배경값 구분 | 통과 | FC-07·08 = 원천 좌표로 반경 직접 계산(`grain_is_proxy=false`). FC-01·11·30~32 = `host_commercial_area` + `grain_notes` + evidence `proxy_note` |
| 3 | 상권 밖 지점 fallback (서울 27% 커버 한계) | 통과 | 잠실우성1,2,3차·종합운동장(STN-0218) → 포함 상권 없음·300m 초과 → **행정동 배경값**, `host_commercial_area=null` |
| 4 | 입력 "잠실동" 해석 | 통과(개선) | 법정동 잠실동 → 행정동 잠실2·3·7동(코드 명시). 자치구 가드로 대치동·삼성동 아파트 유입 차단 |
| 5 | FC-01 총량 분위 순위 금지 | 통과 | 유동밀도(유동/면적) 분위 주력, 총량은 `size_grade`만. 면적 상위 3% 상권은 `밀도_저평가_주의` → 추천 상한. 잠실역 총량분위 74.7 → 밀도분위 7.8, 판정 추천→조건부 |
| 6 | FC-06a·b 근사 비율 방법론 caveat | 통과 | FC-06a = 장기/상주(생활인구÷등록인구), FC-06b = 단기/유동일평균(체류÷통행). 둘 다 `limitation`에 "근사·신호" 명시. 방문 근사비율 잠실6동(롯데월드) 5.6%·서울 97.6%ile — 관광지 방향 맞음 |
| 7 | FC-07 지점 반경 계산 + 버스 유무 이분 금지 | 통과 | 지점 500m 역·아파트, 250m 정류소. 정류소는 수·마을버스·간선으로 비교 |
| 8 | FC-08 K-apt 성격·geocode 89.7% | 통과 | 의무관리 위주(소형 빌라 누락) 명시. seed 아파트는 자체 세대수 반경 합계 포함(수정). 미매칭 349 제외 |
| 9 | FC-52 계획 도시철도 2020년·자치구 grain | 통과 | "예정역" 금지, 운행개선 제외. 샘플에서 미사용(잠실2·3·7동 통과 노선 없음) |
| 10 | entry_health_v1 등급을 판정에 사용 | **해소(2026-09-02)** | `entry-health-v1-cut-design.md` §6 전체 권고안 승인. FC-10 = 전 업종 통합 지역 배경 등급(업종×상권 grain은 커피 기준 71% 0이라 폐기), 동일가중 0.25×4, 20261 사분위 동결 컷(상권 41/51/62·행정동 42/52/61). 라벨과 단조 정렬 검증(HL 56%→경계). `_tier()`가 확정 산식 사용, 등급은 **반대근거 1항목**(게이트 아님). `score_is_predictive=false` |
| 11 | 스키마 필수 필드(`evidence`·`source_freshness`) | 통과 | 샘플 16건 모두 포함. spec §7에 "핵심 필드만" 주석 추가 |
| 12 | `precision` enum 일관성 | 통과(수정) | 스키마 `["지점","매물주소","상권","행정동"]`. spec §1-3에서 "상권+앵커" 폐기 명시 |

## 경계면 이슈

| 생산자 | 소비자 | 문제 | 등급 | 조치 |
| --- | --- | --- | --- | --- |
| candidate-selection-spec §1 (지점) | rag-evidence-schema location | `anchor`·`host_commercial_area`·`point` 필드 필요 | High | **수정 완료** — 스키마 location 재구조, `candidate_type`·`spatial_grain` enum 확장, `precision` 재정의 |
| profile §2 (지점 grain) | spec §1-2 | 지점별 grain 선택 규칙 일치 | — | 두 문서 동일하게 작성(반경 직접/포함 상권/행정동 fallback) |
| entry_health_v1 컷 | 판정 로직 | 가중치·등급 컷 미확정인데 샘플이 등급을 판정에 사용 | **해소** | `entry-health-v1-cut-design.md` (2026-09-02 승인). 전 업종 통합·동일가중·사분위 동결 컷. 등급은 반대근거 1항목 |
| R-ONE 상권 72 | FC-20 비용 차원 | 1차 crosswalk 생성. 자동 결합 후보 52/72, 18 review·테헤란로 unresolved·대상 재사용 1건 | Medium | `output/crosswalks/` 산출물은 확인. 운영 파이프라인 연결·review 지도 QA 필요 |
| host 상권 최근접 배정 | 지점 후보 | 상권 폴리곤 sparse 지역(잠실3동)에서 옆 동 상권에 붙음 | Medium | 자치구 가드만 추가. 동 불일치 시 신뢰도 하향 규칙 미확정 |
| 네이버 트렌드 배치 경계 | FC-41 | 송파구 rel_index 2026-07 이후 급락(2.03→0.34) | Medium | `ingest_naver_trend.py` 앵커 정규화 재검토 |
| 명동 등 외국인 관광 1위 지역 | FC-06b | 단기 생활인구가 실제 관광객보다 낮게 잡힘(원천 데이터 한계) | Low | "신호"로만 사용, 명시 |

## 결함 주입 F16~F25 (라운드 4 + 후속)

| ID | 결함 | 기대 | 방어 지점 |
| --- | --- | --- | --- |
| F16 | 역/아파트 좌표만 있는데 출구번호·동/호수·상가 호실 생성 / 개통예정역을 FC-07에 | High 반려 | spec §1-3·§8, 프로파일 FC-07 "역 단위까지" |
| F17 | 버스 250m 정류소가 거의 전 상권 존재하는데 "버스 접근 가능"을 차별화 장점으로 | High 반려 | 프로파일 FC-07 "유무 이분 금지", 수·유형 비교 |
| F18 | FC-08을 "전체 거주 인구"로 / 미매칭 349를 "아파트 없음"으로 / geocode 신뢰도 무시 | High 반려 | 프로파일 FC-08 경계, `geocode_신뢰도` 컬럼 |
| F19 | FC-01 총 유동인구 분위를 순위 positive로 | High 반려 | 유동밀도(유동/면적) 분위, 총량은 규모 등급 |
| F20 | 지점에 붙인 상권/행정동 배경값을 지점 고유값처럼 | High 반려 | `grain_is_proxy`+`host_commercial_area`+`grain_notes` |
| F21 | 상권 폴리곤을 후보로 내보냄 | High 반려 | spec §1, 후보 = 지점. seed 0개일 때만 상권 fallback |
| F22 | FC-06b를 상주인구로 나눔 / 근사 비율을 방법론 차이 명시 없이 정밀 비율처럼 | High 반려 | spec §8, FC-06a=장기÷상주·FC-06b=단기÷유동, "신호" 표기 |
| F23 | entry_health 등급을 `fit_tier` 게이트·정렬키로 / 커피 진입 건전성으로 해석 / 산식·컷 비공개 | High 반려 | `entry-health-v1-cut-design.md`, FC-10 = 전 업종 통합 지역 배경, 반대근거 1항목, `score_is_predictive=false` |
| F24 | 다른 영역·부분 Kakao context를 연결하거나, POI 밀도를 FC·수요·공실·성공/등급·정렬·긍정/반대 근거로 사용 | High 반려 | 요청과 정확히 일치하는 완결 manifest만 선택, `spatial_grain=지점`·snapshot 한계 기록, context 유무 후보 불변 비교 |
| F25 | POI 카테고리 표시명이 FC-11 상권변화 코드·`entry_health` 라벨을 덮어씀 | High 반려 | POI `category_label` 분리, context 유무의 FC-11 evidence·`entry_health_v1.inputs.라벨` 동등성 검사 |

전부 `artifacts/evals/regression/fault-cases.md`에 등재. 샘플 출력 자동 검사에서 F01·F14·F19·F20·F21·F22·F23 및 POI context F24·F25 통과 확인. F24·F25는 `scripts/qa_poi_context_invariance.py`의 10개 업종 반복 실행으로도 재현했다.

## 미검증 영역

- ~~entry_health_v1 등급 컷~~ — 확정(2026-09-02). 잔여: 실제 사용자 피드백으로 가중치 재보정 시 `v2`; enriched(공실률) 변형은 R-ONE 공실률 이식·`join_eligible` crosswalk 연결 후
- R-ONE crosswalk 18개 review·대상 재사용 1건 지도 검증 (`artifacts/handoff_rone_trdar_crosswalk.md`; `join_eligible=yes` 운영 연결은 MVP 완료)
- host 상권 최근접 배정 규칙 (동 불일치 시 처리)
- 네이버 트렌드 배치 경계 정규화
- 다른 지역·업종·greenfield 케이스 (샘플은 1건)
- Kakao POI context: 잠실동 FD6/CE7 한 영역·두 카테고리만 연결 완료. 다른 지역·업종·카테고리의 수집 완결성, 시간 변동, 밀도 해석은 별도 QA 필요
- 지점 seed 확장(상가·근생 POI) 시 후보 수·품질
- 값 수준 이상치(추정매출 점포당 현실성, 음수·0 vs 결측)
- 도로연장 정규화(FC-01 정밀화), 내국인 생활인구(FC-06 정밀 비율)
- 랜드마크 단일시설, 빅카인즈 뉴스
- 실제 웹 백엔드 DTO ↔ 본 계약

## 판정

- **하네스 계약(데이터·프로파일·후보 명세·RAG 스키마) 정합**: 통과. 지점 후보 재설계가 4개 문서에 일관 반영, 스키마 검증·자동 결함 검사 통과.
- **후보 생성 MVP**: **통과(운영 전 검증 필요)**. Critical 0 / High 0. 공통 CLI가 3개 입력에서 schema 0 오류를 냈고, R-ONE eligible 연결·카카오 POI 선택 seed와 완결 context의 중립 RAG 관측을 지원한다. 잔여 Medium 3건과 UI/백엔드 DTO 연결이 남았다.
- 다음: R-ONE review 지도 QA → host 최근접 동 불일치 규칙 → greenfield·POI·값 이상치 전역 QA → UI/백엔드 DTO.

---

## 부록: 라운드 3 (2026-09-01) 기록

검토 대상: 6개 이식(외국인·인허가·고용률·도시계획사업·네이버트렌드·온톨로지). 프로파일 active 11/partial 11.
결과: RAG 스키마 High 2건(evidence `spatial_grain`에 자치구·권역 없음, `update_cadence`에 monthly·반기·스냅샷 없음) → 동일 세션 수정·`jsonschema` 재검증 통과. Medium(정규화 enum·spec §3·§8 stale) 수정. 결함 F12~F15 추가. Critical/High 미해결 0건.

---

## 격자 생성 evidence → 추천 파이프라인 연결 검증 (2026-09-02, GPT/Codex)

### 요약

- **데이터 흐름: 통과.** `recommendation_pipeline.py`가 `seeds.json`과 같은 디렉터리의 `gridpoint_evidence.jsonl`을 `evidence_id`로 매칭한다.
- **RAG evidence: 통과.** 송파구 잠실동·호프, 한식, 커피와 종로구·호프 실행에서 합성 후보의 `evidence[]`에 요청 업종 및 전체 음식점의 반경 500m 인허가 metric이 생성됐다. 세 실행 모두 RAG schema 오류 0건.
- **합성 좌표 안전장치: 통과.** 합성 후보는 `candidate_type=생성지점_격자`, `spatial_grain=지점(생성)`, `precision=지점(생성)`, `listing_url/address_point=null`이며 모두 `조건부 검토` 이하로 제한됐다. F28 결함 주입 시 스키마가 6개 오류를 검출했다.
- **판정·정렬 격리: 통과.** 생성 인허가 metric은 `context_notes`·`evidence[]`에만 기록되고 `reasons`, `counter_evidence`, `fit_tier`, 정렬에는 사용되지 않는다. 생성 후보를 추가한 뒤 기존 19개 관측 후보의 payload가 모두 불변이었다.
- **provenance: 통과 (2026-09-02 수정 후).** 경계면 이슈 1·2 해소 — 상대·절대 경로 모두 `feature_build.sources`·`run-manifest.json` `source_paths`에 생성 JSONL·인허가 경로가 기록된다.
- **UI/백엔드 DTO: 미검증.** 현재 실행 가능한 연결 지점은 CLI `--include-generated-points`이며, 저장소에서 이 인자를 호출하는 별도 UI/API 구현은 확인하지 못했다(handoff 다음실행 #14).

### 상세 검증

| 검증 항목 | 결과 | 근거 |
| --- | --- | --- |
| 생성 evidence 원본 전수 schema | 통과 | 26개 디렉터리·367개 레코드, `gen-evidence-v1` 오류 0, seed↔evidence ID 누락 0, 10개 업종 metric 누락 0 |
| 송파구 잠실동·호프 연결 | 통과 | 20후보, 합성 6개, 요청 metric·전체 음식점 metric 각 6/6, RAG schema 0 |
| 송파구 잠실동·한식/커피 연결 | 통과 | 합성 5/6개, 요청 metric·전체 음식점 metric 각 전부 존재, RAG schema 0 |
| 종로구 전체·호프 연결 | 통과 | 20후보, 합성 4개, 요청 metric·전체 음식점 metric 각 4/4, RAG schema 0 |
| 기존 후보 영향 | 통과 | 생성 seed 미사용 실행의 19개 공통 후보와 생성 실행의 공통 payload 차이 0건 |
| 과거 매출 기반 예측모델 혼입 | 통과 | `fit_index=null`, `score_is_predictive=false`; 생성 metric은 tier 계산 이후 evidence/context에만 부착 |
| 구조 하네스 | 통과 | `.claude/skills/validating-recommendation-evidence/scripts/validate_harness.py` PASS |

### 경계면 이슈

1. **상대경로 provenance 결함** → **수정 완료(2026-09-02).** `load_generated_seeds()`가 입력 경로를 `ROOT` 기준으로 resolve한 뒤 `relative_to(ROOT)`(실패 시 원문)로 기록한다. 상대·절대 입력 모두 `gridpoint_evidence.jsonl`이 후보 `feature_build.sources`에 남는다(종로구 상대경로 실행으로 확인).
2. **실행 manifest의 범위** → **수정 완료(2026-09-02).** `run-manifest.json`의 top-level `source_paths`가 모든 후보의 `feature_build.sources` 합집합을 포함한다(생성 JSONL·인허가·R-ONE 등). 후보별 `feature_build.sources`는 여전히 후보 단위 정본.
3. **문서 상태 불일치** → **정리 완료(2026-09-02).** `generated-gridpoint-seed-audit.md` 표·§연결 상태, `data-inventory.md`, `data-usage-classification.md`, `README.md` 전부 "반경 인허가 경쟁 metric은 evidence·context_notes에 연결(등급·정렬 미반영)"으로 통일. `gridpoint_evidence.jsonl` 판정은 `conditional`.
4. **coverage 한계.** 서울 25구 대표점은 공식 행정동 425개 중 224개만 실제 레코드가 있고, 전체 생성 레코드의 131/367은 상권 host 없이 행정동 배경값으로 fallback한다. 생성 evidence 연결은 이 공간 coverage를 확장하지 않는다. (미해소 — 행정동 전수 생성은 별도 작업)

### 미검증

- **UI 입력값·백엔드 DTO 연결은 여전히 미검증.** 현재 확인 가능한 연결점은 CLI `--include-generated-points`뿐이다(handoff 다음실행 #14).
- 실제 임대 가능 호실·주소·면적·월세·공실·창업 성공 outcome은 이 연결로 검증되지 않는다. 생성 인허가 수는 경쟁 규모 맥락일 뿐이다.

---

## 재검증 결과 (2026-09-02, GPT/Codex)

### 요약

- **최종 판정: 파이프라인 연결 통과.** 이전에 발견된 상대경로 provenance와 `run-manifest.json` 누락은 수정된 상태로 확인됐다.
- **원본 생성 evidence:** 26개 디렉터리·367개 레코드 전수 `gen-evidence-v1` schema 오류 0, seed↔evidence 누락 0, 10개 업종 metric 누락 0.
- **추천 파이프라인:** 송파구 잠실동·호프, 종로구 전체·호프의 상대경로 실행과 송파구 잠실동·호프 전체 136후보 실행을 재수행했다. 모두 RAG schema 오류 0건.
- **사람 승인 필요:** 행정동 전수 coverage, 실제 임대 가능 호실 데이터, UI/백엔드 DTO 연결은 여전히 별도 확인이 필요하다.

### 상세 검증

| 기준 | 판정 | 재검증 근거 |
| --- | --- | --- |
| 상대 파일·디렉터리 입력 | 통과 | `output/generated_evidence/.../seeds.json`와 디렉터리 입력 모두 117개 seed·117개 evidence 매칭, 생성 JSONL 경로 기록 |
| 후보 RAG 연결 | 통과 | 전체 117개 합성 후보에서 요청 업종 metric·전체 음식점 metric·`gridpoint_generated_evidence` coverage 존재 |
| source provenance | 통과 | 모든 합성 후보 `feature_build.sources`와 `run-manifest.json.source_paths`에 `gridpoint_evidence.jsonl`, `seeds.json`, 인허가 원자료 기록 |
| 후보 안전장치 | 통과 | 117/117이 `생성지점_격자`·`지점(생성)`·`listing_url/address_point=null`·`추천` 아님 |
| 판정·정렬 영향 | 통과 | 생성 전후 공통 관측 후보 19개의 payload 변경 0건; 생성 인허가 metric은 `context_notes`·`evidence[]`에만 존재 |
| 예측모델 혼입 | 통과 | 전체 생성 후보 `fit_index=null`, `score_is_predictive=false` |
| F28 결함 주입 | 통과 | synthetic 후보를 실제 지점·매물처럼 변조했을 때 schema 오류 6건 검출 |
| 문서 상태 동기화 | 통과 | 생성 관련 분석·인벤토리·사용분류·README에서 과거 “RAG 미연결” 문구 잔존 0건 |

### 경계면 이슈

- 생성 데이터는 여전히 서울 25구 대표점 250개이며 공식 행정동 425개 중 224개만 실제 생성 지점을 포함한다. 연결 성공이 공간 coverage 부족을 해소한 것은 아니다.
- 인허가 metric은 요청 업종 및 전체 음식점의 **경쟁 규모 맥락**이다. 매물·공실·월세·수요·성공 outcome으로 승격되지 않는다.

## 빅카인즈 시설·개발 뉴스 snapshot → 추천 RAG 연결 검증 (2026-09-02, GPT/Codex)

### 요약

- **정규화: 통과.** `scripts/ingest_bigkinds_news.py`가 빅카인즈 XLSX 2,616행을 읽어 분석제외 71행과 URL 중복 23행을 제거하고 2,522행 JSONL을 만들었다. 원문 본문은 저장하지 않고 제목·출처·URL·분류·파생 지역/주제 태그만 보존한다.
- **검색 메타데이터: 제한.** XLSX 내부에 검색어·검색기간 필드가 없어 파일명/CLI에서 추정했다. manifest에 `query_metadata_verified=false`와 SHA-256을 기록했다.
- **RAG 연결: 통과.** 후보별 자치구·행정동 기사량이 `FC-51_뉴스_시설개발_자치구기사량`·`FC-51_뉴스_시설개발_행정동기사량`으로 `evidence[]`에 연결되고, `context_notes` 및 `미래신호`의 `FC-51-news`에 역추적된다.
- **판정·정렬 격리: 통과.** 뉴스 포함/제외 잠실동 비교에서 19개 후보의 ID·순서·등급·`reasons`·`counter_evidence`·위치·신뢰도 변경이 0건이었다.
- **동시 경로: 통과.** 잠실동 생성 격자 + Kakao POI context 실행은 30개 출력, 합성 후보 15개, `schema_error_count=0`, 뉴스 coverage 30/30, POI coverage 30/30이었다.

### 상세 검증

| 기준 | 판정 | 재검증 근거 |
| --- | --- | --- |
| 원본 XLSX 파싱 | 통과 | 단일 worksheet BigKinds header 19개·2,616 data rows·일자 2024-01-02~2026-09-01 |
| 제외·중복 처리 | 통과 | 분석제외 71행, URL 중복 23행 제거; 정규화 2,522행; manifest에 정책·건수·원본 SHA 기록 |
| 후보 공간/기간/출처 | 통과 | 자치구·행정동 grain, `2024-01-01~2026-09-01`, JSONL + manifest 경로가 evidence에 기록 |
| RAG schema | 통과 | 잠실·연남·잠실 생성격자+POI 실행 모두 schema 오류 0 |
| 뉴스 evidence 연결 | 통과 | 일반 잠실 19/19·연남 7/7·생성격자+POI 30/30 후보에 자치구 뉴스 evidence 연결 |
| 판정·정렬 영향 | 통과 | 뉴스 포함/제외 실행 비교에서 ID·순서·등급·긍정/반대 근거·위치·신뢰도 변경 0 |
| 과도한 확정 표현 | 통과 | 보도량을 사업 확정·추진단계·수요·성공 outcome으로 해석하지 않는 limitation/context 문구 확인 |

### 경계면 이슈

- 이 snapshot은 `서울시 랜드마크 시설 계획` 검색 결과의 보도량이다. 공식 서울도시계획사업·정비사업 레코드나 사업구역의 확정 상태가 아니다.
- 행정동은 metadata exact match와 통용명 alias match를 분리해 기록한다. `잠실2동`과 `잠실동`처럼 alias가 여러 공식 동에 걸릴 수 있으므로 행정동 수치는 정밀 사업구역 수치가 아니다.
- 뉴스는 `context_notes`·`evidence[]`에만 둔다. `reasons`·`counter_evidence`·`fit_tier`·정렬·예측모델 입력으로 사용하지 않는다.

### 미검증·사람 확인 필요

- 빅카인즈 export에서 검색어·기간 메타데이터를 내부적으로 확인할 수 없으므로 실제 다운로드 조건과 manifest CLI 값을 사람이 대조해야 한다.
- 현재 연결은 다운로드 snapshot + 네이버 현재 시점 수동 snapshot이다. 추천 파이프라인은 두 snapshot을 정적으로 읽고 네이버 API를 호출하지 않으며, 뉴스 자동화·예약 갱신도 하지 않는다.
- 기사량과 공식 사업 레코드의 사업 단위 join, 기사 내용의 실제 사업 상태, 다른 검색어·자치구·행정동의 alias coverage는 추가 QA 대상이다.

### 미검증 영역

- 실제 서비스의 UI 입력값과 백엔드 DTO가 이 CLI 인자와 동일한 지역·행정동·업종 필터를 전달하는지는 실행 대상 부재로 미검증이다.
- `data/인허가/음식점_인허가_서울.csv`가 `.gitignore` 대상이므로 commit만 전달받은 환경의 재현성은 별도 보완이 필요하다.

## 네이버 뉴스 현재 시점 수동 snapshot → 추천 RAG 연결 검증 (2026-09-02, GPT/Codex)

### 요약

- **수동 수집: 통과.** Naver API HUB를 수집 스크립트로 현재 시점에 1회 호출했다. 검색어는 `서울시 재개발` 단일, `display=100`, `sort=date`, 원시 100건을 받아 100건을 저장했다.
- **snapshot 고정: 통과.** `retrieved_at_utc=2026-09-02T13:09:00+00:00`, `automation=false`를 manifest에 기록했다. 추천 파이프라인에는 API 호출·예약·백그라운드 갱신 코드가 없고 JSONL + manifest만 읽는다.
- **보존 최소화: 통과.** 제목·발행일·URL·검색어·파생 지역/주제 태그만 저장하며 description/body는 저장하지 않는다.
- **RAG 연결: 통과.** 후보별 자치구·행정동 기사량이 `FC-51_네이버뉴스_시설개발_자치구기사량`·`FC-51_네이버뉴스_시설개발_행정동기사량`으로 `evidence[]`에 연결된다.
- **판정·정렬 격리: 통과.** BigKinds + Naver 포함/뉴스 전체 제외 잠실동 비교에서 19개 후보의 ID·순서·등급·`reasons`·`counter_evidence`·위치·신뢰도 변경이 0건이었다.

### 검증 상세

| 기준 | 판정 | 재검증 근거 |
| --- | --- | --- |
| API snapshot 호출 | 통과 | `scripts/ingest_naver_news_snapshot.py`, `서울시 재개발` 단일 쿼리 응답 100건 |
| 검색 시각·범위 기록 | 통과 | manifest `retrieved_at_utc`, query labels, `sort=date`, `display_per_query=100` |
| 전수 목록 오해 방지 | 통과 | `query_totals`와 “현재 상위 결과 snapshot이며 전체 검색 일치 건수의 전수 목록이 아님” limitation 기록 |
| 정적 파이프라인 | 통과 | `load_naver_news_snapshot()`은 `data/뉴스/naver_news_snapshot.jsonl`만 읽음; API 호출은 ingest 스크립트에만 존재 |
| 후보 evidence 연결 | 통과 | 잠실동 19/19 후보에 네이버 자치구·행정동 evidence, `schema_error_count=0` |
| 판정·정렬 영향 | 통과 | 뉴스 포함/제외 ID·순서·등급·긍정/반대 근거·위치·신뢰도 변경 0 |
| 하네스 계약 | 통과 | `validate_harness.py` PASS |

### 경계면 이슈·운영 규칙

- API의 `total`은 전체 일치 건수 참고값이고, 저장된 100건/쿼리는 현재 상위 결과다. 이를 전체 뉴스 모집단이나 검색량 절대값으로 사용하지 않는다.
- 네이버 뉴스 보도량은 공식 도시계획사업의 확정·추진단계·정확한 사업구역·상권 수요·창업 성공이 아니다. 공식 `data/도시계획사업/`을 보완하는 중립 맥락이다.
- 새 결과가 필요하면 사람 또는 Claude Code가 수동 명령을 실행해야 한다. 자동화·예약 갱신은 현재 설계 범위에 없다.

## 뉴스 snapshot 연결 재검토 — 권한 복구 후 (2026-09-03, Claude Code)

`data/뉴스` 접근 권한 복구 후 빅카인즈 + 네이버 뉴스 연결을 재검토했다. 두 지역(잠실동 19건·연남동 7건)에서 `--no-news-context` 대비 실행 비교 + 코드·스키마·원본 JSONL 확인.

### 요약

- **통과**: 판정·정렬 격리(잠실 19/19·연남 7/7, candidate_id·순서·fit_tier·confidence·reasons·counter_evidence·location 변경 0건 — `_tier()`·정렬 키가 news를 참조하지 않음), 정적 로딩(API 호출 없음), 스키마(`schema_error_count=0`, normalization enum 완비), `run-manifest.json` top-level `source_paths`에 뉴스 JSONL·manifest 4경로 기록, 본문 미저장, 프로파일 §9-1 "신호 없음(서술만)"에 FC-51-news 등재, manifest 산술 일치.
- **조건부(수정 완료)**: 아래 #1, #2·#3·#4·#5(2026-09-03 후속).
- **조건부(기록만, 미수정)**: #6.

### 상세 검증

| 기준 | 판정 | 근거 |
| --- | --- | --- |
| 판정·정렬 격리 | 통과 | 잠실·연남 news on/off 비교 judgment 변경 0, 정렬 순서 동일. news는 `evidence[]`+`context_notes[]`에만 append |
| 정적 로딩 | 통과 | `load_bigkinds_news()`·`load_naver_news_snapshot()`는 JSONL+manifest만 read. API는 `ingest_*` 스크립트에만 |
| 스키마 | 통과 | `schema_error_count=0`. `normalizations_applied` enum에 news 태그 6종 + `dong_common_name_alias` 등록 |
| manifest source_paths | 통과 | 뉴스 4경로 모두 기록(gridpoint 때 지적된 상대경로 누락 버그 재발 없음) |
| 본문 미저장 | 통과 | naver/bigkinds JSONL에 description·body 필드 없음 |

### #1 [Medium] 지역 매칭 정밀도 — 빅카인즈 기사량 과다 계상 (수정 완료 2026-09-03)

- **문제**: `ingest_bigkinds_news.py`의 `_find_terms`가 무경계 부분문자열 매칭(`name in text`)이고, 매칭 텍스트에 빅카인즈 `위치`·`키워드`·`특성추출(상위 50)`·`통합 분류`·`기관` 필드를 모두 포함. `위치`는 기사당 10~20개 지명을 뽑는 느슨한 NLP 추출이라 본문 주제와 무관한 곁다리 행정동까지 그 동의 기사량에 계산됨.
- **확인된 오탐**: 능동 5건(전부 성주군·여의도·건대입구 기사 — "능동적" 등에서 매칭), 항동 7건, 묵동 21건, 번동 21건, 길동 44건 상당수가 해당 행정동과 무관. 네이버 snapshot도 `title + description` 매칭이라 description 스니펫 곁다리 지명이 잡힘.
- **영향**: 판정·정렬 미반영이라 후보 등급은 불변(재확인). 단 `evidence[]`·`context_notes`에 표시되는 행정동·자치구 기사량 숫자가 부풀려져 사람이 근거로 오독. manifest는 이를 `"exact term match"`로 표기 → 실제는 무경계 부분문자열.
- **수정** (`ingest_bigkinds_news.py`·`ingest_naver_news_snapshot.py`·`recommendation_pipeline.py`):
  - 빅카인즈 지역 매칭 = **제목 부분문자열 + `위치` 필드 토큰 정확일치**(`_match_tokens`, 콤마·공백 분리). `키워드`·`특성추출`·`통합 분류`·`기관`은 지역 매칭에서 제외(주제·서울 scope 판정에는 유지).
  - 네이버 지역 매칭 = **제목만** 부분문자열(description은 주제·scope 판정에만, 미저장).
  - `evidence[]` interpretation을 "제목·위치 지역명 매칭"(빅카인즈) / "제목 지역명 매칭"(네이버)로 수정. 행정동 limitation에 "빅카인즈 위치 필드는 다중 지명 추출이라 곁다리 행정동 포함 가능, 제목 언급이 더 강한 신호" 추가.
  - 두 manifest `region_matching` 문구 갱신. `data/뉴스/README.md`에 개정 절 추가.
- **재생성·검증**: 빅카인즈 재-ingest(2522행 불변, sigungu-tagged 1885→1817, dong-tagged 1481→1396). 네이버 JSONL 지역 태그 제목 기준 in-place 재계산(sigungu 39→15, dong 32→23). 오탐 능동 5→1(잔여 1건은 실제 광진구 능동 프로젝트 언급), 항동 7→1, 길동 44→7. 진성 신호 유지(삼성동 220→212, 상암동 117→116, 송파구 132→131, 강남구 398→391). 잠실·연남 news on/off 재비교 judgment 변경 0·정렬 동일·schema 0. `jamsil-coffee-news-mvp`·`yeonnam-coffee-news-mvp`·`-news-redevelopment-current-mvp`·`-news-grid-poi` + no-news QA 재생성. `validate_harness.py` PASS.
- **잔여 한계(수용)**: 묵동 15·번동 13은 빅카인즈가 `위치`에 넣은 오추출 토큰(고척동·면목동 기사)이라 우리 쪽 매칭 로직으로는 제거 불가. limitation·manifest에 명시. `jamsil-coffee-news-both-mvp`는 더 이상 없는 네이버 쿼리(`서울시 재개발 계획`) snapshot 기반이라 재생성 불가 — stale로 남김.

### #3 [Low] news freshness 미추적 (수정 완료 2026-09-03)

- **문제(초기 진술 정정)**: `source_freshness`(후보별)에는 `bigkinds_news`·`naver_news_snapshot` 항목이 이미 있었으나(`recommendation-quality-review.md` 초기 진술의 "`coverage-summary.json`의 `freshness`" 위치 착오), 값이 `observed_end_period` + `periods_behind_latest:0` + `update_cadence:"snapshot"`로 **정적**이었다. 수동 snapshot은 "최신" 기준이 없어 `periods_behind_latest`가 항상 0이므로, 몇 달 지난 snapshot으로 실행해도 stale 신호가 없었다.
- **수정** (`recommendation_pipeline.py` + `rag-evidence-schema.json`):
  - `news_snapshot_age()` 헬퍼 + `NEWS_SNAPSHOT_STALE_DAYS=45` 상수. `observed_end_period`(빅카인즈=검색기간 종료일, 네이버=수집일) 기준 실행일까지 경과일 계산.
  - `source_freshness.{bigkinds_news,naver_news_snapshot}`에 `retrieved_at_utc`(빅카인즈 `ingested_at_utc` 신규 로드)·`snapshot_age_days`·`is_stale`·`stale_threshold_days` 추가, `update_cadence` → `"manual_snapshot"`.
  - `is_stale=true`면 `context_notes`에 재수집 권장 문구 1줄 추가(판정·정렬 미반영). `coverage-summary.json` `news_context`에 `any_stale`·`stale_threshold_days` + source별 `snapshot_age_days`·`is_stale` 추가.
  - 스키마 `source_freshness` `update_cadence` enum에 `manual_snapshot` 추가, 신규 4필드 허용.
- **검증**: 현재 snapshot(age 1~2일) → `is_stale=false`, stale 문구 없음, schema 0. `NEWS_SNAPSHOT_STALE_DAYS=0` 강제 시 `any_stale=true`·context_notes 문구·`is_stale=true`, schema 0. 잠실·연남·역삼1동 news on/off judgment 변경 0·정렬 동일. `validate_harness.py` PASS.

### #2 [Low] 토픽 필터 no-op — metric 이름 과장 (수정 완료 2026-09-03)

- **문제**: bigkinds 2522/2522·naver 100/100 전부 `topic_match=true`(export가 이미 랜드마크·시설·계획 검색, naver 쿼리가 "재개발"). metric 이름 `FC-51_뉴스_시설개발_자치구기사량`이 "주제로 걸러낸 부분집합"을 시사하지만 실제로는 "주제 한정 snapshot 안에서 지역 매칭된 수".
- **수정** (`recommendation_pipeline.py`):
  - metric 이름 `{prefix}_시설개발_자치구기사량` → `{prefix}_자치구기사량`, `_시설개발_행정동기사량` → `_행정동기사량`(빅카인즈·네이버 4종). `metric_name`은 스키마상 자유 문자열이라 enum 영향 없음.
  - `NewsCatalog.topic_match_count` + `news_context_for_candidate`가 `snapshot_total`·`topic_match_rate` 반환. `evidence` interpretation·`context_notes`에 "시설·개발·정비 주제 snapshot(N건, 주제적합률 P%) 중 {지역} 매칭 M건 · 주제태그 {상위 4개}"로 재작성. `topic_match_rate≥0.95`면 "snapshot이 이미 주제 검색이라 topic 필터 거의 전량 통과 — 주제로 걸러낸 부분집합 아님" 문구.
  - `coverage-summary.json` `news_context.sources[]`에 `topic_match_count`·`topic_match_rate`, 최상위에 `any_topic_filter_effective`. `dimension_evidence.미래신호.grain_notes.FC-51-news`도 갱신.
- **검증**: 잠실 evidence "빅카인즈 시설·개발·정비 주제 snapshot(2522건, 주제적합률 100%) 중 송파구 매칭 131건 · 주제태그 랜드마크 131, 시설·계획 131, 재건축 67, 정비사업 57". schema 0, news on/off judgment 0.

### #3 [Low] news freshness 미추적 (수정 완료 2026-09-03)

- **문제(초기 진술 정정)**: `source_freshness`(후보별)에는 `bigkinds_news`·`naver_news_snapshot` 항목이 이미 있었으나(`recommendation-quality-review.md` 초기 진술의 "`coverage-summary.json`의 `freshness`" 위치 착오), 값이 `observed_end_period` + `periods_behind_latest:0` + `update_cadence:"snapshot"`로 **정적**이었다. 수동 snapshot은 "최신" 기준이 없어 `periods_behind_latest`가 항상 0이므로, 몇 달 지난 snapshot으로 실행해도 stale 신호가 없었다.
- **수정** (`recommendation_pipeline.py` + `rag-evidence-schema.json`):
  - `news_snapshot_age()` 헬퍼 + `NEWS_SNAPSHOT_STALE_DAYS=45` 상수. `observed_end_period`(빅카인즈=검색기간 종료일, 네이버=수집일) 기준 실행일까지 경과일 계산.
  - `source_freshness.{bigkinds_news,naver_news_snapshot}`에 `retrieved_at_utc`(빅카인즈 `ingested_at_utc` 신규 로드)·`snapshot_age_days`·`is_stale`·`stale_threshold_days` 추가, `update_cadence` → `"manual_snapshot"`.
  - `is_stale=true`면 `context_notes`에 재수집 권장 문구 1줄 추가(판정·정렬 미반영). `coverage-summary.json` `news_context`에 `any_stale`·`stale_threshold_days` + source별 `snapshot_age_days`·`is_stale` 추가.
  - 스키마 `source_freshness` `update_cadence` enum에 `manual_snapshot` 추가, 신규 4필드 허용.
- **검증**: 현재 snapshot(age 1~2일) → `is_stale=false`, stale 문구 없음, schema 0. `NEWS_SNAPSHOT_STALE_DAYS=0` 강제 시 `any_stale=true`·context_notes 문구·`is_stale=true`, schema 0. 잠실·연남·역삼1동 news on/off judgment 변경 0·정렬 동일. `validate_harness.py` PASS.

### #4 [Low] summary row_count 합산 (수정 완료 2026-09-03)

- **문제**: `news_context.raw_row_count`가 20개월 export 2616 + 2일 snapshot 100을 한 숫자로 합침.
- **수정**: `news_context` 최상위 `raw_row_count`·`normalized_row_count`(합산) 제거, `row_counts_note`("성격이 다른 snapshot이므로 합산하지 않는다 — sources[]의 source별 수 사용")로 대체. 후보별 `source_freshness`·`sources[]`에 이미 source별 수가 분리돼 있음.

### #5 [Trivial] 네이버 ingest help stale (수정 완료 2026-09-03)

- `--query` help "기본 2개" → 실제 1개. 문구 수정 완료.

### #6 (기록만, 미수정)

| # | 심각도 | 항목 | 내용 |
| --- | --- | --- | --- |
| #6 | Trivial | run-manifest `generated_by` 하드코딩 | `recommendation_pipeline.py`가 실행 주체와 무관하게 `run-manifest.json`·`run-notes.md`에 `"GPT(Codex)"`로 기록. 실제 실행자(사람·Claude Code·GPT) 구분 불가 |

### 미검증 영역

- 네이버 `seoul_scope`가 description 기반인데 description 미저장 → JSONL만으로 재현 불가(설계상 폐기, 허용).
- 빅카인즈 export 검색어·기간(`query_metadata_verified=false`, 사람 대조 필요 — 기존 남은 작업).
- 뉴스 오용 방지 결함 케이스: F29 추가(`fault-cases.md`).
- UI·백엔드 DTO 연결은 CLI만 검증 가능(handoff #14).
