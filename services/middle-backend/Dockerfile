# syntax=docker/dockerfile:1
FROM python:3.13-slim

# uv 를 버전 고정 없이 설치한다. uv.lock 이 revision 3 이라 옛 uv 로는 읽지
# 못하는데, 어느 버전부터 지원하는지 확실치 않은 상태에서 태그를 박으면 빌드가
# 조용히 깨진다. 팀이 uv 버전을 정하면 그때 고정한다.
RUN pip install --no-cache-dir uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 로컬 개발과 같은 잠금 파일로 설치한다. --frozen 은 lock 을 갱신하지 않고
# 어긋나면 실패시킨다 — "내 로컬에서는 되는데" 를 빌드 단계에서 잡는다.
COPY . .
RUN uv sync --frozen

EXPOSE 8000
CMD ["/app/docker-entrypoint.sh"]
