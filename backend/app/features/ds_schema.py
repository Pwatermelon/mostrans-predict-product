"""Маппинг онлайн-телеметрии → вектор FEATURE_COLS (parity с DS)."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

# Копия схемы — backend не импортирует ml-пакет в Docker
FEATURE_COLS: list[str] = [
    "cur_dev_s",
    "horizon_sec",
    "hour",
    "minute",
    "dow",
    "speed_mean",
    "speed_std",
    "speed_last",
    "speed_min",
    "speed_max",
    "n_points",
    "n_valid",
    "valid_ratio",
    "dist_to_stop_m",
    "bearing_err_deg",
    "moved_m",
    "avg_speed_calc",
    "idle_ratio",
    "speed_trend",
    "hist_delay_mean",
    "hist_delay_last",
    "hist_delay_std",
    "stops_before",
    "progress_ratio",
    "cur_dev_abs",
    "cur_dev_pos",
    "eta_gap_sec",
]


def vehicle_status(speed_kmh: float, dwell_sec: float, doors_open: bool = False) -> str:
    """on_route | at_stop | break."""
    if dwell_sec >= 600 and speed_kmh < 2:
        return "break"
    if speed_kmh < 3 or doors_open or dwell_sec >= 40:
        return "at_stop"
    return "on_route"


def suggested_speed_kmh(
    dist_to_stop_m: float,
    horizon_sec: float,
    current_delay_sec: float,
) -> float:
    """Рекомендуемая ср. скорость, чтобы сократить отставание к целевой остановке."""
    if dist_to_stop_m <= 0 or horizon_sec <= 30:
        return 20.0
    # хотим «съесть» часть текущей задержки за горизонт
    target_travel = max(120.0, horizon_sec - max(0.0, current_delay_sec) * 0.5)
    v = (dist_to_stop_m / target_travel) * 3.6
    return float(max(12.0, min(45.0, v)))


def online_to_ds_features(
    *,
    current_delay_sec: float,
    horizon_sec: float,
    ts: float,
    speeds: list[float],
    dist_to_stop_m: float,
    bearing_err_deg: float = 0.0,
    progress_ratio: float = 0.0,
    hist_delays: list[float] | None = None,
    n_valid: int | None = None,
    stops_before: float = 1.0,
) -> dict[str, float]:
    """Строит dict FEATURE_COLS из онлайн-состояния ТС."""
    hist_delays = hist_delays or [current_delay_sec]
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    speeds = speeds or [0.0]
    n_points = float(len(speeds))
    if n_valid is None:
        n_valid = len(speeds)
    speed_mean = sum(speeds) / n_points
    speed_std = 0.0
    if len(speeds) > 1:
        mean = speed_mean
        speed_std = math.sqrt(sum((s - mean) ** 2 for s in speeds) / len(speeds))
    idle_ratio = sum(1 for s in speeds if s < 3.0) / n_points
    speed_trend = 0.0
    if len(speeds) >= 4:
        half = len(speeds) // 2
        speed_trend = sum(speeds[half:]) / (len(speeds) - half) - sum(speeds[:half]) / half

    moved_m = 0.0
    avg_speed_calc = speed_mean
    # грубая оценка пути за окно ≈ mean_speed * duration
    if len(speeds) >= 2:
        moved_m = speed_mean / 3.6 * max(1.0, (len(speeds) - 1) * 12.0)

    cur = float(current_delay_sec)
    h = float(horizon_sec)
    dist = float(dist_to_stop_m) if dist_to_stop_m >= 0 else -1.0

    feats = {
        "cur_dev_s": cur,
        "horizon_sec": h,
        "hour": float(dt.hour) + dt.minute / 60.0,
        "minute": float(dt.minute),
        "dow": float(dt.weekday()),
        "speed_mean": float(speed_mean),
        "speed_std": float(speed_std),
        "speed_last": float(speeds[-1]),
        "speed_min": float(min(speeds)),
        "speed_max": float(max(speeds)),
        "n_points": n_points,
        "n_valid": float(n_valid),
        "valid_ratio": float(n_valid) / max(1.0, n_points),
        "dist_to_stop_m": dist,
        "bearing_err_deg": float(bearing_err_deg),
        "moved_m": float(moved_m),
        "avg_speed_calc": float(avg_speed_calc),
        "idle_ratio": float(idle_ratio),
        "speed_trend": float(speed_trend),
        "hist_delay_mean": float(sum(hist_delays[-8:]) / max(1, len(hist_delays[-8:]))),
        "hist_delay_last": float(hist_delays[-1]),
        "hist_delay_std": 0.0,
        "stops_before": float(stops_before),
        "progress_ratio": float(progress_ratio),
        "cur_dev_abs": abs(cur),
        "cur_dev_pos": max(0.0, cur),
        "eta_gap_sec": h - max(0.0, cur),
    }
    if len(hist_delays) > 1:
        m = feats["hist_delay_mean"]
        feats["hist_delay_std"] = math.sqrt(
            sum((d - m) ** 2 for d in hist_delays[-8:]) / len(hist_delays[-8:])
        )
    return feats
