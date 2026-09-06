#!/bin/bash
# 추천 서비스 DDL 을 ideaton 에 적용한다.
#
# 스키마 정본은 services/recommendation-api/db/*.sql 이며 compose 가 /schema 로
# 마운트한다. 파일을 복사하지 않는 이유는 사본이 갈라지기 때문이다.
#
# 000 은 CREATE EXTENSION postgis/pgcrypto 로 시작한다. postgis 이미지를 쓰므로
# 확장이 이미 설치되어 있고, 여기서는 superuser 라 생성도 통과한다.
set -euo pipefail

shopt -s nullglob
files=(/schema/*.sql)
if [ ${#files[@]} -eq 0 ]; then
  echo "  /schema 에 DDL 이 없다 — 건너뛴다"
  exit 0
fi

for f in "${files[@]}"; do
  echo "  적용: $(basename "$f")"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname ideaton -f "$f"
done

psql -tA --username "$POSTGRES_USER" --dbname ideaton \
  -c "SELECT 'PostGIS ' || extversion FROM pg_extension WHERE extname = 'postgis'"
