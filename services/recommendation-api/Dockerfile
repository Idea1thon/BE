# syntax=docker/dockerfile:1
FROM python:3.13-slim

# recommendation/serving_db.py 는 Python DB 드라이버가 아니라 psql 서브프로세스로
# COPY (SELECT ...) TO STDOUT 을 실행한다. postgresql-client 가 없으면 기동은
# 되지만 파이프라인이 실행 시점에 실패한다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
