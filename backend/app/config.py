"""Конфигурация Backend."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Параметры сервиса из env."""

    model_config = SettingsConfigDict(env_prefix="MT_", env_file=".env", extra="ignore")

    ml_url: str = "http://ml:8001"
    emulator_host: str = "emulator"
    emulator_port: int = 9000
    predict_horizon_sec: int = 780  # середина окна 10–15 мин (13 мин)
    horizon_min_sec: int = 600
    horizon_max_sec: int = 900
    poll_interval_sec: float = 1.0
    dashboard_dir: str = "/app/dashboard"
    data_dir: str = "/app/data"
    artifacts_dir: str = "/app/artifacts"
    degrade_after_sec: float = 8.0
    request_timeout_sec: float = 2.0


settings = Settings()
