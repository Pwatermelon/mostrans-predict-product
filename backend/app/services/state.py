"""Глобальное состояние приложения и рассылка WebSocket."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class Incident:
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
    risk_level: str
    horizon_sec: int
    recommendation: str
    ts: float
    alert_lead_sec: float
    suggested_speed_kmh: float = 20.0
    status: str = "on_route"
    model: str = "delay_catboost_ds"

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
    status: str = "on_route"
    suggested_speed_kmh: float = 20.0
    predicted_delay_sec: float = 0.0
    track: list[dict[str, float]] = field(default_factory=list)
    model: str = "delay_catboost_ds"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DriverMessage:
    id: str
    vehicle_id: str
    text: str
    ts: float
    from_dispatcher: bool = True

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
    mode: str = "live"
    ml_model: str = "delay_catboost_ds"
    uptime_start: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uptime_sec"] = time.time() - self.uptime_start
        return d


class AppState:
    def __init__(self) -> None:
        self.vehicles: dict[str, VehicleState] = {}
        self.incidents: dict[str, Incident] = {}
        self.routes: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[DriverMessage]] = {}
        self.metrics = Metrics()
        self.ws_clients: set[WebSocket] = set()
        self._lat_sum = 0.0
        self._lat_n = 0
        self._msg_seq = 0

    def upsert_vehicle(self, v: VehicleState) -> None:
        self.vehicles[v.vehicle_id] = v

    def upsert_incident(self, inc: Incident) -> None:
        if inc.risk_level == "green" and inc.delay_prob < 0.35:
            self.incidents.pop(inc.vehicle_id, None)
        else:
            self.incidents[inc.vehicle_id] = inc

    def add_message(self, vehicle_id: str, text: str) -> DriverMessage:
        self._msg_seq += 1
        msg = DriverMessage(
            id=f"m{self._msg_seq}",
            vehicle_id=vehicle_id,
            text=text,
            ts=time.time(),
        )
        self.messages.setdefault(vehicle_id, []).append(msg)
        # keep last 50
        self.messages[vehicle_id] = self.messages[vehicle_id][-50:]
        return msg

    def record_latency(self, ms: float, model: str | None = None) -> None:
        self.metrics.last_predict_latency_ms = ms
        self._lat_sum += ms
        self._lat_n += 1
        self.metrics.avg_predict_latency_ms = self._lat_sum / self._lat_n
        self.metrics.predicts_total += 1
        if model:
            self.metrics.ml_model = model

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
            "messages": {
                vid: [m.to_dict() for m in msgs[-10:]]
                for vid, msgs in self.messages.items()
            },
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
