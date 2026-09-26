"""Ансамбль: приоритет DS CatBoost (delay_catboost_ds) + LSTM fallback."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from feature_schema import FEATURE_COLS, MODEL_ID

logger = logging.getLogger("mostrans.ensemble")

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"

# legacy realtime bootstrap features (fallback only)
TAB_FEATURES = [
    "speed_kmh",
    "current_delay_sec",
    "segment_avg_speed_kmh",
    "dwell_sec",
    "speed_drop_ratio",
    "doors_open_ratio",
    "progress_ratio",
    "dist_to_segment_m",
]

CAUSES = {
    "speed_drop": "Аномальное снижение скорости на подходе к перекрёстку",
    "long_dwell": "Длительная посадка / простой на остановке",
    "congestion": "Затор на сегменте маршрута",
    "schedule_drift": "Накопившееся отклонение от нитки графика",
    "doors": "Частые открытия дверей вне остановок",
    "normal": "Без выраженного паттерна сбоя",
}


class TelemetryLSTM(nn.Module):
    def __init__(self, input_size: int = 2, hidden: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden, batch_first=True, num_layers=1)
        self.head = nn.Sequential(
            nn.Linear(hidden, 16),
            nn.ReLU(),
            nn.Linear(16, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


def _pad_seq(speeds: list[float], delays: list[float], n: int = 16) -> np.ndarray:
    s = list(speeds[-n:])
    d = list(delays[-n:])
    while len(s) < n:
        s.insert(0, s[0] if s else 0.0)
    while len(d) < n:
        d.insert(0, d[0] if d else 0.0)
    return np.stack(
        [np.array(s, dtype=np.float32) / 60.0, np.array(d, dtype=np.float32) / 300.0],
        axis=-1,
    )


class EnsemblePredictor:
    """DS CatBoost — основной предикт (тот же, что submission). LSTM — доп. сигнал."""

    def __init__(
        self,
        ds_model: Any | None,
        legacy_cb: Any | None,
        torch_model: TelemetryLSTM,
        device: str = "cpu",
        onnx_session: Any | None = None,
    ):
        self.ds = ds_model
        self.cb = legacy_cb
        self.torch_model = torch_model.to(device)
        self.torch_model.eval()
        self.device = device
        self.onnx = onnx_session
        self.primary = MODEL_ID if ds_model is not None else "ensemble-fallback"

    @classmethod
    def load_or_bootstrap(cls) -> "EnsemblePredictor":
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ds = None
        legacy = None
        try:
            from catboost import CatBoostRegressor

            ds_path = ARTIFACTS / "delay_catboost_ds.cbm"
            if ds_path.exists():
                ds = CatBoostRegressor()
                ds.load_model(str(ds_path))
                logger.info("Loaded DS CatBoost (submission model) from %s", ds_path)
            else:
                # также ищем в artifacts_public
                alt = Path("/app/artifacts_public/delay_catboost_ds.cbm")
                if alt.exists():
                    ds = CatBoostRegressor()
                    ds.load_model(str(alt))
                    logger.info("Loaded DS CatBoost from %s", alt)

            leg_path = ARTIFACTS / "delay_catboost.cbm"
            if leg_path.exists():
                legacy = CatBoostRegressor()
                legacy.load_model(str(leg_path))
            elif ds is None:
                legacy = cls._train_bootstrap_catboost(CatBoostRegressor)
                legacy.save_model(str(leg_path))
        except Exception as e:
            logger.warning("CatBoost load failed: %s", e)

        torch_model = TelemetryLSTM()
        pt_path = ARTIFACTS / "delay_lstm.pt"
        if pt_path.exists():
            torch_model.load_state_dict(
                torch.load(pt_path, map_location=device, weights_only=True)
            )
        else:
            cls._train_bootstrap_lstm(torch_model)
            torch.save(torch_model.state_dict(), pt_path)

        onnx_sess = None
        onnx_path = ARTIFACTS / "delay_lstm.onnx"
        if onnx_path.exists():
            try:
                import onnxruntime as ort

                onnx_sess = ort.InferenceSession(
                    str(onnx_path), providers=["CPUExecutionProvider"]
                )
            except Exception as e:
                logger.warning("ONNX not loaded: %s", e)

        return cls(ds, legacy, torch_model, device=device, onnx_session=onnx_sess)

    @staticmethod
    def _synth_rows(n: int = 800) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        X = np.zeros((n, len(TAB_FEATURES)), dtype=np.float32)
        X[:, 0] = rng.uniform(0, 50, n)
        X[:, 1] = rng.uniform(-30, 300, n)
        X[:, 2] = rng.uniform(5, 40, n)
        X[:, 3] = rng.uniform(0, 120, n)
        X[:, 4] = rng.uniform(0, 1, n)
        X[:, 5] = rng.uniform(0, 1, n)
        X[:, 6] = rng.uniform(0, 1, n)
        X[:, 7] = rng.uniform(0, 200, n)
        y_delay = (
            0.35 * X[:, 1] + 0.8 * X[:, 3] + 90 * X[:, 4] + 40 * X[:, 5] + rng.normal(0, 15, n)
        )
        y_delay = np.clip(y_delay, 0, 600)
        y_prob = 1 / (1 + np.exp(-(y_delay - 90) / 40))
        return X, y_delay.astype(np.float32), y_prob.astype(np.float32)

    @classmethod
    def _train_bootstrap_catboost(cls, CatBoostRegressor: Any) -> Any:
        X, y_delay, _ = cls._synth_rows()
        model = CatBoostRegressor(
            iterations=80,
            depth=4,
            learning_rate=0.1,
            loss_function="RMSE",
            verbose=False,
            allow_writing_files=False,
        )
        model.fit(X, y_delay)
        return model

    @classmethod
    def _train_bootstrap_lstm(cls, model: TelemetryLSTM) -> None:
        rng = np.random.default_rng(7)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()
        model.train()
        for _ in range(40):
            speeds = rng.uniform(0, 50, (32, 16))
            delays = rng.uniform(0, 200, (32, 16))
            x = np.stack([speeds / 60.0, delays / 300.0], axis=-1).astype(np.float32)
            target_delay = delays[:, -1] + rng.normal(0, 10, 32) + (50 - speeds[:, -1])
            target_prob = 1 / (1 + np.exp(-(target_delay - 90) / 40))
            y = np.stack([target_prob, target_delay / 300.0], axis=-1).astype(np.float32)
            pred = model(torch.from_numpy(x))
            pred_out = torch.cat([torch.sigmoid(pred[:, 0:1]), pred[:, 1:2]], dim=1)
            loss = loss_fn(pred_out, torch.from_numpy(y))
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()

    def describe(self) -> dict[str, Any]:
        return {
            "primary_model": self.primary,
            "ds_catboost": self.ds is not None,
            "legacy_catboost": self.cb is not None,
            "pytorch": True,
            "onnx": self.onnx is not None,
            "device": self.device,
            "feature_cols": FEATURE_COLS,
            "horizon_sec": [600, 900],
            "integrity": "submission.csv == delay_catboost_ds.cbm via POST /predict",
        }

    def _ds_vector(self, features: dict[str, Any]) -> np.ndarray:
        return np.array(
            [[float(features.get(k, 0.0) or 0.0) for k in FEATURE_COLS]],
            dtype=np.float32,
        )

    def _legacy_vector(self, features: dict[str, Any]) -> np.ndarray:
        # map DS-ish online keys → legacy
        mapped = {
            "speed_kmh": features.get("speed_last", features.get("speed_kmh", 0)),
            "current_delay_sec": features.get("cur_dev_s", features.get("current_delay_sec", 0)),
            "segment_avg_speed_kmh": features.get("speed_mean", features.get("segment_avg_speed_kmh", 0)),
            "dwell_sec": features.get("dwell_sec", features.get("idle_ratio", 0) * 60),
            "speed_drop_ratio": features.get("speed_drop_ratio", 0),
            "doors_open_ratio": features.get("doors_open_ratio", features.get("idle_ratio", 0)),
            "progress_ratio": features.get("progress_ratio", 0),
            "dist_to_segment_m": features.get(
                "dist_to_stop_m", features.get("dist_to_segment_m", 0)
            ),
        }
        return np.array([[float(mapped.get(k, 0.0) or 0.0) for k in TAB_FEATURES]], dtype=np.float32)

    def _pattern(self, features: dict[str, Any]) -> str:
        dwell = float(features.get("dwell_sec") or features.get("idle_ratio", 0) * 100)
        drop = float(features.get("speed_drop_ratio") or 0)
        speed = float(features.get("speed_last") or features.get("speed_kmh") or 0)
        seg = float(features.get("speed_mean") or features.get("segment_avg_speed_kmh") or 0)
        doors = float(features.get("doors_open_ratio") or 0)
        delay = float(features.get("cur_dev_s") or features.get("current_delay_sec") or 0)
        if dwell >= 45 or float(features.get("idle_ratio") or 0) > 0.7:
            return "long_dwell"
        if drop >= 0.55 and speed < 12:
            return "speed_drop"
        if seg < 10 and speed < 15:
            return "congestion"
        if doors > 0.4 and speed > 5:
            return "doors"
        if delay >= 60:
            return "schedule_drift"
        return "normal"

    def predict(
        self,
        features: dict[str, Any],
        seq_speeds: list[float] | None = None,
        seq_delays: list[float] | None = None,
        horizon_sec: int = 780,
    ) -> dict[str, Any]:
        seq_speeds = seq_speeds or []
        seq_delays = seq_delays or []
        horizon_sec = int(max(600, min(900, horizon_sec or 780)))
        # ensure horizon in features for DS
        if "horizon_sec" not in features or not features.get("horizon_sec"):
            features = {**features, "horizon_sec": float(horizon_sec)}

        pattern = self._pattern(features)
        current = float(
            features.get("cur_dev_s")
            or features.get("current_delay_sec")
            or 0
        )

        # --- primary: DS CatBoost ---
        if self.ds is not None:
            x = self._ds_vector(features)
            delay = float(self.ds.predict(x)[0])
            # LSTM soft signal only for probability nuance
            torch_prob = 0.5
            torch_delay = delay
            if seq_speeds:
                seq = _pad_seq(seq_speeds, seq_delays or [current] * len(seq_speeds))
                with torch.no_grad():
                    xt = torch.from_numpy(seq[np.newaxis, ...]).to(self.device)
                    out_t = self.torch_model(xt)[0]
                    torch_prob = float(torch.sigmoid(out_t[0]))
                    torch_delay = float(out_t[1].item() * 300)
            cb_prob = 1 / (1 + math.exp(-(delay - 90) / 40))
            prob = float(max(0.0, min(0.99, 0.85 * cb_prob + 0.15 * torch_prob)))
            abs_err = abs(delay - current) * 0.35 + 12.0
            return {
                "delay_prob": round(prob, 4),
                "predicted_delay_sec": round(delay, 1),
                "abs_error_sec": round(abs_err, 1),
                "pattern": pattern,
                "cause": CAUSES[pattern],
                "horizon_sec": horizon_sec,
                "model": MODEL_ID,
                "components": {
                    "ds_catboost_delay": round(delay, 1),
                    "torch_delay": round(torch_delay, 1),
                    "torch_prob": round(torch_prob, 4),
                },
            }

        # --- fallback legacy ensemble ---
        x = self._legacy_vector(features)
        cb_delay = current
        if self.cb is not None:
            try:
                cb_delay = float(self.cb.predict(x)[0])
            except Exception:
                pass
        seq = _pad_seq(seq_speeds, seq_delays)
        with torch.no_grad():
            xt = torch.from_numpy(seq[np.newaxis, ...]).to(self.device)
            out_t = self.torch_model(xt)[0]
            torch_prob = float(torch.sigmoid(out_t[0]))
            torch_delay = float(out_t[1].item() * 300)
        delay = 0.55 * cb_delay + 0.45 * max(0.0, torch_delay)
        cb_prob = 1 / (1 + math.exp(-(cb_delay - 90) / 40))
        prob = float(max(0.0, min(0.99, 0.55 * cb_prob + 0.45 * torch_prob)))
        return {
            "delay_prob": round(prob, 4),
            "predicted_delay_sec": round(float(max(0.0, delay)), 1),
            "abs_error_sec": round(abs(delay - current) * 0.35 + 12.0, 1),
            "pattern": pattern,
            "cause": CAUSES[pattern],
            "horizon_sec": horizon_sec,
            "model": "ensemble-fallback",
            "components": {
                "catboost_delay": round(cb_delay, 1),
                "torch_delay": round(torch_delay, 1),
                "torch_prob": round(torch_prob, 4),
            },
        }
