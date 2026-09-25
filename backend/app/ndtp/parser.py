"""Парсер телематики NDTP (упрощённый JSON/бинарный кадр для хакатона).

Формат эмулятора: JSON Lines с полями, совместимыми с типичным NDTP-пейлоадом
(imei/uid, lat, lon, speed, course, doors, ignition, ts).
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class NDTPFrame:
    """Один кадр телематики ТС."""

    vehicle_id: str
    route_id: str
    lat: float
    lon: float
    speed_kmh: float
    course: float
    doors_open: bool
    ignition: bool
    ts: float  # unix seconds
    stop_id: str | None = None
    schedule_delay_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_json_line(line: str | bytes) -> NDTPFrame | None:
    """Парсит одну JSON-строку потока."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="ignore")
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return frame_from_dict(obj)


def frame_from_dict(obj: dict[str, Any]) -> NDTPFrame:
    """Маппинг словаря → NDTPFrame с нормализацией имён полей."""
    vid = str(
        obj.get("vehicle_id")
        or obj.get("uid")
        or obj.get("imei")
        or obj.get("id")
        or "unknown"
    )
    ts_raw = obj.get("ts") or obj.get("timestamp") or obj.get("time")
    if isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = datetime.now(timezone.utc).timestamp()
    elif ts_raw is None:
        ts = datetime.now(timezone.utc).timestamp()
    else:
        ts = float(ts_raw)
        if ts > 1e12:  # ms
            ts /= 1000.0

    doors = obj.get("doors_open", obj.get("doors", 0))
    if isinstance(doors, (int, float)):
        doors_open = bool(int(doors))
    else:
        doors_open = bool(doors)

    return NDTPFrame(
        vehicle_id=vid,
        route_id=str(obj.get("route_id") or obj.get("route") or "R-unknown"),
        lat=float(obj.get("lat") or obj.get("latitude") or 0.0),
        lon=float(obj.get("lon") or obj.get("lng") or obj.get("longitude") or 0.0),
        speed_kmh=float(obj.get("speed_kmh") or obj.get("speed") or 0.0),
        course=float(obj.get("course") or obj.get("bearing") or 0.0),
        doors_open=doors_open,
        ignition=bool(obj.get("ignition", True)),
        ts=ts,
        stop_id=(str(obj["stop_id"]) if obj.get("stop_id") is not None else None),
        schedule_delay_sec=(
            float(obj["schedule_delay_sec"])
            if obj.get("schedule_delay_sec") is not None
            else None
        ),
    )


def parse_binary_frame(buf: bytes) -> NDTPFrame | None:
    """Минимальный бинарный кадр: magic(2)=0xND + float fields.

    Layout (little-endian):
      magic u16=0x4E44 ('ND'), vehicle_id u32, route_id u16,
      lat f32, lon f32, speed f32, course f32, flags u8, ts f64
    """
    if len(buf) < 31:
        return None
    magic, vid, rid, lat, lon, speed, course, flags, ts = struct.unpack_from(
        "<HIHffffBd", buf, 0
    )
    if magic != 0x4E44:
        return None
    return NDTPFrame(
        vehicle_id=f"V-{vid}",
        route_id=f"R-{rid}",
        lat=lat,
        lon=lon,
        speed_kmh=speed,
        course=course,
        doors_open=bool(flags & 0x01),
        ignition=bool(flags & 0x02),
        ts=ts,
    )
