# 카카오맵 POI 이식 인수인계

> **작업 주체: GPT(Codex)**  
> 작업일: 2026-09-02 (Asia/Seoul)  
> 다음 담당: Claude Code  
> 목적: Claude Code가 이 작업을 이어받을 때 저장소 전체를 다시 탐색하지 않도록, 변경 파일·검증 결과·다음 구현 경계를 고정한다.

## 1. 결론과 현재 상태

- 카카오 REST API 키(`KAKAO_REST_API_KEY`)는 `.env`에 입력되어 있고 실제 호출이 성공했다.
- 키워드 검색 `잠실역`은 HTTP 200으로 3개 문서를 반환했다.
- 카테고리 검색 `FD6(음식점)`·`CE7(카페)`는 잠실역 중심 반경 1km에서 6회 호출하여 90개 문서를 반환했다.
- 90개 모두 좌표가 있었고, Kakao place id 기준 중복 제거 후에도 90행이었다.
- 원본 WGS84 좌표와 기존 서울 공간 데이터 결합용 EPSG:5181 좌표를 모두 저장했다.
- **POI seed 수집·정규화와 공통 후보 파이프라인의 선택 연결까지 완료됐다.** `scripts/recommendation_pipeline.py --include-poi`가 현재 CSV를 추가 seed로 읽는다. 다만 잠실역 1km 샘플뿐이므로, 다른 선택 영역의 후보 수·품질·우선순위는 아직 검증되지 않았다.
- **2026-09-02 GPT(Codex)가 공식 쿼터 기반 영속 호출 가드를 추가했다.** 일·월·실행당 한도를 모두 통과한 실제 HTTP 시도만 전송하며, 재시도·HTTP 오류도 보수적으로 카운트한다.
- **2026-09-02 GPT(Codex)가 선택 영역 `rect` 격자 수집기와 커버리지 QA를 구현·실행했다.** 잠실동 `FD6`·`CE7` dry-run은 500m 셀 35개, 최소 70개 논리 요청을 계획했고, 실제 50m 최소 셀 파일럿은 193회 논리 요청으로 343개 POI(FD6 258·CE7 85)를 수집했다. 20개 포화 셀을 분할한 뒤 `coverage.status=complete_requested_queries`를 통과했다. 작은 사각형의 실제 카테고리 호출도 `total=8`, 반환 1건으로 성공했다.
- **2026-09-02 GPT(Codex)가 완결 POI context를 RAG Evidence에 연결했다.** `recommendation_pipeline.py --include-poi-context`는 요청 영역과 정확히 일치하는 complete manifest만 읽어 후보별 250m·500m 카테고리 관측값으로 만든다. 잠실동·커피 실행 27개 모두 스키마 통과했으며, context 유무 비교에서 `fit_tier`·정렬·긍정/반대 근거 변화가 0건이었다.

상태 분류는 `부분 이식·조건부 사용`이다. 잠실역 샘플을 서울 전체 커버리지로 일반화하지 않는다.

## 2. 변경 파일과 출력물

### 코드

| 파일 | 책임 | Claude Code가 먼저 확인할 부분 |
| --- | --- | --- |
| `scripts/kakao_local.py` | Kakao Local REST API의 키워드 검색·카테고리 검색·좌표→주소 호출 | `KAKAO_REST_API_KEY`를 `scripts/_env.py`의 `require()`로 읽음. 키를 URL·로그에 넣지 않음. `--budget`으로 네트워크 없이 예산 확인 |
| `scripts/_budget.py` | API별 일·월·실행당 영속 호출 가드 | `.api_budget.json`에 기록. Kakao는 실제 HTTP 시도 직전에 증가하므로 재시도·HTTP 오류도 반영 |
| `scripts/ingest_kakao_poi.py` | 중심점·반경의 검색 결과를 CSV로 정규화, EPSG:5181 변환, place id 중복 제거, manifest 생성 | 기본 `--max-pages=3`, `--max-requests=100`; manifest에 호출 전후 예산 snapshot도 저장 |
| `scripts/ingest_kakao_poi_grid.py` | 선택 영역 폴리곤을 EPSG:5181 격자로 나눠 Kakao `rect` 검색, PIP·ID 중복 제거·포화 재분할·커버리지 manifest 생성 | `--dry-run`으로 요청 수부터 확인한다. Kakao의 `pageable_count` 최대 45문서보다 `total_count`가 크면 4분할한다. 결과는 `data/카카오POI/context/`에 격리한다. |
| `scripts/recommendation_pipeline.py` | 지점 후보·기존 RAG Evidence + 선택적 POI context 반경 집계 | `--include-poi-context`는 동일 시도·시군구·요청 동의 `complete_requested_queries` manifest가 있어야만 쓴다. POI는 `지점` grain 상세 관측이며 등급·정렬·성공 outcome에는 미사용 |
| `scripts/qa_poi_context_invariance.py` | context 없음/있음 실행 쌍의 F24·F25 불변성 QA | 후보 ID·순서·등급·긍정/반대 근거·FC-11·`entry_health` 라벨과 POI evidence grain·출처를 검사한다. 기본은 잠실동의 10개 업종이며 API를 호출하지 않는다. |
| `.env.example` | 카카오 REST 키·호출 한도 입력 슬롯 | `KAKAO_REST_API_KEY=`가 실제 백엔드 POI용. 기본 `KAKAO_LOCAL_*_LIMIT`은 공식 무료 쿼터보다 낮음. `KAKAO_JAVASCRIPT_KEY`는 지도 화면용 선택 슬롯 |

### 실제 검증 산출물

| 파일 | 내용 |
| --- | --- |
| `data/카카오POI/카카오_로컬_POI_잠실역.csv` | 잠실역 중심 `(127.1000, 37.5133)`, 반경 1,000m, `FD6`+`CE7`, 90행 |
| `data/카카오POI/카카오_로컬_POI_잠실역_manifest.json` | 조회 시각, 검색 조건, 호출 수, 좌표계, 원본/중복 제거 행 수, 후보 사용 제한 |
| `data/카카오POI/context/카카오_로컬_POI_격자_송파구-잠실동.csv` | 잠실2·3·7동 alias 경계, `FD6`+`CE7`, 343행(2026-09-02 UTC 스냅샷). 상세 맥락용이며 seed 자동 입력 아님 |
| `data/카카오POI/context/카카오_로컬_POI_격자_송파구-잠실동_manifest.json` | 193 논리 요청, 218 중복 제거, 포화 셀 20개 분할, 102 leaf 질의 완료, `complete_requested_queries` |

CSV 핵심 컬럼은 `poi_id`, `place_name`, `category_name`, `category_group_code`, `road_address_name`, `address_name`, `x_wgs84`, `y_wgs84`, `x_5181`, `y_5181`, `place_url`, `distance_m`, `search_mode`, `search_query`, `search_rect`, `grid_cell_id`, `retrieved_at_utc`이다. `search_rect`·`grid_cell_id`는 격자 수집 결과에서만 채워진다.

## 3. 재현 명령

프로젝트 루트(`/Users/parkjunwoo/Documents/data-analysis`)에서 실행한다.

```bash
# API 래퍼 단일 키워드 확인
.venv/bin/python3 scripts/kakao_local.py \
  --query 잠실역 --x 127.1000 --y 37.5133 --radius 1000 --size 3 --sort distance

# 선택 영역의 POI를 정규화해 저장
.venv/bin/python3 scripts/ingest_kakao_poi.py \
  --x 127.1000 --y 37.5133 --radius 1000 \
  --category FD6 --category CE7 \
  --max-pages 3 --max-requests 10 \
  --out data/카카오POI/카카오_로컬_POI_잠실역.csv

# 선택 영역 격자 계획만 확인 (API 호출 없음)
.venv/bin/python3 scripts/ingest_kakao_poi_grid.py \
  --sigungu 송파구 --dong 잠실동 \
  --category FD6 --category CE7 --dry-run

# 사람 승인·예산 확인 뒤에만 실제 격자 수집
.venv/bin/python3 scripts/ingest_kakao_poi_grid.py \
  --sigungu 송파구 --dong 잠실동 \
  --category FD6 --category CE7 \
  --cell-size-m 500 --min-cell-size-m 125 --max-api-requests 300

# 완결 context를 기존 후보의 250m·500m RAG 관측으로 연결
.venv/bin/python3 scripts/recommendation_pipeline.py \
  --sido 서울특별시 --sigungu 송파구 --dong 잠실동 \
  --industry-code CS100010 --include-poi --include-poi-context \
  --out output/recommendation_runs/jamsil-coffee-poi-context-mvp

# 네트워크 없이 context 불변성 QA를 10개 업종에 반복
.venv/bin/python3 scripts/qa_poi_context_invariance.py
```

API 키는 출력·manifest·CSV에 저장하지 않는다. 이미 생성된 샘플을 확인할 때는 네트워크 호출 없이 아래만 실행한다.

```bash
.venv/bin/python3 -m py_compile scripts/kakao_local.py scripts/ingest_kakao_poi.py scripts/ingest_kakao_poi_grid.py
head -n 4 data/카카오POI/카카오_로컬_POI_잠실역.csv
cat data/카카오POI/카카오_로컬_POI_잠실역_manifest.json
.venv/bin/python3 scripts/kakao_local.py --budget
```

기존 프로젝트 스크립트와 동일하게 두 파일은 `scripts/`를 실행 경로로 전제한다. 다른 Python 코드에서 모듈로 import해 단위 검증할 때만 `PYTHONPATH=scripts`를 붙인다.

## 4. 추천 설계와의 연결 규칙

현재 정본은 `artifacts/20-method/candidate-selection-spec.md` §1~§2다.

1. 후보는 상권 폴리곤 자체가 아니라 좌표를 가진 명명된 지점이다.
2. 기존 seed는 신뢰도 `high/medium`인 아파트 단지와 역이다.
3. 루트 `data/카카오POI/*.csv`의 Kakao POI는 **선택적 추가 seed**다. 후보 파이프라인 연결 시 `source_type=observed_poi`와 원본 `poi_id`, 조회시각, 검색조건을 보존한다. `data/카카오POI/context/`의 격자 수집 결과는 seed로 자동 로드하지 않는다.
4. 선택 지역 경계 안쪽과 경계에서 300m 이내만 후보 seed로 검토하고, 80m 이내 지점은 기존 병합 규칙을 적용한다. 최종 seed 범위는 후보 파이프라인에서 한 곳으로 고정해야 한다.
5. 경계 폴리곤·상권/배후지/행정동 겹침은 프로젝트 SHP와 PIP가 **공간 정본**이다. Kakao Local은 확정 경계 안에서 여러 중심점·격자/사각형을 조회해 각 지점의 250m·500m 주변 업종 구성·생활편의·최근접 POI를 관측하는 **상세 맥락 층**으로 쓴다.
6. POI가 있다는 사실은 영업 중 장소가 관측되었다는 뜻이지, 빈 점포·임대 매물·수익성·신규 창업 성공을 뜻하지 않는다. 정확한 월세·면적·주차·호실은 별도 검증 매물 원천 없이는 여전히 `unsupported`다.
7. Kakao의 `FD6`·`CE7`은 음식점·카페 카테고리 샘플일 뿐 상가·근린생활시설 전체를 100% 커버하지 않는다. 카테고리 열거·중복제거·누락/페이지 종료 QA 전에는 “상가 전체 커버”로 표현하지 않는다.
8. 추천 판정은 계속 증거 중심 규칙 기반이다. 이번 이식은 seed 공간과 관측 맥락을 보강할 뿐 Top-K 성공확률 모델이나 학습 라벨을 추가하지 않는다. 기존 `scripts/phase1_analysis/scoring_topk_recommend.py`는 오프라인 기준선 감사용이다.

## 5. 좌표·데이터 품질 경계

- Kakao 원본 `x/y`는 경도/위도 WGS84(EPSG:4326)이다.
- `x_5181/y_5181`은 `pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)`로 만든 파생 좌표다.
- 중복 키는 Kakao `id` 우선, id가 없을 때만 장소명+좌표 fallback이다.
- 동일 장소가 여러 검색 카테고리·질의에 걸리면 카테고리와 검색 조건을 ` | `로 병합하고, 거리는 최솟값을 보존한다.
- 격자 수집기는 Kakao가 반환한 사각형 안 결과를 프로젝트 선택 폴리곤에 다시 PIP한다. 각 셀·질의별 페이지 수, 경계 밖 제거 수, `total_count`·`pageable_count`, 포화·부분 상태를 manifest에 남긴다.
- Kakao의 `pageable_count`는 최대 **45문서**다. `total_count > pageable_count` 또는 관측 가능 3페이지 안에 종료되지 않은 셀·질의는 포화로 보고 최소 셀 크기까지 4분할한다. 최소 크기에서도 포화·오류·요청 상한에 걸리면 `partial_*` 상태이며 전체 커버리지를 주장할 수 없다.
- `retrieved_at_utc`는 조회 스냅샷 시각이다. 분기 시계열이나 미래 전망 데이터가 아니다.
- 주소와 `place_url`은 Kakao가 반환한 POI 문맥이다. 정확한 임대 매물 주소·호실·월세를 생성하지 않는다.
- 공식 무료 쿼터(2026-09-02 확인): 키워드 장소 검색과 카테고리 장소 검색은 각각 **일 100,000건**, 전체 API 무료 쿼터는 **월 3,000,000건**이다. 문서의 수치는 변경될 수 있으므로 대량 실행 전 공식 문서·개발자 콘솔을 다시 확인한다.
- 기본 가드: `KAKAO_LOCAL_DAILY_LIMIT=90000`, `KAKAO_LOCAL_MONTHLY_LIMIT=2700000`, `KAKAO_LOCAL_PER_RUN_LIMIT=500`. 환경변수는 무료 상한(일 100,000·월 3,000,000)보다 크게 설정할 수 없고, `scripts/kakao_local.py --budget`으로 사용량을 본다.
- `--max-requests`는 이번 ingest의 **정상 응답 논리 페이지** 추가 상한이다. 영속 가드는 HTTP 재시도·오류를 포함한 실제 전송 시도에 적용한다. 대량 수집은 경계·격자 계획, 기존 manifest, `.api_budget.json`을 먼저 확인하고 중복 실행을 피한다.

## 6. Claude Code의 다음 작업 순서

전체 코드 탐색을 다시 하지 말고 아래 순서로 시작한다.

1. 이 문서와 `artifacts/README.md`를 읽는다.
2. `artifacts/20-method/candidate-selection-spec.md` §1~§2, `.claude/context/data-catalog-contract.md`, `artifacts/final/recommendation-system-spec.md`의 seed·precision·grain 규칙만 확인한다.
3. 현재 `recommendation_pipeline.py --include-poi` 연결을 유지하되, POI를 기본 필수 데이터로 승격하지 않는다.
4. 잠실동 FD6/CE7 파일럿은 `complete_requested_queries`까지 통과했다. 다른 선택 경계·카테고리를 수집할 때는 `scripts/ingest_kakao_poi_grid.py --dry-run`으로 요청 수를 검토하고, 예산 확인 뒤 실제 수집한다. manifest가 `partial_*`이면 수집 범위를 축소·재개하거나 부분 관측으로만 기록한다.
5. `complete_requested_queries`는 **입력한** 카테고리/키워드·경계의 페이지 순회가 끝났다는 뜻일 뿐, Kakao 전체 상가·공실·매물·성공 outcome의 완전성이 아니다. 이 조건을 통과한 뒤에도 POI는 후보 주변 250m·500m 관측 맥락(카테고리 구성·최근접 시설·밀도)의 보조 evidence로만 쓴다.
6. 잠실동·커피 RAG context 연결은 `output/recommendation_runs/jamsil-coffee-poi-context-mvp/`에 저장됐다. `scripts/qa_poi_context_invariance.py`로 잠실동 10개 업종은 F24·F25 반복 QA를 모두 통과했다. 다른 지역은 완결 context가 없으면 `--include-poi-context`를 붙여도 POI evidence가 추가되지 않고 `missing_features`/coverage로 남는다. `precision`, `host_commercial_area`, `spatial_grain`, `source_type`, `grain_is_proxy`와 POI 스냅샷 시각이 RAG Evidence에 역추적되는지 QA한다. **반드시 context 없음 실행과 비교해 후보 ID·순서·`fit_tier`·긍정/반대 근거·FC-11(`상권_변화_지표`) 코드가 불변인지 확인한다(F24·F25).**
7. 샘플 외 다른 지역·업종·POI 밀도에서 후보 수와 80m 병합 결과를 검증한다. 검증 전 `current`로 승격하지 말고 `needs-review` 또는 부분 이식으로 기록한다.
8. 대량 수집·외부 서비스 배포·공모전 제출 전에는 Kakao 이용약관/쿼터와 사람 승인 게이트를 확인한다.

## 7. 문서 상태 갱신 지점

이번 변경으로 이미 갱신한 문서는 다음과 같다.

- `artifacts/10-analysis/data-acquisition-sources.md`: Kakao 발급·실호출·부분 이식 상태
- `artifacts/10-analysis/data-inventory.md`: 샘플 데이터 재고
- `artifacts/10-analysis/data-usage-classification.md`: 조건부 seed 분류와 금지 용도
- `.claude/context/data-catalog-contract.md`: POI/아파트 경계 상태를 partial로 변경
- `artifacts/final/recommendation-system-spec.md`: +300m seed 범위, API 키, 다음 작업
- `artifacts/README.md`: 부분 재실행 기록과 산출물 지도
- `artifacts/30-review/recommendation-quality-review.md`: POI context 불변성 QA와 F24·F25
- `artifacts/evals/regression/fault-cases.md`: partial/다른 영역 context·FC-11 라벨 오염 방어
- `artifacts/handoff.md`: 이 문서로 진입하는 다음 실행 포인터

문서 간 충돌이 발견되면 최신 이식 상태는 이 파일과 `artifacts/README.md`를 우선하고, 최종 판정 규칙은 `candidate-selection-spec.md`를 우선한다. `.Codex`가 아니라 프로젝트의 실제 하네스 경로인 `.claude`를 사용한다.

이 인수인계의 구현·검증 기록은 **GPT(Codex)가 2026-09-02에 작성·수정했다.**
