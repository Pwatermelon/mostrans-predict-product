"""Единая схема признаков DS CatBoost (офлайн сабмит ≡ онлайн /predict)."""

from __future__ import annotations

# Порядок колонок должен совпадать с обучением delay_catboost_ds.cbm
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

MODEL_ID = "delay_catboost_ds"
