# 로컬 실행

```bash
docker compose up --build
```

| | 주소 |
|---|---|
| 중간 백엔드 | http://localhost:8000/docs |
| 통합 recommendation-api (추천 + Siren) | http://localhost:8001/docs |
| PostgreSQL | localhost:55432 (`ideaton`) |

첫 기동에서 일어나는 일:

1. `db` — `ideaton` DB 생성, 추천 스키마 DDL 적용 (PostGIS·pgcrypto 포함)
2. `middle-backend` — `alembic upgrade head` 후 시드, 그다음 서버
3. `recommendation-api` — 입지 추천 + 폐업 위험 사이렌 서버

## 알아둘 것

**DB 초기화는 볼륨이 비어 있을 때 한 번만 돈다.** `services/recommendation-api/db/*.sql` 을 고쳐도 이미 만든 DB 에는 반영되지 않는다. 다시 적용하려면:

```bash
docker compose down -v && docker compose up --build
```

**로컬 compose 로는 실제 추천을 실행할 수 없다.** 초기화 스크립트는 DDL 만 적용하므로 `location.area` 를 비롯한 테이블이 전부 0건이고, `RECOMMENDATION_SOURCE` 가 `db` 로 고정돼 있어 `DbSource.layers()` 가 DB 만 본다. **`RECOMMENDATION_DATA_ROOT` 에 원천 CSV 를 마운트해도 파일로 fallback 하지 않는다.** 서버는 뜨지만 추천 요청은 빈 결과나 오류가 된다.

로컬에서 추천을 돌려보려면 둘 중 하나가 필요하다.

- **적재된 DB 를 가리킨다** — `docker-compose.prod.yml` 처럼 `IDEATON_DATABASE_URL` 을 Azure `ideaton` 으로 두고 추천 API 만 띄운다. 지금은 이 방법만 실제로 동작한다.
- **로컬 DB 에 데이터를 적재한다** — 적재 절차는 추천 서비스 쪽 담당이며 아직 문서화돼 있지 않다. `services/recommendation-api/scripts/` 의 적재 스크립트가 필요로 하는 원천 데이터가 저장소에 없다.

DDL 만으로 충분한 작업(스키마 확인, 마이그레이션, 중간 백엔드 개발)에는 로컬 compose 로 충분하다.

**추천 API 컨테이너에는 `postgresql-client` 가 들어 있다.** `recommendation/serving_db.py` 가 Python 드라이버가 아니라 `psql` 서브프로세스로 `COPY (SELECT ...) TO STDOUT` 을 실행하기 때문이다. 이걸 빼면 기동은 되지만 파이프라인이 실행 시점에 죽는다.

**서비스 간 호출은 컨테이너 이름으로 한다.** 중간 백엔드에서 추천과 Siren은 통합 `recommendation-api`의 `http://recommendation-api:8000`을 사용한다. `localhost:8001`은 호스트에서만 통한다.

**`INTERNAL_API_TOKEN` 은 양쪽이 같아야 한다.** 추천 API 는 이 값이 비어 있으면 fail closed 로 503 을 반환한다 — 설정 누락을 조용히 통과시키지 않으려는 의도된 동작이다.

## 로컬 DB 만 쓰고 싶을 때

앱은 각자 로컬에서 띄우고 DB 만 컨테이너로:

```bash
docker compose up db
```

`postgresql://postgres:devpass@localhost:55432/ideaton` 로 붙는다. 포트는 loopback 에만 게시되므로 같은 기기에서만 접근된다 — 이 구성은 저장소에 공개된 기본 비밀번호를 쓰기 때문이다.

## Azure 와의 관계

구조를 맞춰 뒀다 — 서버 1대, 통합 DB `ideaton`, PostGIS. 로컬은 `postgres` superuser 하나를 쓰고 Azure는 서비스별 계정으로 나눌 수 있다. 운영보고서·Siren 원천·추천 상권 데이터가 같은 DB에 있으므로 중간 백엔드는 ID-only trigger만 Siren에 전달하고, Siren이 읽기 전용으로 원천을 조회한다.
