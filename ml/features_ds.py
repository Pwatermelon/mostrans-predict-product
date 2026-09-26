"""Feature engineering for MosTrans delay prediction (anti-leakage: event_time ≤ T)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from feature_schema import FEATURE_COLS
except ImportError:  # noqa: F401 — fallback when run as script from repo root
    FEATURE_COLS = [
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


def _parse_point(geom: str | float | None) -> tuple[float, float] | None:
    if geom is None or (isinstance(geom, float) and np.isnan(geom)):
        return None
    m = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", str(geom))
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))
    return lon, lat


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dl = lon2 - lon1
    x = np.sin(dl) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dl)
    return float((np.degrees(np.arctan2(x, y)) + 360) % 360)


class FeatureBuilder:
    """Builds tabular features for each (tr_id, T) prediction point."""

    def __init__(self, traffic: pd.DataFrame, schedule: pd.DataFrame):
        self.traffic = traffic.copy()
        self.schedule = schedule.copy()
        self.traffic["event_time"] = pd.to_datetime(self.traffic["event_time"])
        self.traffic["tr_id"] = self.traffic["tr_id"].astype(int)
        for c in ("speed", "lon", "lat", "heading"):
            if c in self.traffic.columns:
                self.traffic[c] = pd.to_numeric(self.traffic[c], errors="coerce")

        self.schedule["tr_id"] = self.schedule["tr_id"].astype(int)
        self.schedule["tt_action_item_id"] = self.schedule["tt_action_item_id"].astype(np.int64)
        self.schedule["time_begin"] = pd.to_datetime(self.schedule["time_begin"])
        if "time_fact_begin" in self.schedule.columns:
            self.schedule["time_fact_begin"] = pd.to_datetime(self.schedule["time_fact_begin"])
            self.schedule["delay_s"] = (
                self.schedule["time_fact_begin"] - self.schedule["time_begin"]
            ).dt.total_seconds()
        else:
            self.schedule["delay_s"] = np.nan

        self.schedule["lon"] = np.nan
        self.schedule["lat"] = np.nan
        if "geom" in self.schedule.columns:
            coords = self.schedule["geom"].map(_parse_point)
            self.schedule["lon"] = coords.map(lambda x: x[0] if x else np.nan)
            self.schedule["lat"] = coords.map(lambda x: x[1] if x else np.nan)

        self.traffic = self.traffic.sort_values(["tr_id", "event_time"])
        self.schedule = self.schedule.sort_values(["tr_id", "time_begin"])
        self._traffic_by_tr = {tid: g for tid, g in self.traffic.groupby("tr_id", sort=False)}
        self._sched_by_tr = {tid: g for tid, g in self.schedule.groupby("tr_id", sort=False)}

    def row_features(self, row: pd.Series) -> dict[str, float]:
        tr_id = int(row["tr_id"])
        T = pd.to_datetime(row["T"])
        target_begin = pd.to_datetime(row["target_time_begin"])
        cur_dev = float(row.get("cur_dev_s", 0.0) or 0.0)
        target_stop = int(row["target_stop_id"])

        horizon = (target_begin - T).total_seconds()
        feats: dict[str, float] = {
            "cur_dev_s": cur_dev,
            "cur_dev_abs": abs(cur_dev),
            "cur_dev_pos": max(0.0, cur_dev),
            "horizon_sec": horizon,
            "hour": float(T.hour) + T.minute / 60.0,
            "minute": float(T.minute),
            "dow": float(T.dayofweek),
            "speed_mean": 0.0,
            "speed_std": 0.0,
            "speed_last": 0.0,
            "speed_min": 0.0,
            "speed_max": 0.0,
            "n_points": 0.0,
            "n_valid": 0.0,
            "valid_ratio": 0.0,
            "dist_to_stop_m": -1.0,
            "bearing_err_deg": 0.0,
            "moved_m": 0.0,
            "avg_speed_calc": 0.0,
            "idle_ratio": 0.0,
            "speed_trend": 0.0,
            "hist_delay_mean": cur_dev,
            "hist_delay_last": cur_dev,
            "hist_delay_std": 0.0,
            "stops_before": 0.0,
            "progress_ratio": 0.0,
            "eta_gap_sec": horizon - max(0.0, cur_dev),
        }

        # --- telemetry window: last 20 min before T ---
        traf = self._traffic_by_tr.get(tr_id)
        if traf is not None:
            win_start = T - pd.Timedelta(minutes=20)
            w = traf[(traf["event_time"] <= T) & (traf["event_time"] >= win_start)]
            feats["n_points"] = float(len(w))
            if len(w):
                valid = w[w["location_valid"].astype(str).str.lower().isin(["true", "1"])]
                # also accept boolean True
                if valid.empty and "location_valid" in w.columns:
                    valid = w[w["location_valid"] == True]  # noqa: E712
                feats["n_valid"] = float(len(valid))
                feats["valid_ratio"] = feats["n_valid"] / max(1.0, feats["n_points"])

                speeds = w["speed"].dropna()
                if len(speeds):
                    feats["speed_mean"] = float(speeds.mean())
                    feats["speed_std"] = float(speeds.std(ddof=0) if len(speeds) > 1 else 0.0)
                    feats["speed_last"] = float(speeds.iloc[-1])
                    feats["speed_min"] = float(speeds.min())
                    feats["speed_max"] = float(speeds.max())
                    feats["idle_ratio"] = float((speeds < 3.0).mean())
                    if len(speeds) >= 4:
                        half = len(speeds) // 2
                        feats["speed_trend"] = float(speeds.iloc[half:].mean() - speeds.iloc[:half].mean())

                coords = w.dropna(subset=["lon", "lat"])
                if len(coords) >= 2:
                    c0, c1 = coords.iloc[0], coords.iloc[-1]
                    feats["moved_m"] = haversine_m(c0["lon"], c0["lat"], c1["lon"], c1["lat"])
                    dt = (c1["event_time"] - c0["event_time"]).total_seconds()
                    if dt > 1:
                        feats["avg_speed_calc"] = feats["moved_m"] / dt * 3.6

                # distance / bearing to target stop
                stop_row = None
                sched = self._sched_by_tr.get(tr_id)
                if sched is not None:
                    hit = sched[sched["tt_action_item_id"] == target_stop]
                    if len(hit):
                        stop_row = hit.iloc[0]
                if stop_row is not None and not np.isnan(stop_row.get("lon", np.nan)):
                    last = coords.iloc[-1] if len(coords) else None
                    if last is not None:
                        feats["dist_to_stop_m"] = haversine_m(
                            last["lon"], last["lat"], stop_row["lon"], stop_row["lat"]
                        )
                        br = bearing_deg(last["lon"], last["lat"], stop_row["lon"], stop_row["lat"])
                        head = float(last["heading"]) if pd.notna(last.get("heading")) else br
                        diff = abs(br - head) % 360
                        feats["bearing_err_deg"] = min(diff, 360 - diff)

        # --- schedule history before T ---
        sched = self._sched_by_tr.get(tr_id)
        if sched is not None:
            past = sched[sched["time_begin"] <= T]
            future = sched[sched["time_begin"] > T]
            if "delay_s" in past.columns:
                delays = past["delay_s"].dropna()
                if len(delays):
                    feats["hist_delay_mean"] = float(delays.tail(8).mean())
                    feats["hist_delay_last"] = float(delays.iloc[-1])
                    feats["hist_delay_std"] = float(delays.tail(8).std(ddof=0) if len(delays) > 1 else 0.0)

            # stops until target
            until = sched[
                (sched["time_begin"] > T) & (sched["time_begin"] <= target_begin)
            ]
            feats["stops_before"] = float(len(until))
            day = sched[sched["time_begin"].dt.date == T.date()].reset_index(drop=True)
            if len(day):
                hit = np.where(day["tt_action_item_id"].values == target_stop)[0]
                if len(hit):
                    feats["progress_ratio"] = float(hit[0] / max(1, len(day) - 1))

        return feats

    def build(self, points: pd.DataFrame, show_progress: bool = True) -> pd.DataFrame:
        rows = []
        n = len(points)
        for i, (_, row) in enumerate(points.iterrows()):
            if show_progress and i % 500 == 0:
                print(f"  features {i}/{n}", flush=True)
            f = self.row_features(row)
            f["sample_id"] = row["sample_id"]
            if "target_delay_s" in row.index and pd.notna(row["target_delay_s"]):
                f["target_delay_s"] = float(row["target_delay_s"])
            rows.append(f)
        return pd.DataFrame(rows)


def load_split(root: Path, split: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load traffic, schedule, points/labels for a split."""
    root = Path(root)
    if split == "train":
        traffic = pd.read_csv(root / "train" / "traffic.csv")
        schedule = pd.read_csv(root / "train" / "schedule.csv")
        points = pd.read_csv(root / "labels" / "labels_train.csv")
    elif split == "test":
        traffic = pd.read_csv(root / "test" / "traffic.csv")
        schedule = pd.read_csv(root / "test" / "schedule.csv")
        points = pd.read_csv(root / "labels" / "labels_test.csv")
    elif split == "validate":
        traffic = pd.read_csv(root / "validate" / "traffic.csv")
        schedule = pd.read_csv(root / "validate" / "schedule_plan.csv")
        points = pd.read_csv(root / "validate" / "points.csv")
    else:
        raise ValueError(split)
    return traffic, schedule, points


def score_hackathon(y_true: np.ndarray, y_pred: np.ndarray, mae_target: float = 40.0) -> dict[str, float]:
    """Approximate platform score; MAE_TARGET unknown — report MAE and relative to zero/cur."""
    mae = float(np.mean(np.abs(y_true - y_pred)))
    mae_zero = float(np.mean(np.abs(y_true)))
    denom = max(1e-6, mae_zero - mae_target)
    score = max(0.0, min(1.0, (mae_zero - mae) / denom))
    return {"mae": mae, "mae_zero": mae_zero, "score_est": score}
