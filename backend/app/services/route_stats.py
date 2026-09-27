"""Статика маршрутов + накопленная статистика / отчёты для диспетчера."""

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
            "first_trip": "05:30",
            "last_trip": "01:15",
        }
    return out


def _severity(avg_delay_sec: float, ontime_pct: float, alerts: int) -> str:
    if avg_delay_sec >= 90 or ontime_pct < 70 or alerts >= 20:
        return "critical"
    if avg_delay_sec >= 60 or ontime_pct < 80 or alerts >= 10:
        return "warning"
    return "ok"


def _hourly_series(seed: int, base_delay: float) -> list[dict[str, Any]]:
    """24 точки: час → средний delay (демо-паттерн пик 8–10 и 17–19)."""
    out = []
    for h in range(24):
        rush = 1.0
        if 7 <= h <= 9:
            rush = 1.55 + (h - 7) * 0.1
        elif 17 <= h <= 19:
            rush = 1.45 + (19 - h) * 0.08
        elif h < 5 or h > 22:
            rush = 0.55
        noise = ((seed * 17 + h * 13) % 11) - 5
        delay = max(10.0, base_delay * rush + noise)
        alerts = int(max(0, (delay - 40) / 12 + ((seed + h) % 3)))
        out.append(
            {
                "hour": h,
                "label": f"{h:02d}:00",
                "avg_delay_sec": round(delay, 1),
                "alerts": alerts,
                "ontime_pct": round(max(45.0, 95 - delay / 2.2), 1),
            }
        )
    return out


class RouteStatsStore:
    """Онлайн-накопление + демо-история день/неделя/месяц + отчёты."""

    def __init__(self, catalog: dict[str, dict[str, Any]]):
        self.catalog = catalog
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._alerts: dict[str, int] = defaultdict(int)
        self._frames: dict[str, int] = defaultdict(int)
        self._risk_counts: dict[str, int] = defaultdict(int)
        self.started = time.time()
        self._history_seed: dict[str, dict[str, Any]] = {}
        for i, rid in enumerate(catalog):
            day_delay = 45 + (i * 17) % 40
            self._history_seed[rid] = {
                "day": {
                    "avg_delay_sec": day_delay,
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
                "hourly": _hourly_series(i + 1, day_delay),
                "problem_segments": [
                    {
                        "name": (
                            catalog[rid]["stops"][j]["name"]
                            if j < len(catalog[rid]["stops"])
                            else f"Сегмент {j + 1}"
                        ),
                        "avg_delay_sec": day_delay + 20 + j * 15,
                        "share_pct": 28 - j * 6,
                    }
                    for j in range(min(3, len(catalog[rid]["stops"]) or 3))
                ],
            }

    def observe(self, route_id: str, delay_sec: float, risk_level: str) -> None:
        buf = self._samples[route_id]
        buf.append(float(delay_sec))
        if len(buf) > 500:
            del buf[: len(buf) - 500]
        self._frames[route_id] += 1
        self._risk_counts[risk_level or "green"] += 1
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
            "trips": max(1, len(samples) // 8) if samples else 0,
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
            "history": {k: v for k, v in hist.items() if k in ("day", "week", "month")},
            "hourly": hist.get("hourly", []),
            "problem_segments": hist.get("problem_segments", []),
            "severity": _severity(
                live["avg_delay_sec"] or hist.get("day", {}).get("avg_delay_sec", 0),
                live["ontime_pct"] if live["samples"] else hist.get("day", {}).get("ontime_pct", 100),
                live["alerts"] or hist.get("day", {}).get("alerts", 0),
            ),
        }

    def overview(self, period: str = "day") -> dict[str, Any]:
        period = period if period in ("day", "week", "month", "live") else "day"
        rows = []
        for rid, cat in self.catalog.items():
            if period == "live":
                st = self.live_stats(rid)
            else:
                st = dict(self._history_seed.get(rid, {}).get(period, self.live_stats(rid)))
            sev = _severity(
                float(st.get("avg_delay_sec") or 0),
                float(st.get("ontime_pct") or 100),
                int(st.get("alerts") or 0),
            )
            rows.append(
                {
                    "route_id": rid,
                    "name": cat["name"],
                    "point_a": (cat["point_a"] or {}).get("name"),
                    "point_b": (cat["point_b"] or {}).get("name"),
                    "stops_count": cat["stops_count"],
                    "first_trip": cat["first_trip"],
                    "last_trip": cat["last_trip"],
                    "severity": sev,
                    **st,
                }
            )
        rows.sort(key=lambda x: -float(x.get("avg_delay_sec") or 0))
        critical = [r for r in rows if r["severity"] == "critical"]
        warning = [r for r in rows if r["severity"] == "warning"]
        return {
            "period": period,
            "generated_at": time.time(),
            "routes": rows,
            "totals": {
                "routes": len(rows),
                "avg_delay_sec": round(
                    sum(r.get("avg_delay_sec", 0) for r in rows) / max(1, len(rows)), 1
                ),
                "avg_ontime_pct": round(
                    sum(float(r.get("ontime_pct") or 0) for r in rows) / max(1, len(rows)), 1
                ),
                "alerts": sum(int(r.get("alerts", 0)) for r in rows),
                "trips": sum(int(r.get("trips") or r.get("samples") or 0) for r in rows),
                "critical_routes": len(critical),
                "warning_routes": len(warning),
            },
        }

    def report(self, period: str = "day") -> dict[str, Any]:
        """Полный отчёт для дашборда с диаграммами."""
        ov = self.overview(period)
        rows = ov["routes"]

        # агрегированный почасовой профиль по всем маршрутам
        hourly_acc: dict[int, list[float]] = defaultdict(list)
        hourly_alerts: dict[int, int] = defaultdict(int)
        for i, rid in enumerate(self.catalog):
            for pt in self._history_seed.get(rid, {}).get("hourly", []):
                hourly_acc[pt["hour"]].append(pt["avg_delay_sec"])
                hourly_alerts[pt["hour"]] += int(pt["alerts"])
        # для live чуть сдвигаем к текущим данным
        if period == "live":
            for rid in self.catalog:
                live = self.live_stats(rid)
                if live["samples"]:
                    for h in range(24):
                        hourly_acc[h].append(live["avg_delay_sec"] * (0.85 + (h % 5) * 0.04))

        timeline = [
            {
                "hour": h,
                "label": f"{h:02d}:00",
                "avg_delay_sec": round(sum(hourly_acc[h]) / max(1, len(hourly_acc[h])), 1)
                if hourly_acc[h]
                else 0,
                "alerts": hourly_alerts[h],
            }
            for h in range(24)
        ]

        risk_live = {
            "green": self._risk_counts.get("green", 0),
            "yellow": self._risk_counts.get("yellow", 0),
            "red": self._risk_counts.get("red", 0),
        }
        if sum(risk_live.values()) == 0:
            # демо-распределение из severity маршрутов
            risk_live = {
                "green": sum(1 for r in rows if r["severity"] == "ok") * 8,
                "yellow": sum(1 for r in rows if r["severity"] == "warning") * 5
                + sum(1 for r in rows if r["severity"] == "ok") * 2,
                "red": sum(1 for r in rows if r["severity"] == "critical") * 4
                + sum(1 for r in rows if r["severity"] == "warning"),
            }

        problems = []
        for r in rows:
            if r["severity"] == "ok":
                continue
            segs = self._history_seed.get(r["route_id"], {}).get("problem_segments", [])
            top = segs[0] if segs else None
            problems.append(
                {
                    "route_id": r["route_id"],
                    "name": r["name"],
                    "severity": r["severity"],
                    "avg_delay_sec": r.get("avg_delay_sec"),
                    "ontime_pct": r.get("ontime_pct"),
                    "alerts": r.get("alerts"),
                    "hotspot": (top or {}).get("name"),
                    "hotspot_delay_sec": (top or {}).get("avg_delay_sec"),
                    "recommendation": (
                        "Выпустить резервное ТС / усилить интервал"
                        if r["severity"] == "critical"
                        else "Контроль посадки, сообщение водителю"
                    ),
                }
            )

        # сравнение периодов для выбранных маршрутов
        compare = []
        for rid, cat in self.catalog.items():
            h = self._history_seed.get(rid, {})
            compare.append(
                {
                    "route_id": rid,
                    "name": cat["name"],
                    "day": h.get("day", {}).get("avg_delay_sec", 0),
                    "week": h.get("week", {}).get("avg_delay_sec", 0),
                    "month": h.get("month", {}).get("avg_delay_sec", 0),
                    "live": self.live_stats(rid)["avg_delay_sec"],
                }
            )

        peak = max(timeline, key=lambda x: x["avg_delay_sec"]) if timeline else None

        return {
            **ov,
            "charts": {
                "delay_by_route": [
                    {
                        "route_id": r["route_id"],
                        "label": r["name"].split("«")[0].strip() or r["route_id"],
                        "full_name": r["name"],
                        "value": r.get("avg_delay_sec", 0),
                        "severity": r["severity"],
                    }
                    for r in rows
                ],
                "alerts_by_route": [
                    {
                        "route_id": r["route_id"],
                        "label": r["name"].split("«")[0].strip() or r["route_id"],
                        "value": int(r.get("alerts") or 0),
                        "severity": r["severity"],
                    }
                    for r in sorted(rows, key=lambda x: -int(x.get("alerts") or 0))
                ],
                "ontime_by_route": [
                    {
                        "route_id": r["route_id"],
                        "label": r["name"].split("«")[0].strip() or r["route_id"],
                        "value": float(r.get("ontime_pct") or 0),
                        "severity": r["severity"],
                    }
                    for r in rows
                ],
                "timeline": timeline,
                "risk_distribution": risk_live,
                "period_compare": compare,
            },
            "problems": problems,
            "insights": {
                "worst_route": rows[0]["name"] if rows else None,
                "worst_delay_sec": rows[0].get("avg_delay_sec") if rows else 0,
                "peak_hour": peak["label"] if peak else None,
                "peak_delay_sec": peak["avg_delay_sec"] if peak else 0,
                "critical_count": ov["totals"]["critical_routes"],
                "warning_count": ov["totals"]["warning_routes"],
            },
        }
