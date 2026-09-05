# 아키텍처 문서

연구 단계 자산(`scripts/`, `output/`, `artifacts/`)을 **입지추천 웹서비스**로 만들 때의 시스템 설계.

| 문서 | 형식 | 용도 |
| --- | --- | --- |
| [`../입지추천_기능요구사항_하네스대응.md`](../입지추천_기능요구사항_하네스대응.md) | Markdown | **팀 공유용 — 기능 요구서(v0.2) 입지 추천 파트 ↔ 데이터 하네스 대응.** REQ ID별 상태(지원/조정필요/갭), 확정 설계 결정 + 근거, 알려진 한계, 팀 결정 필요 항목. 개발·기획·데이터 착수 전 먼저 읽기. |
| [`recommendation-web-service.md`](recommendation-web-service.md) | Markdown (정본) | 상세 설계 — 컴포넌트 인터페이스 시그니처, 횡단 관심사, 배치 하드 규칙, 미해결 질문 전체. 구현 시 참조. |
| [`recommendation-web-service-blueprint.html`](recommendation-web-service-blueprint.html) | HTML 시각 리포트 | 팀 공유용 설계도 — 다이어그램 중심 요약. 발표·온보딩용. [게시된 Artifact](https://claude.ai/code/artifact/2d02f9a3-3b78-4b8d-a003-29de11f5c4ee) |
| [`../data-schema.md`](../data-schema.md) | Markdown | 팀 공유용 데이터 사전 — 데이터 단위, 키, 기간, 출처, 사용 범위와 한계 |
| [`../postgres-migration-plan.md`](../postgres-migration-plan.md) | Markdown + SQL | PostgreSQL/PostGIS 이식 순서와 DB 테이블 구조 |

두 문서는 같은 내용을 다른 깊이로 담는다. 블루프린트(HTML)는 md의 요약이며, 상충하면 md가 정본이다.

## 범위

- **정한다**: 컴포넌트 분해(서빙 C1–C9 / 배치 C10–C18), 요청·배치 데이터 흐름, 저장소 선택, 배포 토폴로지, 설계 결정 D-1~D-10, 단계적 로드맵 P0~P3.
- **위임한다**: 후보 선정 알고리즘 → `artifacts/20-method/candidate-selection-spec.md`, RAG 프롬프트 → `artifacts/20-method/` + `.claude/context/rag-contract.md`, 입력 파싱 규칙 → `artifacts/10-analysis/input-and-condition-contract.md`, 지표 산식 → `artifacts/10-analysis/regional-characteristics-profile.md`.

## 확정 / 미해결 (2026-09-02)

- **확정**: 후보·폴리곤 규모 = 저장소 실측값(Q-1) · 상시 가동 서버 1대 배포(Q-2a) · 호스티드 LLM(Q-3a) · `entry_health_v1` 산식·컷 승인(Q-4a).
- **좁혀진 잔여**(서비스 안 막음): 대상 서버·예산(Q-2b) · LLM 공급자·약관(Q-3b) · `fit_index_v1` 가중치(Q-4b).
- **최우선 작업**: R-1 — 후보 파이프라인(`scripts/sample_jamsil_coffee.py`)을 C5 모듈로 분해 (P0).

## 서빙 파이프라인 ↔ 저장소 (2026-09-03)

- `scripts/recommendation_pipeline.py` 는 기본값 `--source db` 로 **PostgreSQL(compose.yaml 의 Docker `ideaton-db`, PostGIS·pgvector)** 에서 영역 폴리곤·분기 팩트(점포·추정매출·유동인구·상권변화지표)·전 업종 지역 배경(`location.area_store_totals`)을 읽는다. `--source files` 로 원천 CSV/shp 만으로도 실행 가능(A/B 회귀·폴백).
- seed·반경 지표·뉴스·임대료·네이버·POI 컨텍스트·생성 격자는 **두 모드 모두 원천 파일**에서 읽는다(경량 스냅샷·파생·보조 맥락).
- D-3 각주: arch 문서는 서빙 저장소로 Parquet+DuckDB(대안 B=PostGIS)를 제시했으나, 팀 실구현은 **PostgreSQL+PostGIS**(대안 B)를 채택했다. C9 조회 인터페이스는 `scripts/serving_db.py` + `recommendation_pipeline.DbSource` 로 추상화되어 있다.
- 이식·스키마: `db/001_location_schema.sql`, `scripts/migrate_postgres*.py`. 접속은 `.env` 의 `DATABASE_URL`(이식·서빙 공용).
