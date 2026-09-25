"""
ML-ядро MosTrans Predict.

Ансамбль: CatBoost (табличные признаки) + PyTorch LSTM (последовательности телеметрии).
Горизонт прогноза: 600–900 секунд (10–15 минут).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from models.ensemble import EnsemblePredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mostrans.ml")

predictor: EnsemblePredictor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global predictor
    predictor = EnsemblePredictor.load_or_bootstrap()
    logger.info("ML ensemble ready: %s", predictor.describe())
    yield


app = FastAPI(
    title="MosTrans ML Core",
    description="Инференс ансамбля CatBoost + PyTorch для прогноза задержки 10–15 мин",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


class PredictIn(BaseModel):
    features: dict[str, Any]
    seq_speeds: list[float] = Field(default_factory=list)
    seq_delays: list[float] = Field(default_factory=list)
    horizon_sec: int = 780


class PredictOut(BaseModel):
    delay_prob: float
    predicted_delay_sec: float
    abs_error_sec: float
    pattern: str
    cause: str
    horizon_sec: int
    model: str
    latency_ms: float
    components: dict[str, float]


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "ready": predictor is not None}


@app.get("/info")
async def info():
    assert predictor is not None
    return predictor.describe()


@app.post("/predict", response_model=PredictOut)
async def predict(body: PredictIn) -> PredictOut:
    assert predictor is not None
    t0 = time.perf_counter()
    result = predictor.predict(
        features=body.features,
        seq_speeds=body.seq_speeds,
        seq_delays=body.seq_delays,
        horizon_sec=body.horizon_sec,
    )
    latency = (time.perf_counter() - t0) * 1000
    result["latency_ms"] = round(latency, 2)
    return PredictOut(**result)


@app.post("/predict/batch")
async def predict_batch(items: list[PredictIn]) -> list[dict[str, Any]]:
    assert predictor is not None
    out = []
    for body in items:
        t0 = time.perf_counter()
        r = predictor.predict(
            features=body.features,
            seq_speeds=body.seq_speeds,
            seq_delays=body.seq_delays,
            horizon_sec=body.horizon_sec,
        )
        r["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        out.append(r)
    return out
