# pgvector 이미지를 기반으로 PostGIS를 추가한다.
# 현재 프로젝트 DDL은 PostGIS를 필수로 사용하고, 향후 RAG vector 검색을 위해
# pgvector도 함께 유지한다.
FROM pgvector/pgvector:pg16

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       postgresql-16-postgis-3 \
       postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*
COPY initdb/ /docker-entrypoint-initdb.d/

USER postgres
