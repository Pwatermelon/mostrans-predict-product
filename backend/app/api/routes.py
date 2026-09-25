"""HTTP API Backend."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from app.ndtp.parser import frame_from_dict

router = APIRouter()


class TelemetryIn(BaseModel):
    """Ручная подача кадра телематики (для жюри / тестов)."""

    vehicle_id: str
    route_id: str
    lat: float
    lon: float
    speed_kmh: float = 0
    course: float = 0
    doors_open: bool = False
    ignition: bool = True
    ts: float | None = None
    stop_id: str | None = None
    schedule_delay_sec: float | None = None


class WhatIfIn(BaseModel):
    route_id: str = Field(..., examples=["M1"])
    extra_vehicles: int = Field(1, ge=1, le=5)


@router.get("/health", tags=["meta"])
async def health(request: Request) -> dict[str, Any]:
    """Статус backend + режим деградации."""
    state = request.app.state.state
    return {
        "status": "ok" if not state.metrics.degraded else "degraded",
        "metrics": state.metrics.to_dict(),
        "vehicles": len(state.vehicles),
        "incidents": len(state.incidents),
        "ts": time.time(),
    }


@router.get("/snapshot", tags=["dashboard"])
async def snapshot(request: Request) -> dict[str, Any]:
    """Полный снимок для дашборда (polling fallback)."""
    return request.app.state.state.snapshot()


@router.get("/vehicles", tags=["dashboard"])
async def vehicles(request: Request) -> list[dict[str, Any]]:
    return [v.to_dict() for v in request.app.state.state.vehicles.values()]


@router.get("/incidents", tags=["dashboard"])
async def incidents(request: Request) -> list[dict[str, Any]]:
    items = [i.to_dict() for i in request.app.state.state.incidents.values()]
    return sorted(items, key=lambda x: -x["delay_prob"])


@router.get("/routes", tags=["dashboard"])
async def routes(request: Request) -> list[dict[str, Any]]:
    return list(request.app.state.state.routes.values())


@router.get("/metrics", tags=["meta"])
async def metrics(request: Request) -> dict[str, Any]:
    """Latency и пропускная способность."""
    return request.app.state.state.metrics.to_dict()


@router.post("/telemetry", tags=["ingest"])
async def post_telemetry(request: Request, body: TelemetryIn) -> dict[str, Any]:
    """Приём одного кадра NDTP (JSON)."""
    data = body.model_dump()
    if data.get("ts") is None:
        data["ts"] = time.time()
    frame = frame_from_dict(data)
    await request.app.state.orchestrator.ingest_frame(frame)
    return {"accepted": True, "vehicle_id": frame.vehicle_id}


@router.post("/telemetry/batch", tags=["ingest"])
async def post_telemetry_batch(
    request: Request,
    body: list[dict[str, Any]] = Body(...),
) -> dict[str, Any]:
    """Пакетная загрузка исторического датасета / валидации."""
    orch = request.app.state.orchestrator
    n = 0
    for item in body:
        frame = frame_from_dict(item)
        await orch.ingest_frame(frame)
        n += 1
    return {"accepted": n}


@router.post("/what-if", tags=["analytics"])
async def what_if(request: Request, body: WhatIfIn) -> dict[str, Any]:
    """What-if: влияние выпуска дополнительного ТС."""
    return await request.app.state.orchestrator.what_if(body.route_id, body.extra_vehicles)


@router.get("/incidents/{vehicle_id}", tags=["dashboard"])
async def incident_card(request: Request, vehicle_id: str) -> dict[str, Any]:
    inc = request.app.state.state.incidents.get(vehicle_id)
    if not inc:
        raise HTTPException(404, "Инцидент не найден")
    return inc.to_dict()
