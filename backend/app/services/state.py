"""Глобальное состояние приложения и рассылка WebSocket."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from fastapi import WebSocket


@dataclass
class Incident:
    """Карточка инцидента для диспетчера."""

    vehicle_id: str
    route_id: str
    delay_prob: float
    predicted_delay_sec: float
    abs_error_sec: float
    cause: str
    pattern: str
    segment_id: str
    segment_name: str
    lat: float
    lon: float
    risk_level: str  # green | yellow | red
    horizon_sec: int
    recommendation: str
    ts: float
    alert_lead_sec: float  # сколько секунд до события (10–15 мин окно)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VehicleState:
    vehicle_id: str
    route_id: str
    lat: float
    lon: float
    speed_kmh: float
    current_delay_sec: float
    risk_level: str
    delay_prob: float
    segment_name: str
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Metrics:
    frames_total: int = 0
    predicts_total: int = 0
    last_frame_ts: float = 0.0
    last_predict_latency_ms: float = 0.0
    avg_predict_latency_ms: float = 0.0
    degraded: bool = False
    mode: str = "live"  # live | degraded | historical
    uptime_start: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uptime_sec"] = time.time() - self.uptime_start
        return d


class AppState:
    """In-memory state для дашборда и API."""

    def __init__(self) -> None:
        self.vehicles: dict[str, VehicleState] = {}
        self.incidents: dict[str, Incident] = {}
        self.routes: dict[str, dict[str, Any]] = {}
        self.metrics = Metrics()
        self.ws_clients: set[WebSocket] = set()
        self._lat_sum = 0.0
        self._lat_n = 0

    def upsert_vehicle(self, v: VehicleState) -> None:
        self.vehicles[v.vehicle_id] = v

    def upsert_incident(self, inc: Incident) -> None:
        if inc.risk_level == "green" and inc.delay_prob < 0.35:
            self.incidents.pop(inc.vehicle_id, None)
        else:
            self.incidents[inc.vehicle_id] = inc

    def record_latency(self, ms: float) -> None:
        self.metrics.last_predict_latency_ms = ms
        self._lat_sum += ms
        self._lat_n += 1
        self.metrics.avg_predict_latency_ms = self._lat_sum / self._lat_n
        self.metrics.predicts_total += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "ts": time.time(),
            "vehicles": [v.to_dict() for v in self.vehicles.values()],
            "incidents": sorted(
                (i.to_dict() for i in self.incidents.values()),
                key=lambda x: -x["delay_prob"],
            ),
            "routes": list(self.routes.values()),
            "metrics": self.metrics.to_dict(),
        }

    async def broadcast(self) -> None:
        if not self.ws_clients:
            return
        payload = self.snapshot()
        dead: list[WebSocket] = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)
