#!/bin/bash
# Azure 와 같은 구조를 로컬에도 만든다 — 서버 1대, DB 2개.
#
# fmp      중간 백엔드 (alembic 이 스키마를 만든다)
# ideaton  추천·사이렌 (아래 02 스크립트가 DDL 을 적용한다)
#
# 두 서비스가 서로의 DB 를 조회할 수 없다는 경계를 로컬에서도 그대로 재현해야
# "로컬에서는 조인이 되던데" 같은 설계 사고를 막을 수 있다.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
	CREATE DATABASE fmp;
	CREATE DATABASE ideaton;
SQL

echo "  fmp / ideaton 생성 완료"
