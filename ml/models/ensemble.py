"""Ансамбль CatBoost + PyTorch LSTM."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger("mostrans.ensemble")

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"

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
    """Лёгкая LSTM по последовательности скорость/задержка."""

    def __init__(self, input_size: int = 2, hidden: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden, batch_first=True, num_layers=1)
        self.head = nn.Sequential(
            nn.Linear(hidden, 16),
            nn.ReLU(),
            nn.Linear(16, 2),  # prob logit, delay residual
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
    arr = np.stack([np.array(s, dtype=np.float32) / 60.0, np.array(d, dtype=np.float32) / 300.0], axis=-1)
    return arr


class EnsemblePredictor:
    """CatBoost (табличные) + PyTorch (последовательности) → взвешенный ансамбль."""

    def __init__(
        self,
        catboost_model: Any | None,
        torch_model: TelemetryLSTM,
        device: str = "cpu",
        onnx_session: Any | None = None,
    ):
        self.cb = catboost_model
        self.torch_model = torch_model.to(device)
        self.torch_model.eval()
        self.device = device
        self.onnx = onnx_session
        self.w_cb = 0.55
        self.w_torch = 0.45

    @classmethod
    def load_or_bootstrap(cls) -> "EnsemblePredictor":
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        cb = None
        cb_path = ARTIFACTS / "delay_catboost.cbm"
        try:
            from catboost import CatBoostRegressor

            if cb_path.exists():
                cb = CatBoostRegressor()
                cb.load_model(str(cb_path))
                logger.info("Loaded CatBoost from %s", cb_path)
            else:
                cb = cls._train_bootstrap_catboost(CatBoostRegressor)
                cb.save_model(str(cb_path))
                logger.info("Bootstrapped CatBoost → %s", cb_path)
        except Exception as e:
            logger.warning("CatBoost unavailable: %s", e)

        torch_model = TelemetryLSTM()
        pt_path = ARTIFACTS / "delay_lstm.pt"
        if pt_path.exists():
            torch_model.load_state_dict(torch.load(pt_path, map_location=device, weights_only=True))
            logger.info("Loaded LSTM from %s", pt_path)
        else:
            cls._train_bootstrap_lstm(torch_model)
            torch.save(torch_model.state_dict(), pt_path)
            logger.info("Bootstrapped LSTM → %s", pt_path)

        onnx_sess = None
        onnx_path = ARTIFACTS / "delay_lstm.onnx"
        if onnx_path.exists():
            try:
                import onnxruntime as ort

                onnx_sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
                logger.info("ONNX session ready")
            except Exception as e:
                logger.warning("ONNX not loaded: %s", e)

        return cls(cb, torch_model, device=device, onnx_session=onnx_sess)

    @staticmethod
    def _synth_rows(n: int = 800) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        X = np.zeros((n, len(TAB_FEATURES)), dtype=np.float32)
        # speed, delay, seg_avg, dwell, drop, doors, progress, dist
        X[:, 0] = rng.uniform(0, 50, n)
        X[:, 1] = rng.uniform(-30, 300, n)
        X[:, 2] = rng.uniform(5, 40, n)
        X[:, 3] = rng.uniform(0, 120, n)
        X[:, 4] = rng.uniform(0, 1, n)
        X[:, 5] = rng.uniform(0, 1, n)
        X[:, 6] = rng.uniform(0, 1, n)
        X[:, 7] = rng.uniform(0, 200, n)
        # target delay in 10–15 min horizon
        y_delay = (
            0.35 * X[:, 1]
            + 0.8 * X[:, 3]
            + 90 * X[:, 4]
            + 40 * X[:, 5]
            + rng.normal(0, 15, n)
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
            xt = torch.from_numpy(x)
            yt = torch.from_numpy(y)
            pred = model(xt)
            # sigmoid on first logit
            pred_prob = torch.sigmoid(pred[:, 0:1])
            pred_delay = pred[:, 1:2]
            pred_out = torch.cat([pred_prob, pred_delay], dim=1)
            loss = loss_fn(pred_out, yt)
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()

    def describe(self) -> dict[str, Any]:
        return {
            "ensemble": "catboost+pytorch_lstm",
            "catboost": self.cb is not None,
            "pytorch": True,
            "onnx": self.onnx is not None,
            "device": self.device,
            "horizon_sec": [600, 900],
            "weights": {"catboost": self.w_cb, "pytorch": self.w_torch},
        }

    def _vector(self, features: dict[str, Any]) -> np.ndarray:
        return np.array([[float(features.get(k, 0.0) or 0.0) for k in TAB_FEATURES]], dtype=np.float32)

    def _pattern(self, features: dict[str, Any]) -> str:
        dwell = float(features.get("dwell_sec") or 0)
        drop = float(features.get("speed_drop_ratio") or 0)
        speed = float(features.get("speed_kmh") or 0)
        seg = float(features.get("segment_avg_speed_kmh") or 0)
        doors = float(features.get("doors_open_ratio") or 0)
        delay = float(features.get("current_delay_sec") or 0)
        if dwell >= 45:
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
        seq_speeds: list[float],
        seq_delays: list[float],
        horizon_sec: int = 780,
    ) -> dict[str, Any]:
        horizon_sec = int(max(600, min(900, horizon_sec)))
        x = self._vector(features)

        cb_delay = float(features.get("current_delay_sec") or 0)
        if self.cb is not None:
            try:
                cb_delay = float(self.cb.predict(x)[0])
            except Exception:
                pass

        seq = _pad_seq(seq_speeds, seq_delays)
        with torch.no_grad():
            if self.onnx is not None:
                try:
                    out = self.onnx.run(None, {"input": seq[np.newaxis, ...]})[0][0]
                    torch_prob = float(1 / (1 + math.exp(-out[0])))
                    torch_delay = float(out[1] * 300)
                except Exception:
                    xt = torch.from_numpy(seq[np.newaxis, ...]).to(self.device)
                    out_t = self.torch_model(xt)[0]
                    torch_prob = float(torch.sigmoid(out_t[0]))
                    torch_delay = float(out_t[1].item() * 300)
            else:
                xt = torch.from_numpy(seq[np.newaxis, ...]).to(self.device)
                out_t = self.torch_model(xt)[0]
                torch_prob = float(torch.sigmoid(out_t[0]))
                torch_delay = float(out_t[1].item() * 300)

        delay = self.w_cb * cb_delay + self.w_torch * max(0.0, torch_delay)
        # вероятность из задержки + torch
        cb_prob = 1 / (1 + math.exp(-(cb_delay - 90) / 40))
        prob = self.w_cb * cb_prob + self.w_torch * torch_prob
        prob = float(max(0.0, min(0.99, prob)))
        delay = float(max(0.0, delay))

        # абсолютная ошибка прогноза (оценка residual)
        current = float(features.get("current_delay_sec") or 0)
        abs_err = abs(delay - current) * 0.35 + 12.0

        pattern = self._pattern(features)
        return {
            "delay_prob": round(prob, 4),
            "predicted_delay_sec": round(delay, 1),
            "abs_error_sec": round(abs_err, 1),
            "pattern": pattern,
            "cause": CAUSES[pattern],
            "horizon_sec": horizon_sec,
            "model": "ensemble-catboost-pytorch",
            "components": {
                "catboost_delay": round(cb_delay, 1),
                "torch_delay": round(torch_delay, 1),
                "torch_prob": round(torch_prob, 4),
            },
        }
