# BE — 서울 창업 입지 추천 백엔드

서울시 상권분석 데이터 + 이식 데이터를 근거로, 사용자가 고른 지역·업종·특별조건 안에서
조건 충족 지점 후보를 **추천 / 조건부 검토 / 주의** 로 제시하고 각 후보에
**추천 근거 · 반대 근거 · 누락 데이터 · 기준 분기 · 공간 단위 · 출처** 를 함께 반환한다.

성공 확률을 예측하지 않는다. 등급은 조건·근거 기반이며 응답은 항상
`score_is_predictive=false` 를 동반한다 ([`artifacts/adr/ADR-001-evidence-first-ranking.md`](artifacts/adr/ADR-001-evidence-first-ranking.md)).

## 파이프라인 흐름

```
지역 선택 + (선택)자연어 입력
  → LLM 입력 해석·분석 계획 (llm_input_planner, 미설정 시 결정론적 최소 파서)
  → 결정론적 계약 검증
  → 읽기 전용 데이터 분석 (PostgreSQL)
  → 후보·Evidence 생성 (하드 조건 → 지점 seed → host 상권/행정동 배경 → 반경 지표 → 7차원 판정)
  → 검증된 후보만 등급 분류
  → LLM 설명 생성 (llm_explanation, 실패·미설정 시 템플릿 폴백)
```

## 구성

| 경로 | 내용 |
| --- | --- |
| `scripts/recommendation_pipeline.py` | 추천 실행 입구. `--source db`(기본) = PostgreSQL, `--source files` = 원천 CSV/shp |
| `scripts/serving_db.py` | 읽기 전용 PostgreSQL 접근 (psql 서브프로세스, 무의존) |
| `scripts/llm_input_planner.py` · `llm_explanation.py` · `llm_runtime.py` | LLM 입력 해석 / Evidence 설명 / hosted 클라이언트 (모두 결정론적 폴백 포함) |
| `scripts/migrate_postgres.py` · `migrate_postgres_context.py` | 원천 CSV/shp → PostgreSQL (PostGIS·pgvector) 이식 |
| `scripts/ingest_*.py` | 이식 데이터 수집 (K-apt·인허가·R-ONE·네이버·뉴스·카카오 POI) |
| `db/001_location_schema.sql` | DB 스키마 |
| `docker/` · `compose.yaml` | PostgreSQL 16 + PostGIS 3 + pgvector 컨테이너 |
| `artifacts/` | 입력·후보·근거·데이터 계약, 설계 결정(ADR), 검증 기록 |
| `.claude/` | 증거 중심 추천 하네스 (에이전트·스킬·컨텍스트 계약) |
| `docs/architecture/` | 웹서비스 시스템 아키텍처 |

**원천 데이터(`data/`, `output/`)는 저장소에 없다.** `--source db` 는 PostgreSQL 에서만
읽으므로 데이터 파일이 필요 없다. 이식하려면 원천 CSV 를 별도로 받아야 한다 —
[`docs/postgres-migration-plan.md`](docs/postgres-migration-plan.md) · [`docs/data-schema.md`](docs/data-schema.md).

## 빠른 실행

```bash
python3 -m venv .venv
.venv/bin/pip install pyshp shapely pyproj jsonschema
cp .env.example .env            # DATABASE_URL 등 채우기
cp .env.docker.example .env.docker

docker compose up -d db
psql "$DATABASE_URL" -f db/001_location_schema.sql
# 이식 (원천 CSV 필요 — docs/postgres-migration-plan.md)
.venv/bin/python3 scripts/migrate_postgres.py --phase all
.venv/bin/python3 scripts/migrate_postgres_context.py --phase all

# 추천 실행 (데이터 파일 불필요)
.venv/bin/python3 scripts/recommendation_pipeline.py \
  --sido 서울특별시 --sigungu 송파구 --dong 잠실동 --industry-code CS100010
```

## 개발 문서

- 시스템 아키텍처: [`docs/architecture/recommendation-web-service.md`](docs/architecture/recommendation-web-service.md)
- DB 이식: [`docs/postgres-migration-plan.md`](docs/postgres-migration-plan.md) · [`docs/docker-postgres.md`](docs/docker-postgres.md)
- 데이터 사전: [`docs/data-schema.md`](docs/data-schema.md)
- 진행 기록: [`docs/PROGRESS.md`](docs/PROGRESS.md) · [`artifacts/improvement-log.md`](artifacts/improvement-log.md)
