"""Эмулятор потока телематики NDTP (JSON Lines по TCP).

Симулирует автобусы/троллейбусы Москвы с периодическими сбоями.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("ndtp-emulator")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "routes.json"
if not DATA.exists():
    DATA = ROOT.parent / "data" / "routes.json"


@dataclass
class VehicleSim:
    vehicle_id: str
    route_id: str
    stops: list[dict]
    idx: float = 0.0
    speed: float = 25.0
    delay: float = 0.0
    doors: bool = False
    failure_mode: str | None = None
    failure_until: float = 0.0
    clients_seen: int = 0


def load_routes() -> list[dict]:
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))["routes"]
    return [
        {
            "id": "M1",
            "name": "М1 Тверская",
            "expected_speed_kmh": 20,
            "stops": [
                {"id": "A1", "name": "Охотный ряд", "lat": 55.7575, "lon": 37.6165},
                {"id": "A2", "name": "Тверская", "lat": 55.7640, "lon": 37.6055},
                {"id": "A3", "name": "Маяковская", "lat": 55.7698, "lon": 37.5960},
            ],
        }
    ]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class Emulator:
    def __init__(self, n_vehicles: int = 12):
        self.routes = load_routes()
        self.vehicles: list[VehicleSim] = []
        self.clients: set[asyncio.StreamWriter] = set()
        rng = random.Random(42)
        for i in range(n_vehicles):
            route = self.routes[i % len(self.routes)]
            self.vehicles.append(
                VehicleSim(
                    vehicle_id=f"ТС-{1000 + i}",
                    route_id=route["id"],
                    stops=route["stops"],
                    idx=rng.random() * (len(route["stops"]) - 1.01),
                    speed=rng.uniform(18, 32),
                    delay=rng.uniform(-20, 40),
                )
            )

    def tick(self, now: float) -> list[dict]:
        frames = []
        for v in self.vehicles:
            # случайный сбой
            if v.failure_mode is None and random.random() < 0.008:
                v.failure_mode = random.choice(
                    ["speed_drop", "long_dwell", "congestion", "doors"]
                )
                v.failure_until = now + random.uniform(90, 240)

            if v.failure_mode and now > v.failure_until:
                v.failure_mode = None
                v.doors = False

            speed = v.speed
            if v.failure_mode == "speed_drop":
                speed = max(3.0, speed * 0.25)
                v.delay += 1.2
            elif v.failure_mode == "long_dwell":
                speed = 0.5
                v.doors = True
                v.delay += 1.5
            elif v.failure_mode == "congestion":
                speed = max(5.0, speed * 0.4)
                v.delay += 0.9
            elif v.failure_mode == "doors":
                v.doors = random.random() < 0.6
                speed = max(8.0, speed * 0.7)
                v.delay += 0.5
            else:
                v.doors = False
                v.delay = max(-30, v.delay - 0.15)

            n = len(v.stops)
            step = (speed / 3600.0) * 40  # грубо: доля сегмента / тик ~1с
            v.idx = (v.idx + step) % (n - 0.001)
            i0 = int(v.idx)
            i1 = min(i0 + 1, n - 1)
            t = v.idx - i0
            s0, s1 = v.stops[i0], v.stops[i1]
            # лёгкий шум GPS
            lat = lerp(s0["lat"], s1["lat"], t) + random.uniform(-0.00015, 0.00015)
            lon = lerp(s0["lon"], s1["lon"], t) + random.uniform(-0.00015, 0.00015)
            course = math.degrees(
                math.atan2(s1["lon"] - s0["lon"], s1["lat"] - s0["lat"])
            ) % 360

            frames.append(
                {
                    "vehicle_id": v.vehicle_id,
                    "route_id": v.route_id,
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed_kmh": round(speed + random.uniform(-1, 1), 1),
                    "course": round(course, 1),
                    "doors_open": v.doors,
                    "ignition": True,
                    "ts": now,
                    "stop_id": s0["id"],
                    "schedule_delay_sec": round(v.delay, 1),
                    "failure_mode": v.failure_mode,
                }
            )
        return frames

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.info("client connected %s", peer)
        self.clients.add(writer)
        try:
            while True:
                # держим соединение; данные шлём из broadcast
                try:
                    data = await asyncio.wait_for(reader.read(100), timeout=60)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    continue
        finally:
            self.clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("client disconnected %s", peer)

    async def broadcast_loop(self):
        while True:
            now = time.time()
            frames = self.tick(now)
            if self.clients:
                payload = "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in frames)
                dead = []
                for w in list(self.clients):
                    try:
                        w.write(payload.encode("utf-8"))
                        await w.drain()
                    except Exception:
                        dead.append(w)
                for w in dead:
                    self.clients.discard(w)
            # также пишем sample для historical fallback
            sample_dir = DATA.parent if DATA.exists() else (ROOT / "data")
            sample = sample_dir / "sample_telemetry.jsonl"
            try:
                with sample.open("w", encoding="utf-8") as f:
                    for fr in frames[:30]:
                        f.write(json.dumps(fr, ensure_ascii=False) + "\n")
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def run(self, host: str = "0.0.0.0", port: int = 9000):
        server = await asyncio.start_server(self.handle, host, port)
        logger.info("NDTP emulator on %s:%s, vehicles=%s", host, port, len(self.vehicles))
        asyncio.create_task(self.broadcast_loop())
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(Emulator().run())
