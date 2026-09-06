---
name: 수행 작업 (Task)
about: 일반 개발 작업이나 개선 사항을 등록할 때 사용해주세요
title: "[TASK] 서빙 파이프라인 지역 스코핑 / Seoul-wide 읽기 캐싱"
labels: performance
assignees: ''
type: Task
---

## 작업 내용

`services/recommendation-api/recommendation/pipeline.py` 의 `DbSource` 는 추천 요청마다
**서울 전체** 범위를 DB에서 읽는다. 자치구/행정동 선택과 무관하게 매 요청:

- `_layer("commercial_area" | "hinterland" | "admin_dong")` — `location.area` 폴리곤 전량
  (grain별 1,050~1,650행, geometry hex WKB 합 **약 19MB**)
- `scope_index(...)` × 8 — `store/sales/flow_quarter`, `metric_snapshot`(상권변화지표)
  분기×grain(×업종) 슬라이스 전량
- `environment(...)` × 2 — `area_store_totals` 분기 2개 전량
- `_anchor_rows(...)` — `context.anchor_snapshot` 전량

또한 `serving_db.query()` 는 **쿼리 1건마다 `psql` 서브프로세스를 새로 띄운다**
(SSL 핸드셰이크 포함). 요청당 15+ 회.

현재 Azure `ideaton` 에는 강남·송파 2개 구만 적재돼 있다. 서울 전체(25개 구)로
확대하면 위 쿼리들의 요청당 반환량이 약 12배가 되고, B1ms(1 vCPU burstable / RAM 2GB)
에서 동시 요청 시 CPU 크레딧 고갈 → 스로틀 → 지연 폭증이 우려된다.

### 측정치 (로컬 Docker, 서울 전체 데이터 기준)

| 쿼리 | 실행시간 | 비고 |
|---|---|---|
| `_layer` commercial_area | ~17ms | geometry 7MB |
| `scope_index` sales(1분기·상권·업종) | ~4ms | 인덱스 커버 |
| geometry 총 전송량 (3 grain) | — | ~19MB / 요청 |

SQL 자체는 인덱스가 잘 걸려 있어 빠르다. 병목은 **(a) 요청마다 19MB geometry 재전송·재파싱,
(b) 쿼리당 psql 프로세스 spawn**.

### 제약: Seoul-wide 분포가 스코어링 baseline

`scope_index` / `_layer` 결과를 요청 자치구로 좁히면 **출력이 바뀐다**. 이유:

- `run_pipeline` 이 `all_flow_density = sorted(flow_trdar 전체)` 로 **서울 전체 분위(percentile)**
  분포를 만들고 `build_candidate` 의 `pct()` 가 이 분포에 대해 후보의 서울 분위를 매긴다.
- `trdar_area` (= `_layer` 전체의 면적맵)도 이 분포 계산에 쓰인다.
- 관련 메모: "스코어링 시 서울 전체 값 우선", 과거 정렬순서 버그 수정 이력.

따라서 팩트 쿼리를 단순히 `WHERE sigungu_code = ?` 로 좁힐 수 없다. 서울 분위 breakpoint 를
**오프라인 사전계산**해서 `context.metric_snapshot`(region grain)에 저장하고, 요청 경로는
breakpoint + 자치구 스코프 팩트만 읽는 구조로 가야 근본 해결이 된다.

## 작업 목표

서울 전체 25개 구를 적재해도 **요청당 DB 재조회 비용(psql spawn·전송)이 자치구
수에 비례하지 않도록** 만든다. 캐시가 줄이는 것은 DB 재조회이며, `_copy_rows()`·
`_layer()` WKB 파싱·서울 전체 분포 계산은 매 요청 그대로 수행된다(그 부분은 Phase 2).
출력(`candidates.json`)은 기존과 **바이트 동일**을 유지한다.

## 세부 작업

### Phase 1 — Seoul-wide 읽기 프로세스 캐시 (이 브랜치, 완료)
- [x] `serving_db.query()` 에 `(target + 최신 dataset_run)` 스탬프 키 인메모리 캐시.
      `data_version()`(매 요청 `describe()`)이 스탬프를 갱신 → 캐시와 매니페스트 버전 일치.
- [x] 엔트리 최대 수명(`SERVING_CACHE_TTL_SECONDS`) — 부분 적재 실패로 스탬프가 안 바뀌어도
      갱신된 DB 값이 무기한 가려지지 않게 (ziholee P2-1).
- [x] `_CACHE_GEN` 세대 가드 — 진행 중 조회가 초기화를 가로지르면 저장 스킵 (ziholee P2-2).
- [x] 반환 시 행 단위 얕은 복사(`_copy_rows`). RAG 검색 경로는 `use_cache=False`.
- [x] `FileSource` 무변경. A/B: `--source db` cache vs no-cache `candidates.json` 바이트 동일.

### Phase 2 — 서울 분위 breakpoint 사전계산 (후속 이슈)
- [ ] 오프라인 스크립트: grain·분기·업종별 flow_density / 점포 / 매출 분포의 분위 breakpoint 를
      `metric_snapshot` region grain 으로 적재.
- [ ] `run_pipeline` 이 `all_flow_density` 등을 전체 팩트에서 계산하는 대신 breakpoint 를 읽도록.
- [ ] `scope_index` / `_layer` 를 요청 자치구(+경계 여유)로 실제 스코핑 → WKB 파싱·분포 계산도 축소.

### Phase 3 — 연결 재사용·부하 측정 (후속 이슈)
- [ ] `serving_db` 를 쿼리당 psql spawn 대신 세션 재사용(psql `-f` 배치 or psycopg 풀)으로.
- [ ] FastAPI 커넥션 풀 상한, `statement_timeout` 설정.
- [ ] 캐시 미스 single-flight(per-key in-flight 락) — 재시작·전체 무효화 직후 동시 콜드 요청이
      같은 SQL 을 각자 조회하는 것 방지.
- [ ] 실측: 동시 콜드 요청의 지연·부하(순차 반복 벤치로는 부족), 캐시 상주/피크 메모리
      (FIFO 512 는 항목 수 상한일 뿐 — 작은 결과와 11만행 결과가 각 1항목).

## 참고 사항

- 근본 병목은 데이터 크기가 아니라 (1) 요청마다 서울 전체 재읽기 (2) psql spawn.
- Phase 1 은 (2) 의 반복만 제거한다. (1) 의 WKB 파싱·서울 분포 계산은 Phase 2 몫.
- B1ms 로 데모(동시 1~3)는 Phase 1 만으로 충분. 실서비스 트래픽은 Phase 2·3 + 스펙업(B2s↑) 필요.
- 관련 파일: `services/recommendation-api/recommendation/{pipeline.py,serving_db.py}`
- 관련 메모: `serving_pipeline_db_source`, `azure_team_db_ideaton`, `songpa_to_seoul_generalization`
