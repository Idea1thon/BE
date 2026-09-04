# PostgreSQL 마이그레이션 계획

작성일: 2026-09-03
작성 주체: GPT(Codex)

## 현재 상태

PostgreSQL + PostGIS 초기 이식이 완료됐다. 첨부된 Postgres.app 서버 정보의 `ideaton`은 서버 이름으로 확인했고, `127.0.0.1:5432`의 PostgreSQL 18.4에 프로젝트 데이터베이스 `ideaton`을 생성했다. 현재 로컬 OS 사용자 인증으로 접속하며 비밀번호·키는 저장소 문서에 기록하지 않는다.

2026-09-03 기준으로 DDL, 원천 manifest, 공간 정본, 2021~2026 핵심 지표, 교차영역, 앵커, 보조 맥락 snapshot까지 적재하고 기본 QA를 통과했다. 원천 파일은 수정하지 않았다. 아직 추천 파이프라인의 읽기 저장소는 기존 Parquet/DuckDB snapshot이며, PostgreSQL은 팀 공용화를 위한 검증 중인 저장소다.

현재 데이터 디렉터리는 약 1.4GB이며, 연도별 CSV와 JSONL·SHP가 섞여 있다. 이 파일들을 한 테이블에 넣지 않고 다음처럼 분리한다.

```text
meta       : 원천 파일, checksum, 수집일, 행 수, 데이터 버전
location   : 공간 정본, 업종, 분기 점포·매출·유동인구, 후보 anchor
context    : 인구·고용·임대료·계획·POI·뉴스 snapshot
evidence   : 추천 실행·후보·RAG Evidence 원문 JSON
```

공간 PIP·반경 검색은 PostGIS로 수행할 수 있다. 이번 단계에서는 추천 파이프라인을 즉시 DB 조회로 바꾸지 않고, 먼저 DB를 팀 공용 저장소로 구축한 뒤 결과 동등성 QA 후 읽기 어댑터를 연결한다. 기존 로컬 snapshot 기반 실행의 재현성을 유지하기 위한 순서다.

팀 공유용 Docker 구성은 [`docs/docker-postgres.md`](docker-postgres.md), [`compose.yaml`](../compose.yaml), [`docker/postgres/Dockerfile`](../docker/postgres/Dockerfile)에 고정했다. Postgres.app의 5432와 충돌하지 않도록 Docker 호스트 포트는 55432를 사용한다.

초기 적재 QA 결과:

| 대상 | 행 수 | 비고 |
| --- | ---: | --- |
| `meta.dataset_file` | 101 | 원천·생성 파일 manifest |
| `location.area` | 3,146 | 상권 1,650·배후지 1,071·행정동 425, geometry non-null 100% |
| `location.area_crosswalk` | 16,875 | 공간 중첩 + R-ONE proxy 관계 |
| `location.store_quarter` | 568,764 | 2021~2026, 10개 업종, 3 grain |
| `location.sales_quarter` | 404,327 | 2021~2026, 10개 업종, 3 grain |
| `location.flow_quarter` | 66,446 | 3 grain |
| `location.permitted_establishment` | 684,820 | 공공 인허가 원본, 업종 매핑 579,001건, 좌표 결측 39,281건 |
| `location.permit_quarter` | 298,448 | 인허가 기반 분기 패널, audit-only |
| `location.anchor_point` | 15,984 | 역사 411·버스 11,792·아파트 3,396·Kakao POI 385 |
| `context.poi_snapshot` | 433 | Kakao Local snapshot |
| `context.metric_snapshot` | 3,538,673 | 공식 파일/API·R-ONE·검색 트렌드·상주/직장인구 구성 |
| `context.plan_snapshot` | 3,900 | 도시계획·정비사업·도시철도망 계획, 원천 완전 중복 8행 제거 |
| `context.news_snapshot` | 2,622 | BigKinds 2,522·네이버 뉴스 API 100 |
| `evidence.recommendation_run` | 14 | 기존 추천 실행 snapshot |
| `evidence.candidate` | 269 | 기존 후보·RAG Evidence JSON |

기본 QA에서 핵심 PK 중복 0건, metric·인허가 원본 중복 0건, 공간 geometry SRID 오류 0건을 확인했다. 점포 원천 상권 파일은 CP949 인코딩 샘플 경계 문제로 첫 적재에서 제외됐으나, 판정 로직을 수정하고 2021~2026 전체를 재적재하여 상권 258,931행을 포함한 최종 568,764행으로 교정했다. 상권배후지 데이터에는 현재 경계 SHP에 없는 19개 코드가 있어 점포 3,820행·매출 3,116행·유동인구 399행은 삭제하지 않고 orphan으로 보존했으며, geometry 기반 추천 결합에서는 제외해야 한다.

## 생성된 DB 구조

[`db/001_location_schema.sql`](../db/001_location_schema.sql)에 idempotent DDL을 만들었다.

| 스키마 | 주요 테이블 | 내용 |
| --- | --- | --- |
| `meta` | `dataset_file`, `dataset_run` | 원천 파일·manifest·checksum·이식 실행 상태 |
| `location` | `area`, `industry`, `area_crosswalk` | 상권·배후지·행정동 경계와 업종·공간 결합 |
| `location` | `store_quarter`, `sales_quarter`, `flow_quarter` | 핵심 분기 지표 |
| `location` | `permitted_establishment`, `permit_quarter` | 공공 인허가 원본·audit-only 분기 패널 |
| `location` | `anchor_point` | 역·버스·아파트·Kakao POI·생성 지점 |
| `context` | `metric_snapshot` | 업종 없는 배경값과 대리값 |
| `context` | `poi_snapshot`, `plan_snapshot`, `news_snapshot` | POI·도시계획·뉴스 관측 snapshot |
| `evidence` | `recommendation_run`, `candidate` | 추천 실행과 RAG Evidence 원문 |

### 주요 제약

- 모든 공간 geometry는 `EPSG:5181`로 저장한다. 원천 WGS84 좌표는 별도 위·경도 컬럼에 보존한다.
- 분기 핵심 테이블의 기본 키는 `(period, spatial_unit_type, spatial_unit_code, industry_code)`다.
- 업종 없는 배경값은 `context.metric_snapshot`에 저장하고 업종별 행으로 복제하지 않는다.
- R-ONE 대리 결합은 crosswalk에서 `join_eligible=true`인 행만 사용하고 `grain_is_proxy=true`를 유지한다.
- 결측은 `NULL`과 `missing_reason`으로 보존한다. 0으로 대체하지 않는다.
- 생성 격지는 `synthetic_anchor=true`로 저장하고 실제 매물·상가·주소로 표시하지 않는다.
- 뉴스는 본문 없이 메타데이터만 저장하며 공식 사업 상태·미래 성공·수요·공실로 해석하지 않는다.

## 권장 이식 순서

### 1단계: DB 생성과 파일 등록

새 환경을 준비할 때는 PostgreSQL + PostGIS 대상에서 아래처럼 DDL을 실행한다. 현재 로컬 `ideaton`에는 이미 적용되어 있다. 실제 비밀번호나 키를 문서에 넣지 않는다.

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/001_location_schema.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/002_commercial_building.sql   # 건축물대장 상업용 건물 (context)
```

그 다음 `scripts/migrate_postgres.py --phase metadata`로 `data/`와 `output/crosswalks/`의 파일별 상대경로, 원천 출처, encoding, SHA-256, manifest를 등록한다.

### 2단계: 정본·핵심 데이터 이식

다음 순서로 이식한다.

1. `area`: 상권 1,650·배후지 1,071·행정동 425와 SHP geometry — 완료
2. `industry`: 10개 업종과 온톨로지 — 완료
3. `area_crosswalk`: 상권↔행정동·배후지·R-ONE 관계 — 완료
4. `store_quarter`, `sales_quarter`, `flow_quarter`: 2021~2026 보유 분기 — 완료
5. `permitted_establishment`, `permit_quarter`: 공공 인허가 원본·분기 패널 — 완료. 점포·매출 대체 금지
6. `anchor_point`: 역·정류장·아파트·Kakao POI — 완료

연도별 점포·매출 파일은 2025/2026 스키마 드리프트와 중복 분기를 먼저 정규화한다. 원천 컬럼명과 최종 매핑은 [`docs/data-schema.md`](data-schema.md) 및 [`artifacts/10-analysis/data-schema.json`](../artifacts/10-analysis/data-schema.json)을 기준으로 한다.

### 3단계: 보조 데이터 이식 — 완료(초기 범위)

상주·직장인구, 외국인 생활인구, 고용률, 임대료·R-ONE, 도시계획, POI, 네이버 트렌드, BigKinds·네이버 뉴스 snapshot을 `context`에 이식했다. 실행 스크립트는 [`scripts/migrate_postgres_context.py`](../scripts/migrate_postgres_context.py)다.

건축물대장(GIS건물통합정보 파생) 상업용 건물 115,141동은 `context.commercial_building`에,
상권 × 스냅샷 요약은 materialized view `context.commercial_building_area_summary`에 적재한다.
`--phase building`은 `db/002_commercial_building.sql` 적용과 `scripts/ingest_building_ledger.py`
실행(→ `data/건축물대장/상가건물_서울.csv`)을 선행한다. 건물 주용도 기준이며 매물·공실·임대료가 아니다.

이 데이터들은 모두 같은 분기 테이블로 취급하지 않는다.

- 상주·직장인구·역·버스·아파트: 수준 또는 snapshot
- 외국인 생활인구·검색 트렌드: 분기/월 계열
- 고용률: 자치구 반기
- 임대료: 권역·R-ONE 조사상권 proxy
- 뉴스: 검색 실행 시점 snapshot

### 4단계: Evidence 연결 후 전환 여부 결정

기존 `output/recommendation_runs/` JSON 14개 실행과 후보 269개를 `evidence`에 보관했다. 다음으로 로컬 snapshot과 DB 조회 결과를 대조한다.

- 로컬 snapshot 실행 결과와 DB 조회 결과의 후보·근거·등급이 같은가
- 키 중복과 many-to-many가 없는가
- 상권·배후지·행정동의 grain이 섞이지 않았는가
- 결측이 0으로 변환되지 않았는가
- source path, source period, proxy, synthetic flag가 유지되는가

QA가 통과한 뒤에만 `scripts/recommendation_pipeline.py`의 저장소 어댑터를 DB로 전환한다. PostgreSQL이 추가되었다고 해서 기존의 증거 중심·규칙 기반 추천을 확률 예측 모델로 변경하지 않는다.

## 팀원이 준비해야 할 것

현재 로컬 이식은 완료됐고, 팀 공유·배포 단계에서는 아래 중 하나의 공용 접속 대상이 추가로 필요하다.

1. 팀 공용 PostgreSQL + PostGIS의 `DATABASE_URL`
2. 로컬 Docker/Podman에서 실행할 PostgreSQL + PostGIS
3. 개발용 PostgreSQL 서버의 host, port, database, user, password, SSL 설정

현재 `.env.example`에는 다음 DB 환경변수 슬롯이 추가되어 있다. 실제 값은 `.env`에만 둔다.

```text
DATABASE_URL=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=seoul_location
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_SCHEMA=location
POSTGRES_SSLMODE=prefer
```

현재 로컬 재현 명령은 다음과 같다.

```bash
.venv/bin/python3 scripts/migrate_postgres.py --phase metadata
.venv/bin/python3 scripts/migrate_postgres.py --phase reference
.venv/bin/python3 scripts/migrate_postgres.py --phase areas
.venv/bin/python3 scripts/migrate_postgres.py --phase core --years 2021 2022 2023 2024 2025 2026
.venv/bin/python3 scripts/migrate_postgres_context.py --phase crosswalks
.venv/bin/python3 scripts/migrate_postgres_context.py --phase anchors
.venv/bin/python3 scripts/migrate_postgres_context.py --phase context
.venv/bin/python3 scripts/ingest_building_ledger.py
.venv/bin/python3 scripts/migrate_postgres_context.py --phase building
.venv/bin/python3 scripts/migrate_postgres_context.py --phase evidence
```

Docker `.env.docker` 비밀번호 입력, 컨테이너 기동, Postgres.app→Docker 데이터 복원 및 주요 행 수 대조는 완료됐다. PostgreSQL 18 원본을 PostgreSQL 16 Docker 대상으로 복원하기 위해 일반 SQL 덤프에서 `transaction_timeout` 설정을 제외했으며, Docker DB에는 `vector` 확장도 활성화했다. 다음 작업은 ① DB 조회 결과와 기존 snapshot 후보·RAG 결과 동등성 QA, ② 읽기 어댑터를 통한 추천 파이프라인의 단계적 전환, ③ 팀 LAN/VPN 방화벽·접속 테스트다.
