#!/bin/bash
# 운영보고서·추천·Siren 원천을 하나의 IDEATON DB에 둔다.
# middle-backend의 Alembic이 같은 DB에 애플리케이션 테이블을 만들고,
# 다음 초기화 스크립트가 추천 스키마를 적용한다.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
	CREATE DATABASE ideaton;
SQL

echo "  ideaton 생성 완료"
