# 로컬 실행

```bash
docker compose up --build
```

| | 주소 |
|---|---|
| 중간 백엔드 | http://localhost:8000/docs |
| 추천 API | http://localhost:8001/docs |
| PostgreSQL | localhost:55432 (`fmp`, `ideaton`) |

첫 기동에서 일어나는 일:

1. `db` — `fmp`·`ideaton` 두 DB 생성, `ideaton` 에 추천 스키마 DDL 적용 (PostGIS·pgcrypto 포함)
2. `middle-backend` — `alembic upgrade head` 후 시드, 그다음 서버
3. `recommendation-api` — 서버만

## 알아둘 것

**DB 초기화는 볼륨이 비어 있을 때 한 번만 돈다.** `services/recommendation-api/db/*.sql` 을 고쳐도 이미 만든 DB 에는 반영되지 않는다. 다시 적용하려면:

```bash
docker compose down -v && docker compose up --build
```

**추천 파이프라인 실행에는 원천 데이터가 필요하다.** 저장소에 없다. 서버는 데이터 없이도 뜨지만 실제 추천 요청은 실패한다. 데이터를 가진 사람은 `.env` 에 `RECOMMENDATION_DATA_ROOT` 를 채우고 볼륨을 연결한다.

**추천 API 컨테이너에는 `postgresql-client` 가 들어 있다.** `recommendation/serving_db.py` 가 Python 드라이버가 아니라 `psql` 서브프로세스로 `COPY (SELECT ...) TO STDOUT` 을 실행하기 때문이다. 이걸 빼면 기동은 되지만 파이프라인이 실행 시점에 죽는다.

**서비스 간 호출은 컨테이너 이름으로 한다.** 중간 백엔드에서 추천 API 는 `http://recommendation-api:8000` 이다. `localhost:8001` 은 호스트에서만 통한다.

**`INTERNAL_API_TOKEN` 은 양쪽이 같아야 한다.** 추천 API 는 이 값이 비어 있으면 fail closed 로 503 을 반환한다 — 설정 누락을 조용히 통과시키지 않으려는 의도된 동작이다.

## 로컬 DB 만 쓰고 싶을 때

앱은 각자 로컬에서 띄우고 DB 만 컨테이너로:

```bash
docker compose up db
```

`postgresql://postgres:devpass@localhost:55432/fmp` 로 붙는다.

## Azure 와의 관계

구조를 맞춰 뒀다 — 서버 1대, DB 2개(`fmp` / `ideaton`), PostGIS. 다른 점은 로컬이 `postgres` superuser 하나를 쓰고 Azure 는 서비스별 계정(`fmp_app` / `pipeline_app`)으로 나눈다는 것이다. 계정 분리까지 재현하지 않은 이유는 로컬에서 권한 문제로 막히는 시간이 얻는 것보다 크기 때문이다. **다만 DB 경계는 로컬에서도 그대로다** — 서로의 DB 를 조인할 수 없다.
