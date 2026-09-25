"""
MosTrans Predict — Backend API.

Оркестрация потока NDTP, расчёт признаков, вызов ML-ядра, WebSocket-дашборд.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.services.orchestrator import Orchestrator
from app.services.state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mostrans.backend")

state = AppState()
orchestrator = Orchestrator(state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Старт фоновых задач и graceful shutdown."""
    app.state.state = state
    app.state.orchestrator = orchestrator
    task = asyncio.create_task(orchestrator.run())
    logger.info("Backend started, horizon=%ss", settings.predict_horizon_sec)
    yield
    orchestrator.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Backend stopped")


app = FastAPI(
    title="MosTrans Predict API",
    description=(
        "Backend хакатона Московского транспорта: приём NDTP, "
        "признаки, прогноз задержки 10–15 мин, алерты диспетчеру."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")

DASHBOARD_DIR = Path(settings.dashboard_dir)
ARTIFACTS_DIR = Path(settings.artifacts_dir)
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")
if ARTIFACTS_DIR.exists():
    app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


@app.get("/", tags=["meta"])
async def root():
    """Лендинг дашборда."""
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "service": "mostrans-predict-backend",
        "docs": "/docs",
        "health": "/api/v1/health",
        "submission": "/artifacts/submission.csv",
    }


@app.get("/healthz", tags=["meta"])
async def healthz():
    """Healthcheck для Docker/Caddy."""
    return {"status": "ok", "ts": time.time()}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """Поток состояния дашборда в реальном времени."""
    await websocket.accept()
    state.ws_clients.add(websocket)
    try:
        await websocket.send_json(state.snapshot())
        while True:
            # keepalive / client pings
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(websocket)
