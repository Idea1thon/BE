# syntax=docker/dockerfile:1
FROM python:3.13-slim

# 순수 계산 서비스라 psql·빌드 도구가 필요 없다. 이미지를 얇게 둔다.
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# api.py 가 `from .models import ...` 형태의 상대 임포트를 쓰므로 패키지로 담는다.
COPY . ./services/siren/
RUN touch services/__init__.py

EXPOSE 8000
CMD ["uvicorn", "services.siren.api:app", "--host", "0.0.0.0", "--port", "8000"]
