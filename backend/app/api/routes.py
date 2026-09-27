"""HTTP API Backend."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import (
    User,
    authenticate,
    demo_accounts,
    ensure_driver_access,
    issue_token,
    require_dispatcher,
    require_user,
)
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


class LoginIn(BaseModel):
    login: str = Field(..., examples=["dispatcher"])
    password: str = Field(..., examples=["demo"])


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


@router.get("/auth/demo-accounts", tags=["auth"])
async def auth_demo_accounts() -> dict[str, Any]:
    """Демо-логины для жюри (пароль у всех: demo)."""
    return {"accounts": demo_accounts(), "password": "demo"}


@router.post("/auth/login", tags=["auth"])
async def auth_login(body: LoginIn, response: Response) -> dict[str, Any]:
    """Вход: диспетчер или водитель ТС."""
    user = authenticate(body.login, body.password)
    if not user:
        raise HTTPException(401, "Неверный логин или пароль")
    token = issue_token(user)
    response.set_cookie(
        key="mt_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    return {"ok": True, "token": token, "user": user.to_dict()}


@router.post("/auth/logout", tags=["auth"])
async def auth_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie("mt_token", path="/")
    return {"ok": True}


@router.get("/auth/me", tags=["auth"])
async def auth_me(user: User = Depends(require_user)) -> dict[str, Any]:
    return {"user": user.to_dict()}


@router.get("/snapshot", tags=["dashboard"])
async def snapshot(
    request: Request,
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """Полный снимок для дашборда (polling fallback)."""
    return request.app.state.state.snapshot()


@router.get("/vehicles", tags=["dashboard"])
async def vehicles(
    request: Request,
    user: User = Depends(require_user),
) -> list[dict[str, Any]]:
    """Список ТС: диспетчер — все; водитель — только своё."""
    vs = list(request.app.state.state.vehicles.values())
    if user.role == "driver":
        vs = [v for v in vs if v.vehicle_id == user.vehicle_id]
    return [v.to_dict() for v in vs]


@router.get("/incidents", tags=["dashboard"])
async def incidents(
    request: Request,
    _user: User = Depends(require_dispatcher),
) -> list[dict[str, Any]]:
    items = [i.to_dict() for i in request.app.state.state.incidents.values()]
    return sorted(items, key=lambda x: -x["delay_prob"])


@router.get("/routes", tags=["dashboard"])
async def routes(
    request: Request,
    _user: User = Depends(require_dispatcher),
) -> list[dict[str, Any]]:
    return list(request.app.state.state.routes.values())


@router.get("/metrics", tags=["meta"])
async def metrics(request: Request) -> dict[str, Any]:
    """Latency и пропускная способность (публично для health)."""
    return request.app.state.state.metrics.to_dict()


@router.post("/telemetry", tags=["ingest"])
async def post_telemetry(request: Request, body: TelemetryIn) -> dict[str, Any]:
    """Приём одного кадра NDTP (JSON). Без auth — поток жюри/эмулятор."""
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
    """Пакетная загрузка исторического датасета / валидации (JSON-массив)."""
    if len(body) > 20_000:
        raise HTTPException(400, "Слишком большой пакет (макс. 20000 кадров)")
    orch = request.app.state.orchestrator
    n = 0
    for item in body:
        frame = frame_from_dict(item)
        await orch.ingest_frame(frame)
        n += 1
    return {"accepted": n}


@router.post("/telemetry/upload", tags=["ingest"])
async def post_telemetry_upload(request: Request) -> dict[str, Any]:
    """Загрузка CSV / JSON / JSONL (тело raw). Для панели «Данные» и жюри."""
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "Пустое тело")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(400, "Пустое тело")
    items: list[dict[str, Any]] = []
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "csv" in ctype or (not text.startswith("{") and not text.startswith("[") and "," in text.split("\n", 1)[0]):
            reader = csv.DictReader(io.StringIO(text))
            items = [dict(row) for row in reader]
        elif text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("ожидался JSON-массив")
            items = parsed
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                items.append(json.loads(line))
    except Exception as exc:
        raise HTTPException(400, f"Не удалось разобрать файл: {exc}") from exc
    if not items:
        raise HTTPException(400, "Нет кадров в файле")
    if len(items) > 20_000:
        raise HTTPException(400, "Слишком большой пакет (макс. 20000 кадров)")
    orch = request.app.state.orchestrator
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        frame = frame_from_dict(item)
        await orch.ingest_frame(frame)
        n += 1
    return {"accepted": n, "format": "csv" if "csv" in ctype else "json"}


@router.post("/what-if", tags=["analytics"])
async def what_if(
    request: Request,
    body: WhatIfIn,
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """What-if: влияние выпуска дополнительного ТС."""
    return await request.app.state.orchestrator.what_if(body.route_id, body.extra_vehicles)


@router.get("/incidents/{vehicle_id}", tags=["dashboard"])
async def incident_card(
    request: Request,
    vehicle_id: str,
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    inc = request.app.state.state.incidents.get(vehicle_id)
    if not inc:
        raise HTTPException(404, "Инцидент не найден")
    return inc.to_dict()


class MessageIn(BaseModel):
    vehicle_id: str
    text: str = Field(..., min_length=1, max_length=500)


@router.post("/messages", tags=["dispatcher"])
async def send_message(
    request: Request,
    body: MessageIn,
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """Диспетчер → водитель: сообщение о задержке / рекомендации."""
    state = request.app.state.state
    msg = state.add_message(body.vehicle_id, body.text.strip())
    await state.broadcast()
    return {"ok": True, "message": msg.to_dict()}


@router.get("/messages/{vehicle_id}", tags=["dispatcher"])
async def list_messages(
    request: Request,
    vehicle_id: str,
    user: User = Depends(require_user),
) -> list[dict[str, Any]]:
    ensure_driver_access(user, vehicle_id)
    return [m.to_dict() for m in request.app.state.state.messages.get(vehicle_id, [])]


@router.get("/driver/{vehicle_id}", tags=["driver"])
async def driver_view(
    request: Request,
    vehicle_id: str,
    user: User = Depends(require_user),
) -> dict[str, Any]:
    """Упрощённый вид для водителя: задержка, скорость, inbox."""
    ensure_driver_access(user, vehicle_id)
    state = request.app.state.state
    v = state.vehicles.get(vehicle_id)
    if not v:
        raise HTTPException(404, "ТС не найдено (ещё нет в потоке)")
    return {
        "vehicle_id": vehicle_id,
        "route_id": v.route_id,
        "status": v.status,
        "current_delay_sec": v.current_delay_sec,
        "predicted_delay_sec": v.predicted_delay_sec,
        "suggested_speed_kmh": v.suggested_speed_kmh,
        "speed_kmh": v.speed_kmh,
        "risk_level": v.risk_level,
        "segment_name": v.segment_name,
        "messages": [m.to_dict() for m in state.messages.get(vehicle_id, [])[-20:]],
        "model": v.model,
    }


@router.get("/routes/{route_id}/detail", tags=["analytics"])
async def route_detail(
    request: Request,
    route_id: str,
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """Карточка маршрута: A/B, остановки, окна рейсов, live + история."""
    detail = request.app.state.orchestrator.stats.route_detail(route_id)
    if not detail:
        raise HTTPException(404, "Маршрут не найден")
    vehicles = [
        v.to_dict()
        for v in request.app.state.state.vehicles.values()
        if v.route_id == route_id
    ]
    detail["active_vehicles"] = vehicles
    detail["active_count"] = len(vehicles)
    return detail


@router.get("/stats/overview", tags=["analytics"])
async def stats_overview(
    request: Request,
    period: str = "day",
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """Сводка по всем маршрутам: day | week | month | live."""
    return request.app.state.orchestrator.stats.overview(period)


@router.get("/stats/report", tags=["analytics"])
async def stats_report(
    request: Request,
    period: str = "day",
    _user: User = Depends(require_dispatcher),
) -> dict[str, Any]:
    """Отчёт для BI: KPI, проблемные маршруты, данные для диаграмм."""
    return request.app.state.orchestrator.stats.report(period)
