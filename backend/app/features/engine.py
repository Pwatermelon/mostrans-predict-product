"""Расчёт производных признаков и map-matching."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

from app.ndtp.parser import NDTPFrame


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в метрах."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class RouteSegment:
    """Сегмент маршрутной сети."""

    segment_id: str
    route_id: str
    name: str
    lat: float
    lon: float
    next_lat: float
    next_lon: float
    expected_speed_kmh: float = 22.0


@dataclass
class VehicleFeatures:
    """Признаки для ML на горизонте 10–15 мин."""

    vehicle_id: str
    route_id: str
    lat: float
    lon: float
    speed_kmh: float
    current_delay_sec: float
    segment_avg_speed_kmh: float
    dwell_sec: float
    speed_drop_ratio: float
    doors_open_ratio: float
    progress_ratio: float
    matched_segment_id: str
    matched_segment_name: str
    dist_to_segment_m: float
    ts: float
    seq_speeds: list[float] = field(default_factory=list)
    seq_delays: list[float] = field(default_factory=list)
    doors_open: bool = False
    track: list[dict[str, float]] = field(default_factory=list)

    def tabular(self) -> dict[str, float | str]:
        return {
            "vehicle_id": self.vehicle_id,
            "route_id": self.route_id,
            "speed_kmh": self.speed_kmh,
            "current_delay_sec": self.current_delay_sec,
            "segment_avg_speed_kmh": self.segment_avg_speed_kmh,
            "dwell_sec": self.dwell_sec,
            "speed_drop_ratio": self.speed_drop_ratio,
            "doors_open_ratio": self.doors_open_ratio,
            "progress_ratio": self.progress_ratio,
            "dist_to_segment_m": self.dist_to_segment_m,
            "matched_segment_id": self.matched_segment_id,
        }

    def ds_features(self, horizon_sec: float = 780.0) -> dict[str, float]:
        """Вектор FEATURE_COLS для delay_catboost_ds (parity с сабмитом)."""
        from app.features.ds_schema import online_to_ds_features

        return online_to_ds_features(
            current_delay_sec=self.current_delay_sec,
            horizon_sec=horizon_sec,
            ts=self.ts,
            speeds=self.seq_speeds,
            dist_to_stop_m=self.dist_to_segment_m,
            progress_ratio=self.progress_ratio,
            hist_delays=self.seq_delays,
            stops_before=max(1.0, (1.0 - self.progress_ratio) * 8),
        )


class FeatureEngine:
    """Онлайн-расчёт признаков + простой map matching."""

    def __init__(self, segments: list[RouteSegment], history_len: int = 24):
        self.segments = segments
        self.by_route: dict[str, list[RouteSegment]] = defaultdict(list)
        for s in segments:
            self.by_route[s.route_id].append(s)
        self.history: dict[str, Deque[NDTPFrame]] = defaultdict(
            lambda: deque(maxlen=history_len)
        )
        self.track: dict[str, Deque[dict[str, float]]] = defaultdict(
            lambda: deque(maxlen=40)
        )
        self.dwell_start: dict[str, float | None] = defaultdict(lambda: None)
        self.schedule_offset: dict[str, float] = defaultdict(float)

    def match_segment(self, frame: NDTPFrame) -> tuple[RouteSegment | None, float]:
        """Ближайший сегмент маршрута (map matching по haversine)."""
        candidates = self.by_route.get(frame.route_id) or self.segments
        best: RouteSegment | None = None
        best_d = float("inf")
        for seg in candidates:
            d = haversine_m(frame.lat, frame.lon, seg.lat, seg.lon)
            if d < best_d:
                best_d = d
                best = seg
        return best, best_d

    def update(self, frame: NDTPFrame) -> VehicleFeatures:
        """Обновляет историю ТС и возвращает вектор признаков."""
        hist = self.history[frame.vehicle_id]
        hist.append(frame)
        self.track[frame.vehicle_id].append(
            {"lat": frame.lat, "lon": frame.lon, "ts": frame.ts, "speed": frame.speed_kmh}
        )

        # dwell: скорость < 3 км/ч или двери открыты
        if frame.speed_kmh < 3.0 or frame.doors_open:
            if self.dwell_start[frame.vehicle_id] is None:
                self.dwell_start[frame.vehicle_id] = frame.ts
        else:
            self.dwell_start[frame.vehicle_id] = None
        dwell = 0.0
        if self.dwell_start[frame.vehicle_id] is not None:
            dwell = max(0.0, frame.ts - self.dwell_start[frame.vehicle_id])

        # текущее отклонение от графика
        if frame.schedule_delay_sec is not None:
            self.schedule_offset[frame.vehicle_id] = frame.schedule_delay_sec
        current_delay = self.schedule_offset[frame.vehicle_id]

        speeds = [f.speed_kmh for f in hist]
        segment_avg = sum(speeds) / max(1, len(speeds))
        expected = 22.0
        seg, dist = self.match_segment(frame)
        if seg:
            expected = seg.expected_speed_kmh
        speed_drop = 1.0 - min(1.0, frame.speed_kmh / max(1.0, expected))

        doors_ratio = sum(1 for f in hist if f.doors_open) / max(1, len(hist))
        route_segs = self.by_route.get(frame.route_id) or self.segments
        progress = 0.0
        if route_segs and seg:
            try:
                idx = route_segs.index(seg)
                progress = idx / max(1, len(route_segs) - 1)
            except ValueError:
                progress = 0.0

        delays = []
        for f in hist:
            if f.schedule_delay_sec is not None:
                delays.append(f.schedule_delay_sec)
            else:
                delays.append(current_delay)

        return VehicleFeatures(
            vehicle_id=frame.vehicle_id,
            route_id=frame.route_id,
            lat=frame.lat,
            lon=frame.lon,
            speed_kmh=frame.speed_kmh,
            current_delay_sec=current_delay,
            segment_avg_speed_kmh=segment_avg,
            dwell_sec=dwell,
            speed_drop_ratio=max(0.0, speed_drop),
            doors_open_ratio=doors_ratio,
            progress_ratio=progress,
            matched_segment_id=seg.segment_id if seg else "unknown",
            matched_segment_name=seg.name if seg else "неизвестно",
            dist_to_segment_m=dist if math.isfinite(dist) else 9999.0,
            ts=frame.ts,
            seq_speeds=speeds[-16:],
            seq_delays=delays[-16:],
            doors_open=frame.doors_open,
            track=list(self.track[frame.vehicle_id]),
        )
