"""Оркестратор: поток NDTP → признаки → ML → дашборд."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from app.config import settings
from app.features.engine import FeatureEngine, RouteSegment, VehicleFeatures
from app.ndtp.parser import NDTPFrame, parse_json_line
from app.services.state import AppState, Incident, VehicleState

logger = logging.getLogger("mostrans.orchestrator")

CAUSE_MAP = {
    "speed_drop": "Аномальное снижение скорости на подходе к перекрёстку",
    "long_dwell": "Длительная посадка / простой на остановке",
    "congestion": "Затор на сегменте маршрута",
    "schedule_drift": "Накопившееся отклонение от нитки графика",
    "doors": "Частые открытия дверей вне остановок",
    "normal": "Без выраженного паттерна сбоя",
}


def risk_level(prob: float, delay_sec: float) -> str:
    if prob >= 0.65 or delay_sec >= 180:
        return "red"
    if prob >= 0.40 or delay_sec >= 90:
        return "yellow"
    return "green"


def infer_pattern(feat: VehicleFeatures) -> tuple[str, str]:
    if feat.dwell_sec >= 45:
        return "long_dwell", CAUSE_MAP["long_dwell"]
    if feat.speed_drop_ratio >= 0.55 and feat.speed_kmh < 12:
        return "speed_drop", CAUSE_MAP["speed_drop"]
    if feat.segment_avg_speed_kmh < 10 and feat.speed_kmh < 15:
        return "congestion", CAUSE_MAP["congestion"]
    if feat.doors_open_ratio > 0.4 and feat.speed_kmh > 5:
        return "doors", CAUSE_MAP["doors"]
    if feat.current_delay_sec >= 60:
        return "schedule_drift", CAUSE_MAP["schedule_drift"]
    return "normal", CAUSE_MAP["normal"]


def recommendation(pattern: str, risk: str) -> str:
    if risk == "green":
        return "Контроль по расписанию, действий не требуется"
    tips = {
        "long_dwell": "Ускорить посадку / связаться с водителем; рассмотреть пропуск малозагруженной остановки",
        "speed_drop": "Проверить светофорный объект; при необходимости — приоритет ТС",
        "congestion": "Оценить объезд; выпустить резервное ТС на хвост маршрута",
        "doors": "Проверить корректность датчика дверей и режим посадки",
        "schedule_drift": "Скорректировать интервал; what-if: доп. ТС",
        "normal": "Наблюдение; перепроверить через 2–3 мин",
    }
    return tips.get(pattern, tips["normal"])


class Orchestrator:
    """Связка поток → прогноз → алерты."""

    def __init__(self, state: AppState):
        self.state = state
        self._stop = False
        self._reader_task: asyncio.Task | None = None
        self.segments = self._load_segments()
        self.engine = FeatureEngine(self.segments)
        self._last_features: dict[str, VehicleFeatures] = {}
        self._frame_queue: asyncio.Queue[NDTPFrame] = asyncio.Queue(maxsize=5000)

        for seg in self.segments:
            r = self.state.routes.setdefault(
                seg.route_id,
                {
                    "route_id": seg.route_id,
                    "name": f"Маршрут {seg.route_id}",
                    "risk_level": "green",
                    "max_prob": 0.0,
                    "points": [],
                },
            )
            r["points"].append(
                {
                    "segment_id": seg.segment_id,
                    "name": seg.name,
                    "lat": seg.lat,
                    "lon": seg.lon,
                }
            )

    def _load_segments(self) -> list[RouteSegment]:
        path = Path(settings.data_dir) / "routes.json"
        if not path.exists():
            # fallback — центр Москвы demo
            return [
                RouteSegment("S1", "M1", "Тверская", 55.757, 37.615, 55.760, 37.620, 20),
                RouteSegment("S2", "M1", "Пушкинская", 55.765, 37.605, 55.770, 37.600, 18),
                RouteSegment("S3", "M2", "Арбат", 55.752, 37.591, 55.750, 37.585, 22),
            ]
        data = json.loads(path.read_text(encoding="utf-8"))
        out: list[RouteSegment] = []
        for r in data["routes"]:
            pts = r["stops"]
            for i, p in enumerate(pts):
                nxt = pts[(i + 1) % len(pts)]
                out.append(
                    RouteSegment(
                        segment_id=p["id"],
                        route_id=r["id"],
                        name=p["name"],
                        lat=p["lat"],
                        lon=p["lon"],
                        next_lat=nxt["lat"],
                        next_lon=nxt["lon"],
                        expected_speed_kmh=float(r.get("expected_speed_kmh", 22)),
                    )
                )
            self.state.routes[r["id"]] = {
                "route_id": r["id"],
                "name": r.get("name", r["id"]),
                "risk_level": "green",
                "max_prob": 0.0,
                "points": [
                    {"segment_id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"]}
                    for p in pts
                ],
            }
        return out

    def stop(self) -> None:
        self._stop = True

    async def ingest_frame(self, frame: NDTPFrame) -> None:
        try:
            self._frame_queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                _ = self._frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await self._frame_queue.put(frame)

    async def run(self) -> None:
        self._reader_task = asyncio.create_task(self._tcp_reader())
        asyncio.create_task(self._historical_fallback_loop())
        while not self._stop:
            try:
                frame = await asyncio.wait_for(self._frame_queue.get(), timeout=1.0)
                await self._process_frame(frame)
            except asyncio.TimeoutError:
                self._check_degrade()
                await self.state.broadcast()
            except Exception:
                logger.exception("process error")
                await asyncio.sleep(0.2)

    def _check_degrade(self) -> None:
        now = time.time()
        last = self.state.metrics.last_frame_ts
        if last and now - last > settings.degrade_after_sec:
            self.state.metrics.degraded = True
            self.state.metrics.mode = "degraded"
        elif last:
            self.state.metrics.degraded = False
            self.state.metrics.mode = "live"

    async def _historical_fallback_loop(self) -> None:
        """При обрыве связи — крутим последний известный снимок без падения."""
        path = Path(settings.data_dir) / "sample_telemetry.jsonl"
        while not self._stop:
            await asyncio.sleep(2.0)
            if not self.state.metrics.degraded:
                continue
            if not path.exists():
                continue
            self.state.metrics.mode = "historical"
            # лёгкий replay последних строк
            try:
                lines = path.read_text(encoding="utf-8").strip().splitlines()[-40:]
                for line in lines:
                    fr = parse_json_line(line)
                    if fr:
                        fr.ts = time.time()
                        await self.ingest_frame(fr)
                    await asyncio.sleep(0.05)
            except Exception:
                logger.exception("historical replay failed")

    async def _tcp_reader(self) -> None:
        """Подключение к эмулятору NDTP (TCP JSONL) с реконнектом."""
        host, port = settings.emulator_host, settings.emulator_port
        backoff = 0.5
        while not self._stop:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                logger.info("Connected to NDTP emulator %s:%s", host, port)
                backoff = 0.5
                self.state.metrics.degraded = False
                self.state.metrics.mode = "live"
                while not self._stop:
                    line = await reader.readline()
                    if not line:
                        break
                    frame = parse_json_line(line)
                    if frame:
                        await self.ingest_frame(frame)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            except Exception as e:
                logger.warning("NDTP connection lost: %s", e)
                self.state.metrics.degraded = True
                self.state.metrics.mode = "degraded"
                await asyncio.sleep(backoff)
                backoff = min(8.0, backoff * 1.5)

    async def _process_frame(self, frame: NDTPFrame) -> None:
        self.state.metrics.frames_total += 1
        self.state.metrics.last_frame_ts = time.time()
        feat = self.engine.update(frame)
        self._last_features[frame.vehicle_id] = feat

        t0 = time.perf_counter()
        pred = await self._call_ml(feat)
        latency_ms = (time.perf_counter() - t0) * 1000
        self.state.record_latency(latency_ms)

        pattern, cause = infer_pattern(feat)
        # если ML вернул pattern — используем
        if pred.get("pattern") and pred["pattern"] != "normal":
            pattern = pred["pattern"]
            cause = CAUSE_MAP.get(pattern, pred.get("cause", cause))

        prob = float(pred.get("delay_prob", 0.0))
        delay = float(pred.get("predicted_delay_sec", max(0.0, feat.current_delay_sec)))
        abs_err = float(pred.get("abs_error_sec", abs(delay - feat.current_delay_sec)))
        risk = risk_level(prob, delay)
        horizon = int(pred.get("horizon_sec", settings.predict_horizon_sec))
        # алерт только в окне 10–15 мин (не «задним числом»)
        alert_lead = float(horizon)

        self.state.upsert_vehicle(
            VehicleState(
                vehicle_id=feat.vehicle_id,
                route_id=feat.route_id,
                lat=feat.lat,
                lon=feat.lon,
                speed_kmh=feat.speed_kmh,
                current_delay_sec=feat.current_delay_sec,
                risk_level=risk,
                delay_prob=prob,
                segment_name=feat.matched_segment_name,
                ts=feat.ts,
            )
        )

        inc = Incident(
            vehicle_id=feat.vehicle_id,
            route_id=feat.route_id,
            delay_prob=prob,
            predicted_delay_sec=delay,
            abs_error_sec=abs_err,
            cause=cause,
            pattern=pattern,
            segment_id=feat.matched_segment_id,
            segment_name=feat.matched_segment_name,
            lat=feat.lat,
            lon=feat.lon,
            risk_level=risk,
            horizon_sec=horizon,
            recommendation=recommendation(pattern, risk),
            ts=time.time(),
            alert_lead_sec=alert_lead,
        )
        self.state.upsert_incident(inc)
        self._update_route_risk(feat.route_id)
        await self.state.broadcast()

    def _update_route_risk(self, route_id: str) -> None:
        route = self.state.routes.get(route_id)
        if not route:
            return
        probs = [
            v.delay_prob
            for v in self.state.vehicles.values()
            if v.route_id == route_id
        ]
        max_p = max(probs) if probs else 0.0
        route["max_prob"] = max_p
        route["risk_level"] = risk_level(max_p, 0)

    async def _call_ml(self, feat: VehicleFeatures) -> dict:
        payload = {
            "features": feat.tabular(),
            "seq_speeds": feat.seq_speeds,
            "seq_delays": feat.seq_delays,
            "horizon_sec": settings.predict_horizon_sec,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_sec) as client:
                r = await client.post(f"{settings.ml_url}/predict", json=payload)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning("ML unavailable, heuristic fallback: %s", e)
            return self._heuristic(feat)

    def _heuristic(self, feat: VehicleFeatures) -> dict:
        """Деградация без ML: эвристика по признакам."""
        score = 0.0
        score += min(0.45, feat.current_delay_sec / 400)
        score += min(0.25, feat.speed_drop_ratio * 0.4)
        score += min(0.2, feat.dwell_sec / 120)
        score += min(0.15, feat.doors_open_ratio * 0.3)
        delay = feat.current_delay_sec + score * 200
        pattern, cause = infer_pattern(feat)
        return {
            "delay_prob": min(0.95, score),
            "predicted_delay_sec": delay,
            "abs_error_sec": 45.0,
            "pattern": pattern,
            "cause": cause,
            "horizon_sec": settings.predict_horizon_sec,
            "model": "heuristic-fallback",
        }

    async def what_if(self, route_id: str, extra_vehicles: int = 1) -> dict:
        """What-if: оценка влияния доп. ТС на график маршрута."""
        route_vehicles = [v for v in self.state.vehicles.values() if v.route_id == route_id]
        if not route_vehicles:
            return {
                "route_id": route_id,
                "extra_vehicles": extra_vehicles,
                "message": "Нет активных ТС на маршруте",
                "before_avg_delay_sec": 0,
                "after_avg_delay_sec": 0,
                "risk_reduction": 0,
            }
        before = sum(v.current_delay_sec for v in route_vehicles) / len(route_vehicles)
        # простая модель: каждый доп. ТС снижает среднюю задержку и риск ~12–18%
        factor = max(0.35, 1.0 - 0.15 * extra_vehicles)
        after = before * factor
        probs = [v.delay_prob for v in route_vehicles]
        before_p = sum(probs) / len(probs)
        after_p = before_p * factor
        return {
            "route_id": route_id,
            "extra_vehicles": extra_vehicles,
            "before_avg_delay_sec": round(before, 1),
            "after_avg_delay_sec": round(after, 1),
            "before_avg_risk": round(before_p, 3),
            "after_avg_risk": round(after_p, 3),
            "risk_reduction": round(before_p - after_p, 3),
            "recommendation": (
                f"Выпуск +{extra_vehicles} ТС на {route_id}: "
                f"ожидаемое снижение ср. опоздания {before - after:.0f} с, "
                f"риска на {(before_p - after_p) * 100:.0f} п.п."
            ),
        }
