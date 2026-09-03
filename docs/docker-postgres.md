# Docker 팀 공유용 PostgreSQL

작성일: 2026-09-03
작성 주체: GPT(Codex)

## 목적

Postgres.app의 로컬 DB를 Docker Desktop의 PostgreSQL + PostGIS + pgvector 컨테이너로 복원해 팀원이 같은 DB에 접근할 수 있게 한다.

현재 Postgres.app 서버는 `127.0.0.1:5432`에서 계속 실행할 수 있으므로 Docker 호스트 포트는 `55432`로 분리한다. 컨테이너 내부 PostgreSQL 포트는 기본값인 `5432`다.

Docker 컨테이너는 인터넷에 직접 공개하지 않는다. `POSTGRES_BIND_ADDRESS=0.0.0.0`은 같은 신뢰된 LAN 또는 VPN에서만 사용하며, 공용 서버에 배포할 때는 방화벽·VPN·TLS 또는 SSH 터널을 추가한다.

## 최초 구성

```bash
cp .env.docker.example .env.docker
```

`.env.docker`에서 `POSTGRES_PASSWORD`를 강한 임의 문자열로 변경한다. `.env.docker`는 `.gitignore`에 포함되어 Git에 올라가지 않는다.

Docker Desktop이 실행된 상태에서 이미지와 컨테이너를 준비한다.

```bash
docker compose --env-file .env.docker build db
docker compose --env-file .env.docker up -d db
docker compose --env-file .env.docker ps
```

상태가 `healthy`가 될 때까지 확인한다.

```bash
docker compose --env-file .env.docker logs -f db
```

## Postgres.app 데이터 복원

새 Docker volume이 비어 있는 최초 실행을 기준으로 한다. 현재 Postgres.app의 원본 DB는 변경하지 않는다.
현재 원본은 PostgreSQL 18.4이고 Docker 대상은 PostgreSQL 16이다. 따라서 PostgreSQL 18에서 만든 custom archive를 PostgreSQL 16의 `pg_restore`로 직접 읽을 수 없다. 아래처럼 일반 SQL로 덤프하고 PostgreSQL 16에 없는 `transaction_timeout` 설정만 제외한다.

```bash
pg_dump -h 127.0.0.1 -p 5432 -U parkjunwoo -d ideaton \
  --format=plain --no-owner --no-privileges \
| sed '/^SET transaction_timeout = 0;$/d' \
| docker compose --env-file .env.docker exec -T db \
    sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

원본에는 `vector` 확장이 활성화되어 있지 않을 수 있으므로, Docker DB에서 RAG 임베딩을 사용할 경우 다음을 한 번 실행한다.

```bash
docker compose --env-file .env.docker exec -T db \
  psql -v ON_ERROR_STOP=1 -U ideaton -d ideaton \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

복원 후 접속 확인:

```bash
docker compose --env-file .env.docker exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version();"'
```

복원 대상 volume에 이미 데이터가 있다면 먼저 별도 백업과 복원 계획을 확정한다. `down -v`, `dropdb`, `pg_restore --clean`은 기존 Docker 데이터를 삭제할 수 있으므로 기본 절차에 포함하지 않는다.

## 팀원 접속

호스트 컴퓨터의 LAN/VPN IP를 확인한다. macOS에서는 예를 들어 다음처럼 확인할 수 있다.

```bash
ipconfig getifaddr en0
```

팀원은 다음 정보를 사용한다.

```text
Host: <Docker 호스트의 LAN/VPN IP>
Port: 55432
Database: ideaton
User: ideaton
Password: .env.docker에 설정한 값
SSL mode: prefer (공용망에서는 사용하지 말고 TLS/VPN 구성)
```

접속 문자열 형식:

```text
postgresql://ideaton:<password>@<host-ip>:55432/ideaton
```

Docker 호스트의 방화벽에서 `55432/tcp`를 신뢰된 팀원 네트워크에만 허용해야 한다. 공용 인터넷에 포트를 그대로 열지 않는다.

## 운영 규칙

- `ideaton_pgdata` volume이 실제 DB 데이터다. 컨테이너를 재생성해도 volume을 유지한다.
- 정기 백업은 컨테이너 내부 `pg_dump`로 수행한다.
- 추천 파이프라인은 DB 조회 결과와 기존 Parquet/DuckDB 결과를 대조 QA한 뒤 전환한다.
- Docker DB는 팀 공유 저장소이며, 비밀번호·API 키·덤프 파일은 Git에 커밋하지 않는다.
- Postgres.app과 Docker DB는 서로 다른 DB다. 같은 `ideaton` 이름을 사용해도 포트가 다르다.

## 현재 상태

이 문서와 `compose.yaml`, `docker/postgres/Dockerfile`은 GPT(Codex)가 구성했다. 2026-09-03 기준 Docker 이미지 빌드, 컨테이너 기동(`healthy`), Postgres.app→Docker 데이터 복원, `vector` 확장 활성화 및 주요 행 수 대조를 완료했다. Docker 컨테이너는 `0.0.0.0:55432->5432`로 게시되어 있으며, 팀원 접속은 신뢰된 LAN/VPN에서만 허용한다.
