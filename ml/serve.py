"""
ML-ядро MosTrans Predict.

Основная модель: delay_catboost_ds.cbm (та же, что submission.csv).
Доп.: PyTorch LSTM для вероятностного сигнала / fallback.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from feature_schema import FEATURE_COLS, MODEL_ID
from models.ensemble import EnsemblePredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mostrans.ml")

predictor: EnsemblePredictor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global predictor
    predictor = EnsemblePredictor.load_or_bootstrap()
    logger.info("ML ready: %s", predictor.describe())
    yield


app = FastAPI(
    title="MosTrans ML Core",
    description=(
        f"Инференс {MODEL_ID}: тот же CatBoost, что формирует submission.csv. "
        "Признаки FEATURE_COLS, горизонт 10–15 мин."
    ),
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


class PredictIn(BaseModel):
    features: dict[str, Any] = Field(
        ...,
        description="Вектор признаков FEATURE_COLS для parity с сабмитом",
    )
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
    return {
        "status": "ok",
        "ready": predictor is not None,
        "model": predictor.primary if predictor else None,
    }


@app.get("/info")
async def info():
    assert predictor is not None
    return predictor.describe()


@app.get("/feature_cols")
async def feature_cols():
    return {"model": MODEL_ID, "cols": FEATURE_COLS}


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
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
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
