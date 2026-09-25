FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app
COPY data /app/data
COPY dashboard /app/dashboard
COPY artifacts/public /app/artifacts

ENV MT_DASHBOARD_DIR=/app/dashboard
ENV MT_DATA_DIR=/app/data
ENV MT_ARTIFACTS_DIR=/app/artifacts
ENV MT_ML_URL=http://ml:8001
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
