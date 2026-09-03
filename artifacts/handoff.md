# 인수인계

> **2026-09-03 GPT(Codex) 작업 포인터:** 현재 보유 데이터와 외부/API 출처, 키 상태, 추천 사용 분류는 [`artifacts/handoff_data_sources.md`](handoff_data_sources.md)에 통합 기록했다. Claude Code는 API를 다시 탐색하기 전에 이 문서와 `artifacts/10-analysis/data-inventory.md`를 먼저 읽는다. 도로명주소 좌표제공 API(`JUSO_COORD_API_KEY`)는 배포 URL/IP 확보 전 미발급·보류 상태다.
> **2026-09-03 GPT(Codex) 작업 포인터:** 데이터 스키마와 정규화·결합 규칙은 [`artifacts/10-analysis/data-schema.md`](10-analysis/data-schema.md)와 [`artifacts/10-analysis/data-schema.json`](10-analysis/data-schema.json)을 먼저 읽는다. 원천 스냅샷·정규화 계층·후보/RAG Evidence를 분리하고, 상권·배후지·행정동 grain 혼합과 결측·proxy·생성 지점을 금지한다.
> **2026-09-03 GPT(Codex) PostgreSQL 작업 포인터:** PostgreSQL + PostGIS 초기 이식이 완료됐다. 첨부 서버의 `ideaton`은 Postgres.app 서버명으로 확인했고, 로컬 `127.0.0.1:5432`에 프로젝트 DB `ideaton`·PostGIS 스키마·원천 manifest·2026 핵심 지표·crosswalk·역/버스/아파트/Kakao POI 앵커·보조 지표·계획·뉴스 snapshot을 적재했다. 상세 계획·QA·재현 명령은 [`docs/postgres-migration-plan.md`](../docs/postgres-migration-plan.md), 기본 DDL은 [`db/001_location_schema.sql`](../db/001_location_schema.sql), 적재기는 [`scripts/migrate_postgres.py`](../scripts/migrate_postgres.py)·[`scripts/migrate_postgres_context.py`](../scripts/migrate_postgres_context.py)다. 추천 파이프라인은 아직 기존 snapshot 읽기 저장소를 유지하며, DB 조회 결과 동등성 QA 후 단계 전환한다. 이 작업은 **GPT(Codex)**가 수행했다.
> **2026-09-03 GPT(Codex) Docker 공유 DB 작업 포인터:** Postgres.app의 원본 DB와 Docker DB를 분리하고 팀원이 접근할 수 있도록 [`docs/docker-postgres.md`](../docs/docker-postgres.md), [`compose.yaml`](../compose.yaml), [`docker/postgres/Dockerfile`](../docker/postgres/Dockerfile)을 구성했다. Docker 호스트 포트는 Postgres.app 5432와 충돌하지 않도록 55432이며, `.env.docker` 비밀번호 입력, 이미지 빌드, 컨테이너 `healthy` 기동, PostgreSQL 18→16 호환 SQL 복원, `vector` 확장 활성화 및 주요 행 수 대조까지 완료했다. 공용 인터넷 직접 공개는 금지하고 LAN/VPN·방화벽을 사용한다. 이 구성은 **GPT(Codex)**가 수행했다.
> **2026-09-02 GPT(Codex) 작업 포인터:** 카카오맵 POI REST API 이식·실호출·잠실역 샘플·공식 쿼터 가드의 상세 인수인계는 [`artifacts/handoff_kakao_poi.md`](handoff_kakao_poi.md)를 먼저 읽는다. Claude Code는 이 문서와 `artifacts/README.md`를 읽은 뒤 전체 코드 탐색 없이 선택 경계의 POI 배치·상세 맥락 QA 작업으로 들어간다.
> **2026-09-02 GPT(Codex) 작업 포인터:** R-ONE Open API 사용법 확인·실호출·래퍼의 상세 인수인계는 [`artifacts/handoff_rone_api.md`](handoff_rone_api.md)를 먼저 읽는다. Claude Code는 키를 다시 탐색하거나 실제 키를 출력하지 말고, 문서의 재현 명령과 grain 경계를 기준으로 후속 이식 작업을 이어간다.
> **2026-09-02 GPT(Codex) 작업 포인터:** R-ONE 상권 72개와 서울시 상권분석 1,650개 간 1차 crosswalk는 [`artifacts/handoff_rone_trdar_crosswalk.md`](handoff_rone_trdar_crosswalk.md)를 먼저 읽는다. `output/crosswalks/crosswalk_rone_trdar.csv`의 `join_eligible=yes`만 자동 결합 후보이며, 복합·도로 proxy·대상 재사용·미해결은 사람 검토/보류한다.
> **2026-09-02 GPT(Codex) 작업 포인터:** 공통 후보 생성 MVP는 [`scripts/recommendation_pipeline.py`](../scripts/recommendation_pipeline.py)에 구현되어 있다. 전체 코드 탐색 대신 이 파일의 CLI와 `output/recommendation_runs/` 실행 결과를 먼저 확인하고, 다중 지역·업종 QA와 UI/백엔드 DTO 연결을 이어간다.
> **2026-09-02 검증 포인터:** FC 피처 설명력 검증 결과는 [`artifacts/10-analysis/feature-evidential-value.md`](10-analysis/feature-evidential-value.md). **연속 FC 중 폐업/생존 단조 신호 0개** → 학습형 결합 스코어(`fit_index_v1` 가중 모델) 금지. FC 신호 등급표는 프로파일 §9-1.
> **2026-09-02 반영 완료:** `recommendation_pipeline.py`·`sample_jamsil_coffee.py._tier()`에 근거 3분류 적용 — 신호 없음 FC(FC-01 유동밀도·FC-30 등)는 `context_notes`로 분리(판정·정렬 미반영), 약한 배경 신호(FC-08·31·07 bus_n)는 2개 이상일 때만 추천, 정렬 키에서 `-len(reasons)` 제거. `rag-evidence-schema.json`에 `context_notes` 추가, 결함 F26, `candidate-selection-spec.md` §4-1·§5. `qa_poi_context_invariance.py` 10/10 PASS·`validate_harness.py` PASS 유지.
> **2026-09-02 값 수준 QA (P2 #4):** `scripts/qa_value_levels.py` → `artifacts/10-analysis/value-level-qa.md`. 정합성 통과. `당월_매출_금액`=분기합계(단위 `원/점포·분기`로 정정), 극소값 30셀 결측 취급. **상권×업종 매출 커버리지 54% — 다른 grain fallback 안 함(결정)**: 표본 부족 = 소규모 시장 신호이므로 `sales_thin_market`으로 `조건부 검토` 유지 + 사유 명확화("추정매출 미제공 — 표본 임계치 미만, 매출 검증 불가"). 결함 F27, spec §0-6-1.
> **2026-09-02 네이버 계절성 (P2 #3):** `scripts/analyze_naver_seasonality.py` → `artifacts/10-analysis/naver-search-seasonality.md`. 10업종 계절 진폭 1.2~1.6x(봄 peak·여름 trough) — raw 6개월 기울기가 계절 아티팩트(F12). **호프 2026 여름 급등은 계절성 아님**(raw_ratio 상한 100, `surge_active`). `robust_slope12`는 비계절성 지속 여부를 점검하는 분석용 값이며, 후보 파이프라인의 현재 관심도·등급·정렬에는 사용하지 않는다.
> **2026-09-02 GPT(Codex) 검색 관심도 연결:** `scripts/recommendation_pipeline.py`에 FC-42 업종 검색 관심도를 연결했다. 최신월·최근 3개월 **원계열** `rel_index`와 계절 국면을 `context_notes`·RAG `evidence`에 기록하며, 계절 보정 `robust_slope12`·단일 급등은 후보 가점·등급·정렬에 사용하지 않는다. `미검증 급등`은 경고로만 표시한다. `reasons`·`counter_evidence`·`fit_tier`는 기존과 동일. 관련 계약은 `candidate-selection-spec.md` §3·§4-1·§8, `rag-evidence-schema.json`, `naver-search-seasonality.md`다.
> **2026-09-02 반영(사용자 요청):** FC-42 현재 관심도 배수를 `current_lift_vs_all`(최근3개월÷전체5년평균, 장기 우상향 편향) → **`yoy_clean_recent_mean`**(전년 동월 대비·이상치 제외, `naver_seasonality.csv`)로 교체. 상태 라벨 "전년 동월 대비 상승/하락/유사", surge면 "미검증 급등" 우선. 4 canonical mvp 재실행(GPT는 안 함) — FC-42 context 반영·tier 불변·schema 0. `qa_poi_context_invariance` 10/10.
> **2026-09-02 격자 생성 좌표 근거 (사용자 요청):** 개별 매물 크롤링(불법·비확장) 대안. `artifacts/20-method/generated-evidence-schema.json`(`gen-evidence-v1`) + `scripts/generate_gridpoint_evidence.py`. `--all-seoul` = 서울 25개 자치구 각 ~10개(최원점 표본추출) → `output/generated_evidence/<slug>/` + `_seoul_index.json`. **2026-09-02 실행: 250 레코드 schema 0.** 절차: 격자→PIP→반경 150m 영업 중 인허가 ≥3→역·아파트 80m dedup→FPS. `recommendation_pipeline.py --include-generated-points <seeds.json|dir>`로 기존 seed에 **추가**. 생성지점은 `candidate_type=생성지점_격자`·`synthetic_anchor=true`·`spatial_grain=precision=지점(생성)`·`listing_url=address_point=null`·`place_name` 생성 고지·`context_notes[0]` evidence_id 링크. `rag-evidence-schema.json` allOf 조건부 + `--limit` 절단 시 타입 라운드로빈 인터리브(spec §5). 결함 F28. P2 잔여: #1 내국인 생활인구(다운로드), #5 Kakao 다른 지역(GPT). 매출 미제공은 fallback 안 함으로 확정. `_tier` 주의 조건 강화(A+B)는 사용자 새 데이터 대기.
> **2026-09-02 격자 합성 후보 seed 전수감사 + 후속 (GPT/Codex 감사 + 사용자 요청):** `output/generated_evidence/`는 25구 250개 + `송파구-잠실동` 별도 117개. **개별 상가/매물이 아닌 합성 좌표 + 접근성/경쟁 맥락** — 주소·면적·월세·공실·매물 링크 없음(매물 공백 미해소). 25구 대표점은 공식 행정동 425개 중 224개만 실제 레코드. **후속 반영(2026-09-02):** ① 명칭 고정 → `generated-gridpoint-seed-audit.md`(구 -commercial-dataset-). ② `metrics`에 10개 외식 업종별 인허가 수 전부 기록 + `generator.params.industries`. ③ `recommendation_pipeline.py`: `synthetic_anchor=true` 후보는 `추천` 상한을 `조건부 검토`로 캡. **RAG 연결 완료(2026-09-02):** `--include-generated-points`가 `seeds.json` + `gridpoint_evidence.jsonl`을 함께 읽어 합성 후보 evidence에 반경 500m 인허가 경쟁 metric(요청 업종 + 전체) + `context_notes` 10업종 브레이크다운을 연결. 경쟁 규모 맥락이며 `fit_tier`·정렬 미반영. 역·버스·아파트는 실행 시 재계산(정본). 잔여: 행정동 전수·원자료 checksum.

## 현재 완료

- 프로젝트 컨텍스트·데이터·지표·RAG 계약 생성
- 5개 Agent 역할 카드 생성
- 6개 작업 Skill, 1개 legacy 감사 Skill, 1개 Orchestrator 생성
- 데이터 핵심·조건부·운영 제외·추가필요 분류 작성
- 후보 선정·RAG 스키마·초기 QA·최종 시스템 명세 작성
- 구조 검증 스크립트 작성
- 운영 제외 데이터 약 360MB 삭제: 중복 ZIP, 아파트, 로컬브랜드, 기존 Top-K 결과·그래프, 메타파일

## 다음 실행

1. 구조 검증 스크립트 PASS 확인
2. ~~실제 CSV 최신성·키·join coverage 재계산~~ → 완료(2026-09-01, `data-integrity-check.md`)
3. ~~`candidate-selection-spec.md`에 결합 규칙 7개 반영~~ → 완료(0절)
4. ~~`rag-evidence-schema.json` grain·병합 근거 필드~~ → 완료
5. ~~지역 특성 프로파일 정식화~~ → 완료(`regional-characteristics-profile.md`)
6. ~~`candidate-selection-spec.md` 3절 FC id 참조~~ → 완료
7. ~~QA 재검토 → 최종 명세 통합~~ → 완료 (경계면 High 1건 수정·재검증)
8. ~~출력 위치 범위 명확화~~ → 완료 (`location` 블록, `precision`, 크로스워크 연결)
9. 데이터 값 수준 검사(음수·이상치·0 vs 결측), 영역 좌표계 확정, 계단식 as_of 분기. **R-ONE↔상권분석 1차 crosswalk는 생성 완료**했으나 proxy·복합권역 QA가 남음
10. ~~송파동·커피 샘플~~·~~FC-01 정규화~~·~~후보 단위 상권→지점 재설계~~ → 완료(2026-09-02). ~~entry_health 컷~~도 확정·승인
11. 위치: 후보 = 지점(아파트·역). 카카오 POI root seed 샘플은 `recommendation_pipeline.py --include-poi`에 선택 연결했고, `scripts/ingest_kakao_poi_grid.py`가 선택 경계 `rect` 격자·PIP·포화 분할·커버리지 manifest를 구현했다. 잠실동 FD6/CE7은 343행·193 요청·`complete_requested_queries`까지 통과했고, `--include-poi-context`가 이를 후보별 250m/500m RAG 관측으로 연결했다. `scripts/qa_poi_context_invariance.py`의 10개 업종 반복에서 후보·등급·근거·FC-11 라벨 불변 QA도 통과했다. 다른 영역 배치, 출구 좌표·정밀 아파트 경계·매물은 잔여
12. ~~entry_health_v1 컷~~ → 확정·승인(2026-09-02, `entry-health-v1-cut-design.md`). FC-10 = 전 업종 통합 지역 배경 등급, sample `_tier()` 반영, QA #10 해소. **R-ONE↔상권분석 1차 crosswalk 생성 완료**(자동 결합 후보 52/72, review 18, 미해결 1, 대상 재사용 1), MVP는 eligible만 연결. host 최근접은 자치구 가드까지 구현했고 동 불일치 규칙은 잔여. 도로연장 정규화(FC-01 TODO)
13. ~~후보 생성 파이프라인 코드화~~ → 완료(`scripts/recommendation_pipeline.py`, 2026-09-02 GPT/Codex). 지점 seed·grain 선택·판정·RAG schema 검증을 공통 CLI로 일반화
14. UI·백엔드 DTO 연결

## 공통 후보 생성 MVP (2026-09-02, GPT/Codex)

- 실행 파일: `scripts/recommendation_pipeline.py`
- 입력: `--sido`·`--sigungu`·`--dong`(선택)·`--industry-code`·`--special-condition-text`·`--quarter`
- seed: 검증된 아파트·역. `--include-poi`를 주면 현재 `data/카카오POI/` 잠실역 샘플도 선택적으로 합친다. 경계 내부+300m, 80m dedup 규칙을 사용한다.
- 공간 연결: 지점 상권 PIP → 같은 자치구 최근접(최대 300m) → 행정동 배경값. 상권·배후지·행정동 코드를 분리해 기록한다.
- feature: 20261 업종 점포·추정매출·유동·변화지표, 지점 반경 역·버스·아파트, `entry_health_v1`, R-ONE `join_eligible=yes` 임대가격지수 proxy, FC-42 업종 검색 관심도 context.
- 한계 보존: 개별 매물·주소·공실·성공 outcome이 없는 조건은 필터링하지 않고 `missing_features`/`unsupported_conditions`에 남긴다. `fit_index=null`, `score_is_predictive=false`다.
- 산출물: `request.json`, `candidates.json`, `coverage-summary.json`, `run-manifest.json`, `run-notes.md`. FC-42는 `context_notes`·`evidence`에만 기록하며 등급·정렬에 미반영.
- 재현 예:

  `.venv/bin/python3 scripts/recommendation_pipeline.py --sido 서울특별시 --sigungu 송파구 --dong 잠실동 --industry-code CS100010 --special-condition-text '월세 300만원 이하, 20평 이상, 주차 가능' --include-poi --out output/recommendation_runs/jamsil-coffee-mvp --limit 20`

- 검증 결과: 잠실·커피 20건, 연남·커피 5건, 역삼1동·한식 5건 모두 RAG schema 0 오류. 잘못된 시군구 입력은 중단된다. GPT(Codex)가 구현·실행했다.
- 다음: R-ONE `review` 지도 QA, host 최근접 동 불일치 규칙, 값 이상치·greenfield·선택 영역 POI QA, UI/백엔드 DTO 연결.

## 카카오맵 POI 이식 (2026-09-02, GPT/Codex)

- `.env.example`에 `KAKAO_REST_API_KEY`를 추가하고 사용자의 `.env` 키로 키워드·카테고리 실호출을 확인했다.
- `scripts/kakao_local.py`는 키워드 검색·카테고리 검색·coord2address를 제공한다.
- `scripts/ingest_kakao_poi.py`는 Kakao 결과를 `data/카카오POI/`의 UTF-8-SIG CSV와 manifest로 저장한다. WGS84→EPSG:5181 변환, Kakao id dedup, 조회시각·검색조건 보존을 포함한다.
- 실제 샘플: 잠실역 `(127.1000, 37.5133)` 반경 1km, `FD6`+`CE7`, 6회 호출, 원본 90행·중복 제거 90행·좌표 결측 0행.
- **공통 후보 생성 엔진에 선택적으로 연결했다.** `--include-poi`에서 현재 CSV 샘플을 seed로 읽는다. POI는 매물·공실·성공 outcome이 아니며, 현재 추천 규칙·Top-K 감사 경계를 바꾸지 않는다.
- **공식 쿼터 가드 추가:** `KAKAO_LOCAL_*_LIMIT` 기본 일 90,000·월 2,700,000·실행당 500이며, 무료 상한 일 100,000·월 3,000,000보다 큰 설정은 거부한다. 실제 HTTP 시도(재시도·오류 포함)는 `.api_budget.json`에 기록하고 `scripts/kakao_local.py --budget`으로 확인한다.
- 공간 설계: SHP의 상권·배후지·행정동 경계/PIP가 정본이고, Kakao Local은 확정된 경계의 격자/사각형 수집 후 후보 250m·500m 주변의 POI 구성·생활편의·최근접 장소를 보여주는 상세 맥락 층이다. 이로 매물·공실·호실 데이터가 생기지는 않는다.
- 상세 실행 명령과 다음 작업은 [`handoff_kakao_poi.md`](handoff_kakao_poi.md)에 고정했다.

## 시설·개발 뉴스 snapshot (2026-09-02 GPT/Codex · 2026-09-03 Claude Code 재검토)

- 빅카인즈 + 네이버 뉴스 시설·개발 보도량을 `FC-51-news` 중립 맥락으로만 연결. `evidence[]`·`context_notes`에만, `reasons`·`counter_evidence`·`fit_tier`·정렬·`data_confidence` 미반영. 파이프라인은 정적 JSONL만 읽고 API 호출·자동화 없음. 상세: [`handoff_bigkinds_news.md`](handoff_bigkinds_news.md)·[`handoff_naver_news_snapshot.md`](handoff_naver_news_snapshot.md).
- **2026-09-03 재검토 (`recommendation-quality-review.md` 2026-09-03 절)**: 판정·정렬 격리·정적 로딩·스키마·manifest source_paths 통과.
  - **#1 수정 완료**: 지역 매칭이 무경계 부분문자열 + 키워드/특성추출/위치 전체 → 곁다리 지명 오탐. 빅카인즈 제목+`위치` 토큰 정확일치, 네이버 제목만으로 축소. 결함 케이스 F29.
  - **#3 수정 완료**: 수동 snapshot 최신성 추적. `source_freshness.{bigkinds_news,naver_news_snapshot}`에 `snapshot_age_days`·`is_stale`·`retrieved_at_utc`·`stale_threshold_days`(기본 45) + `update_cadence="manual_snapshot"`. stale이면 `context_notes` 1줄 + `coverage-summary.json` `news_context.any_stale`. 임계 상수 `NEWS_SNAPSHOT_STALE_DAYS`. 판정·정렬 불변. `rag-evidence-schema.json` enum·필드 확장.
  - **#2 수정 완료**: metric 이름 `FC-51_뉴스_시설개발_{자치구,행정동}기사량` → `_{자치구,행정동}기사량`. `topic_match_rate`(≈1.0 = snapshot 자체가 주제 한정 검색) + 주제태그 상위 4개를 evidence·context_notes에 노출. `coverage-summary.json` source별 `topic_match_count`·`topic_match_rate`.
  - **#4 수정 완료**: `news_context` 최상위 합산 `raw_row_count`·`normalized_row_count` 제거 → `row_counts_note`. source별 수는 `sources[]`·`source_freshness` 유지.
  - **#5 수정 완료**: 네이버 ingest `--query` help 문구.
- **잔여(미수정, 기록만)**: #6 `run-manifest.json`·`run-notes.md` `generated_by` `"GPT(Codex)"` 하드코딩(실행자 무관).
- `output/recommendation_runs/jamsil-coffee-news-both-mvp`는 없어진 네이버 쿼리(`서울시 재개발 계획`) snapshot 기반이라 재생성 불가 — stale.

## 부분 재실행 묶음 마감 (2026-09-01)

전 산출물 current. 순서: 정합성 검사 → 후보 명세(0절 결합 규칙) → RAG 스키마(feature_build) → 지역 특성 프로파일(FC, entry_health) → 후보 명세(FC id) → RAG 스키마(location) → QA(F01~F11) → 최종 명세. fault-cases에 F09(concat 중복)·F10(grain 오분류)·F11(앵커 없이 매물 표현) 추가.

## 외국인 생활인구 이식 (2026-09-01)

- `data/외국인생활인구/외국인생활인구_행정동_{분기,월}.csv` (장기 OA-14992 + 단기 OA-14993 통합, 집계본만 커밋)
- 재실행: `.venv/bin/python3 scripts/ingest_foreign_resident_population.py` (새 월 파일을 `~/Downloads/서울창업입지_원천데이터/외국인생활인구/`에)
- 202410 해소됨. **20263만 부분분기**(202607) — 2026 Q3 완성되면 재실행
- FC-06a·FC-06b partial 활성. QA needs-review
- 잔여(P3): 내국인 생활인구(SPOP_LOCAL_RESD_DONG)로 "외국인/전체 인구 비율" 분모 — 없어도 서울 분위로 상대 수준 제공 가능

## 원천 데이터 위치 (2026-09-01)

이식 스크립트는 **`~/Documents/서울창업입지_원천데이터/`** 를 기본으로 읽는다 (`.env` 의 `RAW_DATA_DIR` 로 변경, `--src` 로 개별 재정의). `scripts/_raw.py` 가 경로 계산.

| 하위 폴더 | 파일 | 이식 스크립트 |
| --- | --- | --- |
| `외국인생활인구/` | `LONG_FOREIGNER_DONG_*.csv` 43, `TEMP_FOREIGNER_DONG_*.csv` 43 | `ingest_foreign_resident_population.py` |
| `식품인허가/` | `식품_{일반,휴게}음식점_서울특별시.csv` | `ingest_food_license.py` |
| `고용률/` | `고용률.csv` | `ingest_employment_rate.py` |
| `도시계획사업/` | `UQ120_도시계획사업/`, `사업장목록.xls` | `ingest_urban_projects.py`, `ingest_redev_associations.py` |
| `임대동향/` | `임대동향 지역별 {임대료,임대가격지수}_*.csv` 5 (R-ONE) | `ingest_rent_trend.py` |
| `도시철도역사/` | `전체_도시철도역사정보_*.xlsx`, `전체_도시철도노선정보_*.xlsx`, `지하철출입구_버스연계.csv` | `ingest_subway_stations.py`, `ingest_subway_context.py` |
| `버스정류장/` | `서울_버스정류소_*.xlsx`, `전국_버스정류장_*.csv` (+ `_과거스냅샷/`) | `ingest_bus_stops.py` |
| `공동주택/` | `공동주택_단지면적정보.xlsx`, `GIS건물통합정보_서울/AL_D010_11_*.{shp,dbf,…}` (EPSG:5186) | `ingest_apartment_complex.py` (`DATA_GO_KR_SERVICE_KEY` + VWorld. 지오코딩 캐시 `data/공동주택/_geocode_cache.json` 커밋) |
| `_미사용_참고/` | `경제활동인구(시도).csv`, `지하철 리프트 위치정보.csv` (부적합 판정) | — |

새 월/분기 데이터는 같은 파일명으로 해당 하위 폴더에 넣고 재실행.

## R-ONE 임대동향 이식 (2026-09-01)

- `data/임대료/R-ONE_임대동향_분기.csv` (커밋). 재실행: `.venv/bin/python3 scripts/ingest_rent_trend.py`
- FC-20 grain: 권역4 → **R-ONE 상권72**. 현재 이식 CSV에는 공실률이 없고, API 공실률 표는 실호출 확인만 된 상태라 FC-21은 `매장용빌딩...csv` 권역 유지
- `.env.example`에 `RONE_API_KEY` 키 슬롯을 추가했다(2026-09-02). 이후 GPT/Codex가 `scripts/rone_api.py`를 추가하고 공식 API 실호출·응답 스키마를 검증했다. 현재 운영 재현 경로는 CSV 이식본이며, API 자동 갱신과 FC-21 공실률 이식은 잔여다.
- **1차 crosswalk 완료(2026-09-02, GPT/Codex)**: `scripts/build_rone_trdar_crosswalk.py`가 R-ONE 72개 상권명과 서울시 상권분석 SHP 1,650개를 명시적 alias/복합권역/도로 proxy로 연결했다. 산출물은 `output/crosswalks/crosswalk_rone_trdar.csv`(84 mapping rows)와 `crosswalk_rone_trdar_summary.json`이다.

## R-ONE ↔ 서울시 상권분석 crosswalk (2026-09-02, GPT/Codex)

- R-ONE에는 서울시 상권분석서비스와 동일한 폴리곤이 없으므로, 이 결과는 **공간 중첩 매핑이 아닌 명칭·별칭 기반 proxy crosswalk**다. R-ONE 임대료를 대상 서울 상권의 실측 임대료로 바꾸어 표현하지 않는다.
- 결과: R-ONE 72개 중 71개는 하나 이상의 대상 후보를 보유하고, 52개 상권만 `join_eligible=yes` 자동 결합 후보로 남겼다. 18개는 `review`(복합권역·도로명·대표상권 proxy), `테헤란로` 1개는 `unresolved`다. 서울 대상 1개(`3120179`, 양재역)는 두 R-ONE 권역이 재사용하므로 자동 조인에서 제외했다.
- 자동 결합 규칙: `mapping_status=matched` + `mapping_role=primary` + `target_reuse_count=1`인 행만 후보로 사용. `review`, `component`, 재사용 대상, `unresolved`는 R-ONE 값을 상권 feature에 자동 부착하지 않는다.
- 공통 MVP(`scripts/recommendation_pipeline.py`)는 위 규칙의 `join_eligible=yes` 행만 FC-20 임대가격지수 proxy로 결합하며, `review`·재사용·미해결은 서울전체 지수 fallback과 `missing_features`로 남긴다.
- 재현: `.venv/bin/python3 scripts/build_rone_trdar_crosswalk.py`
- 상세 컬럼·방법·다음 조치는 [`handoff_rone_trdar_crosswalk.md`](handoff_rone_trdar_crosswalk.md)에 고정했다.

## 도시철도역사 이식 (2026-09-01)

- `data/도시철도역사/{역사정보_서울,상권_역세권}.csv` (커밋). 재실행: `.venv/bin/python3 scripts/ingest_subway_stations.py`
- 국가철도공단 전체 도시철도역사정보(15013205). WGS84→EPSG:5181, 상권/행정동 point-in-polygon + 폴리곤-점 거리
- **FC-07(대중교통 접근성) 신설·active**. 지점 후보는 역 좌표로 반경 직접 계산(2026-09-02 재설계)
- 개통 스냅샷(개통일·예정역 없음). 역만 — 버스·출구 위치는 잔여

## 실데이터 샘플 + 후보 재설계 + QA 라운드 4 — 잠실동·커피 (2026-09-01~02)

- `scripts/sample_jamsil_coffee.py` → `artifacts/40-sample/jamsil-coffee/{feature-table,candidate-evidence}.json + run-notes.md + qa-review.md` (**설계 기준 샘플**). 공통 실행은 `scripts/recommendation_pipeline.py`로 일반화했다.
- **후보 단위 재설계**: 상권 폴리곤 → **지점(아파트 단지·역)**. 상권 27% 커버·크기 20배 편차. `candidate-selection-spec` §1 전면 재작성, `rag-evidence-schema` location(anchor·host_commercial_area·point)·candidate_type·spatial_grain, 회귀 F20·F21
  - FC-07 역·버스, FC-08 아파트 = 지점 반경 500m 직접 계산(고유값, `grain_is_proxy=false`)
  - FC-01 유동밀도·FC-11·FC-30~32 = 포함 상권 → 없으면 행정동 배경값(`grain_is_proxy`+`host_commercial_area`+`grain_notes`)
  - FC-06a = 장기외국인/상주인구, FC-06b = 단기외국인/유동일평균 (근사·신호, F22)
  - 잠실2·3·7동 지점 16개(아파트12+역4). 스키마 통과, 자동 결함 검사(F01·14·19·20·21·22) 통과. 추천 7·조건부 9
- **QA 라운드 4** (`recommendation-quality-review.md`): Critical 0. 잔여 High(entry_health 컷) **2026-09-02 해소**. Medium 3( R-ONE crosswalk 잔여 QA·host 최근접·행정동 유동밀도)
- **다음**: `join_eligible` 운영 연결은 MVP에 반영됨. 이제 R-ONE `review` 지도 QA → host 최근접 동 불일치 규칙 → 값 수준·greenfield·POI 전역 QA → UI/백엔드 DTO 연결 순서로 진행한다.

## entry_health_v1 등급 컷 확정 (2026-09-02 승인)

- `scripts/design_entry_health_cuts.py` → `output/entry_health/` (units·grade_by_label·weight_sensitivity·cut_candidates.json)
- **grain 재정의**: 처음엔 상권×업종(커피)로 분위를 냈으나 **커피 폐업률·개업률 71% 0** → 무의미. **FC-10 = 전 업종 통합 지역(상권 1,650·행정동 425) 배경 진입 환경 등급** — `상권_변화_지표` 라벨도 전 업종 통합이라 grain 일치. 업종 신호는 FC-11·12·30
- **산식**: `risk = 0.25·폐업률분위 + 0.25·(100−개업률분위) + 0.25·(100−점포증감률분위) + 0.25·라벨리스크·100` (전 업종 통합, 서울 분위)
  - 잠정안 대비: 순개업률→개업률(폐업 이중계상 제거), 동일가중
- **컷**: 20261 서울 사분위 **동결**. 상권 `[41,51,62]` · 행정동 `[42,52,61]`
- **검증**: 등급 × 라벨 단조 정렬(HL 셀 56% → 경계, LH → 양호). 가중치 대안이 경계 등급 20~40% 이동하나 2등급↑ ≤6%
- **판정**: 등급은 **반대근거 1항목**으로만(주의·경계일 때), 게이트·정렬키 금지
- 샘플: 잠실역 상권 LH 양호, 잠실새내역 LL 양호, 관광특구 LL 주의, 주거 행정동 HH 주의. `_tier()` 반영, 스키마 0 오류

## 공동주택(아파트) 이식 (2026-09-01)

- `data/공동주택/{아파트단지_서울,상권_아파트접근성}.csv` + `_geocode_cache.json` (커밋). 재실행: `.venv/bin/python3 scripts/ingest_apartment_complex.py`
- K-apt 15057332(`AptListService4/getSidoAptList4` sidoCode=11) 3,396단지 + 면적 15073269 + **VWorld Search API 지오코딩**(캐시). GIS건물통합정보(15083092, 5186→5181)로 footprint 근사
- 커버리지: **89.7% 좌표 확보**(미매칭 349 = 신축·도시형생활주택·SH임대). 1,461/1,650 상권이 250m 내 단지
- **FC-08(배후 주거단지 규모) 신설·active**. `nearby_anchors` 아파트단지 타입(+`households`) 부분 활성
- K-apt = 의무관리 위주 → **소형 빌라·연립 누락**. `geocode_신뢰도` 컬럼 무시 금지(F18)
- 재시도: 캐시의 `conf=none` 은 재실행마다 자동 재시도(`--keep-none` 으로 끔)
- **잔여**: 미매칭 349, 소형 빌라·연립, 정밀 경계(현재 근사), 재건축 예정

## 버스정류소 + 도시철도 보조 이식 (2026-09-01)

- `data/버스정류장/{버스정류소_서울,상권_버스접근성}.csv` — `scripts/ingest_bus_stops.py` (서울시 OA-15067 스냅샷 + 국토부 15067528 인접분, WGS84→5181)
- `data/도시철도역사/{역출입구_요약,도시철도망계획_노선,도시철도망계획_자치구}.csv` — `scripts/ingest_subway_context.py` (t-data tnSubwayEntrc + 관보 제19878호 이미지)
- **FC-07 철도+버스로 확장**. **FC-52에 자치구 계획 도시철도 신설·연장 노선 수 추가**(자치구 grain, 2020 관보)
- 버스정류소·역사는 **계단식 스냅샷** — 분기 트렌드 금지. 버스 250m 정류소는 1,649/1,650 상권 존재 → 수·유형 비교
- tnSubwayEntrc X/Y는 연계 버스정류장 좌표(출입구 좌표 아님) — 출입구 수·주변건물만 사용
- 과거 버스 월 스냅샷 25개는 `원천데이터/버스정류장/_과거스냅샷/`
- **잔여**: 출구 좌표, 버스 노선·배차, GTX·동북선 등 별도 계획, 아파트 단지 경계(사용자 다음 작업)

## API 키 (2026-09-01)

- `.env.example` → `cp .env.example .env` 후 값 입력. `.env`·`.api_budget.json` 는 gitignore. 로더 `scripts/_env.py`
- `VWORLD_API_KEY` — VWorld 지오코딩, 양방향. 래퍼 `scripts/geocode.py`. **실호출 검증 완료**
- `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` — 데이터랩(NAVER API HUB/NCP 이관). 래퍼 `scripts/naver_datalab.py`가 신규(`naverapihub.apigw.ntruss.com`)→레거시 엔드포인트 자동 시도. **실호출 검증 완료**
- 무료 호출 가드: `naver_datalab` 월 28000회(< 무료 30000)에서 자동 중단. `--budget` 로 사용량 확인
- FC-41·42 이식 완료: `scripts/ingest_naver_trend.py` (재실행 시 74 호출). `data/네이버트렌드/`, 온톨로지 `data/ontology/업종_검색키워드.json`
- 월 갱신: `ingest_naver_trend.py` 재실행 (74/28000). `--only 업종|자치구|행정동` 로 분할 가능
- **온톨로지 변경 시**: `ingest_food_license.py`(업태 매핑) + `ingest_naver_trend.py`(검색키워드) 둘 다 재실행
- 계절성 보정(전년 동월 대비)은 후속 과제

## QA 라운드 3 완료 (2026-09-01)

- 6개 이식 경계면 검증. RAG 스키마 High 2건(evidence grain 자치구·권역, 갱신주기 monthly·반기·스냅샷) 수정·재검증
- `candidate-selection-spec` §3·§8, 프로파일 §8, `final` 갱신. fault F12~F15 추가
- QA·final `current`. 전 산출물 정합. **다음: 실데이터 샘플 QA** (송파 잠실동·커피)

## 자치구 고용률 이식 (2026-09-01)

- `data/고용률/자치구_고용률_반기.csv` (커밋). 재실행: `.venv/bin/python3 scripts/ingest_employment_rate.py`
- grain=자치구·반기. 상권은 소속 자치구값 대리. FC-53 partial

## 도시계획사업·정비사업 이식 (2026-09-01)

- `data/도시계획사업/` (4파일 커밋). 재실행: `scripts/ingest_urban_projects.py` + `ingest_redev_associations.py`
- 원본: `~/Downloads/서울창업입지_원천데이터/도시계획사업/` (UQ120 shp + 사업장목록.xls)
- FC-51·52 partial. 추진단계는 **스냅샷**(발표일 시계열 아님) → "예정" 단정 금지
- 잔여: 랜드마크 단일시설(경기장·전시관 등) 공식 사건

## 식품 인허가 이식 (2026-09-01)

- `data/인허가/음식점_상권분기_패널.csv` (커밋, 13MB) — 상권×업종×분기 영업중·개업·폐업. `audit-only`
- `data/인허가/음식점_인허가_서울.csv` (`.gitignore`, 94MB) — 업소 단위. 재생성: `.venv/bin/python3 scripts/ingest_food_license.py`
- 원천은 `~/Downloads/서울창업입지_원천데이터/식품인허가/`. pyproj 필요(설치됨)
- **분기별 폐업률은 점포데이터 대체 금지**(r=0.11). 점포수 r=0.92는 신뢰. 개별 개·폐점일로 생존분석·greenfield만
- 20263 부분분기. 좌표결측 5.7%, 상권 폴리곤 밖 배정 실패 125k(정상 — 상권분석 데이터가 서울 전역 아님)

## 정합성 검사 핵심 (2026-09-01)

- 데이터 손실 없음. `git status`의 `D` 항목은 사용자의 연도 폴더 재구성 결과.
- 개별 파일 내부는 깨끗. 위험은 전부 연도 분할 파일 병합·연도별 스키마 드리프트.
- 점포·추정매출은 정규화 전 concat 금지 (점포-상권 단순 concat 시 125만 중복키).
- 재실행: `.venv/bin/python3 scripts/audit_data_integrity.py --json output/data_integrity_report.json`

## 주의

- 작업 트리에는 기존 사용자 변경이 많다. 하네스 외 파일을 되돌리거나 정리하지 않는다.
- `.Codex` 경로는 macOS 대소문자 비구분 환경에서 읽기 전용 `.codex`와 충돌해 기존 `.claude` 레이아웃을 사용했다.
- 2026-08-31 삭제 요청이 승인되어 위 항목을 영구 삭제했다. 아파트 3개 CSV는 Git 추적 파일이라 복구 가능하지만, 중복 ZIP·로컬브랜드·Top-K 파생물은 미추적 파일이어서 작업공간에서는 직접 복구할 수 없다.
