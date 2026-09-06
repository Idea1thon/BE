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

서울 전체 25개 구를 적재해도 **요청당 DB 부하가 2개 구 때와 동일**하도록 만든다.
출력(`candidates.json`)은 기존과 **바이트 동일**을 유지한다.

## 세부 작업

### Phase 1 — Seoul-wide 읽기 프로세스 캐시 (이 브랜치)
- [ ] `serving_cache.py` 추가: `(target, data_version, method, params)` 키 기반 인메모리 캐시.
      `meta.dataset_run` 의 최신 `data_version` 이 바뀌면 자동 무효화. 짧은 TTL 병행.
- [ ] `DbSource._layer` / `scope_index` / `environment` / `_anchor_rows` 를 캐시 경유로 변경.
      반환 시 `deepcopy` 하여 호출부 mutation 으로부터 캐시 보호.
- [ ] `FileSource` 는 무변경. `--source files` 경로 영향 없음.
- [ ] A/B 검증: `--source db` vs `--source files` 바이트 동일, `--source db` cold vs warm 동일.

### Phase 2 — 서울 분위 breakpoint 사전계산 (후속 이슈)
- [ ] 오프라인 스크립트: grain·분기·업종별 flow_density / 점포 / 매출 분포의 분위 breakpoint 를
      `metric_snapshot` region grain 으로 적재.
- [ ] `run_pipeline` 이 `all_flow_density` 등을 전체 팩트에서 계산하는 대신 breakpoint 를 읽도록.
- [ ] `scope_index` / `_layer` 를 요청 자치구(+경계 여유)로 실제 스코핑.

### Phase 3 — 연결 재사용 (후속 이슈)
- [ ] `serving_db` 를 쿼리당 psql spawn 대신 세션 재사용(psql `-f` 배치 or psycopg 풀)으로.
- [ ] FastAPI 커넥션 풀 상한, `statement_timeout` 설정.

## 참고 사항

- 근본 병목은 데이터 크기가 아니라 (1) 요청마다 서울 전체 재읽기 (2) psql spawn.
- B1ms 로 데모(동시 1~3)는 Phase 1 만으로 충분. 실서비스 트래픽은 Phase 3 + 스펙업(B2s↑) 필요.
- 관련 파일: `services/recommendation-api/recommendation/{pipeline.py,serving_db.py}`
- 관련 메모: `serving_pipeline_db_source`, `azure_team_db_ideaton`, `songpa_to_seoul_generalization`
