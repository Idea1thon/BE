#!/bin/sh
# 컨테이너 기동 절차. 마이그레이션 → 시드 → 서버.
#
# 마이그레이션을 여기서 돌리는 이유: compose 로 처음 올리는 사람이 스키마를 따로
# 만들 필요가 없어야 한다. alembic 은 이미 적용된 리비전을 건너뛰므로 재기동해도
# 안전하다. 시드도 존재 여부를 보고 넘어가므로 멱등이다.
set -e

echo "▸ 마이그레이션"
uv run alembic upgrade head

if [ "${ENVIRONMENT}" = "development" ]; then
  echo "▸ 시드"
  uv run python -m scripts.seed
fi

echo "▸ 서버"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
