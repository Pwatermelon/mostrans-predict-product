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
from app.auth import parse_token
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


@app.get("/login", tags=["meta"])
@app.get("/login/", tags=["meta"])
async def login_page():
    """Страница входа диспетчер / водитель."""
    path = DASHBOARD_DIR / "login.html"
    if path.exists():
        return FileResponse(path)
    return {"error": "login.html not found"}


@app.get("/driver", tags=["meta"])
@app.get("/driver/", tags=["meta"])
async def driver_page():
    """Мобильный вид водителя."""
    path = DASHBOARD_DIR / "driver.html"
    if path.exists():
        return FileResponse(path)
    return {"error": "driver.html not found"}


@app.get("/stats", tags=["meta"])
@app.get("/stats/", tags=["meta"])
async def stats_page():
    """Дашборд исторической статистики маршрутов."""
    path = DASHBOARD_DIR / "stats.html"
    if path.exists():
        return FileResponse(path)
    return {"error": "stats.html not found"}


@app.get("/admin", tags=["meta"])
@app.get("/admin/", tags=["meta"])
async def admin_page():
    """Панель загрузки потока / исторического датасета."""
    path = DASHBOARD_DIR / "admin.html"
    if path.exists():
        return FileResponse(path)
    return {"error": "admin.html not found"}


@app.get("/healthz", tags=["meta"])
async def healthz():
    """Healthcheck для Docker/Caddy."""
    return {"status": "ok", "ts": time.time()}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """Поток состояния дашборда — только диспетчер."""
    token = websocket.query_params.get("token") or websocket.cookies.get("mt_token")
    user = parse_token(token) if token else None
    if not user or user.role != "dispatcher":
        await websocket.close(code=4401)
        return
    await websocket.accept()
    state.ws_clients.add(websocket)
    try:
        await websocket.send_json(state.snapshot())
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(websocket)
