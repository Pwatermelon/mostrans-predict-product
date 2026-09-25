FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY ml/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt pandas pyarrow

COPY ml/models /app/models
COPY ml/serve.py /app/serve.py
COPY ml/train.py /app/train.py
COPY ml/train_ds.py /app/train_ds.py
COPY ml/predict_validate.py /app/predict_validate.py
COPY ml/features_ds.py /app/features_ds.py
COPY ml/artifacts /app/artifacts
# датасет хакатона + готовый сабмит/модель — всё внутри образа
COPY data/hackathon /app/data/hackathon
COPY data/cache /app/data/cache
COPY artifacts/public /app/artifacts_public
RUN cp -f /app/artifacts_public/* /app/artifacts/ 2>/dev/null || true \
    && cp -f /app/artifacts_public/delay_catboost_ds.cbm /app/artifacts/ 2>/dev/null || true

ENV PYTHONUNBUFFERED=1
ENV MT_DATA_ROOT=/app/data/hackathon
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8001/healthz || exit 1

# bootstrap realtime models if missing, then serve
CMD ["sh", "-c", "if [ ! -f artifacts/delay_catboost.cbm ] || [ ! -f artifacts/delay_lstm.pt ]; then python train.py --out artifacts; fi; uvicorn serve:app --host 0.0.0.0 --port 8001"]
