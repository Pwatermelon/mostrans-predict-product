"""Статика маршрутов + накопленная статистика для диспетчера."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_routes_catalog(data_dir: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(data_dir) / "routes.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for r in raw.get("routes", []):
        stops = r.get("stops", [])
        out[r["id"]] = {
            "route_id": r["id"],
            "name": r.get("name", r["id"]),
            "expected_speed_kmh": r.get("expected_speed_kmh", 20),
            "stops": stops,
            "point_a": stops[0] if stops else None,
            "point_b": stops[-1] if stops else None,
            "stops_count": len(stops),
            # демо-окна рейсов (нет полного GTFS — фиксируем рабочие окна)
            "first_trip": "05:30",
            "last_trip": "01:15",
        }
    return out


class RouteStatsStore:
    """Онлайн-накопление + демо-история день/неделя/месяц."""

    def __init__(self, catalog: dict[str, dict[str, Any]]):
        self.catalog = catalog
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._alerts: dict[str, int] = defaultdict(int)
        self._frames: dict[str, int] = defaultdict(int)
        self.started = time.time()
        # seed «истории» для питча (реалистичные порядки величин)
        self._history_seed = {
            rid: {
                "day": {
                    "avg_delay_sec": 45 + (i * 17) % 40,
                    "ontime_pct": 78 - (i * 5) % 15,
                    "alerts": 12 + (i * 3) % 10,
                    "trips": 86 + (i * 7) % 20,
                },
                "week": {
                    "avg_delay_sec": 52 + (i * 11) % 35,
                    "ontime_pct": 74 - (i * 4) % 12,
                    "alerts": 70 + (i * 9) % 40,
                    "trips": 520 + (i * 30) % 80,
                },
                "month": {
                    "avg_delay_sec": 58 + (i * 9) % 30,
                    "ontime_pct": 71 - (i * 3) % 10,
                    "alerts": 280 + (i * 25) % 100,
                    "trips": 2100 + (i * 120) % 400,
                },
            }
            for i, rid in enumerate(catalog)
        }

    def observe(self, route_id: str, delay_sec: float, risk_level: str) -> None:
        buf = self._samples[route_id]
        buf.append(float(delay_sec))
        if len(buf) > 500:
            del buf[: len(buf) - 500]
        self._frames[route_id] += 1
        if risk_level in ("yellow", "red"):
            self._alerts[route_id] += 1

    def live_stats(self, route_id: str) -> dict[str, Any]:
        samples = self._samples.get(route_id, [])
        avg = sum(samples) / len(samples) if samples else 0.0
        ontime = (
            sum(1 for s in samples if s <= 120) / len(samples) * 100 if samples else 100.0
        )
        return {
            "avg_delay_sec": round(avg, 1),
            "ontime_pct": round(ontime, 1),
            "alerts": self._alerts.get(route_id, 0),
            "samples": len(samples),
            "frames": self._frames.get(route_id, 0),
        }

    def route_detail(self, route_id: str) -> dict[str, Any] | None:
        cat = self.catalog.get(route_id)
        if not cat:
            return None
        live = self.live_stats(route_id)
        hist = self._history_seed.get(route_id, {})
        return {
            **cat,
            "live": live,
            "history": hist,
        }

    def overview(self, period: str = "day") -> dict[str, Any]:
        period = period if period in ("day", "week", "month", "live") else "day"
        rows = []
        for rid, cat in self.catalog.items():
            if period == "live":
                st = self.live_stats(rid)
            else:
                st = self._history_seed.get(rid, {}).get(period, self.live_stats(rid))
            rows.append(
                {
                    "route_id": rid,
                    "name": cat["name"],
                    "point_a": (cat["point_a"] or {}).get("name"),
                    "point_b": (cat["point_b"] or {}).get("name"),
                    "stops_count": cat["stops_count"],
                    "first_trip": cat["first_trip"],
                    "last_trip": cat["last_trip"],
                    **st,
                }
            )
        rows.sort(key=lambda x: -float(x.get("avg_delay_sec") or 0))
        return {
            "period": period,
            "generated_at": time.time(),
            "routes": rows,
            "totals": {
                "routes": len(rows),
                "avg_delay_sec": round(
                    sum(r.get("avg_delay_sec", 0) for r in rows) / max(1, len(rows)), 1
                ),
                "alerts": sum(int(r.get("alerts", 0)) for r in rows),
            },
        }
