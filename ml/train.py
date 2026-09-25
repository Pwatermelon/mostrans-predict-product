"""Обучение ансамбля на синтетике / CSV датасета.

Usage:
  python train.py --out artifacts
  python train.py --csv /path/to/train.csv --out artifacts
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from models.ensemble import ARTIFACTS, TAB_FEATURES, EnsemblePredictor, TelemetryLSTM


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MosTrans ML ensemble")
    parser.add_argument("--out", type=Path, default=ARTIFACTS)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    from catboost import CatBoostRegressor

    if args.csv and args.csv.exists():
        import csv

        rows = []
        with args.csv.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        X = np.array(
            [[float(r.get(k, 0) or 0) for k in TAB_FEATURES] for r in rows],
            dtype=np.float32,
        )
        y = np.array(
            [float(r.get("target_delay_sec") or r.get("y") or 0) for r in rows],
            dtype=np.float32,
        )
    else:
        X, y, _ = EnsemblePredictor._synth_rows(2000)

    cb = CatBoostRegressor(
        iterations=120,
        depth=5,
        learning_rate=0.08,
        loss_function="RMSE",
        verbose=False,
        allow_writing_files=False,
    )
    cb.fit(X, y)
    cb_path = args.out / "delay_catboost.cbm"
    cb.save_model(str(cb_path))
    print(f"Saved {cb_path}")

    lstm = TelemetryLSTM()
    EnsemblePredictor._train_bootstrap_lstm(lstm)
    # extra epochs on synth sequences correlated with y
    pt_path = args.out / "delay_lstm.pt"
    torch.save(lstm.state_dict(), pt_path)
    print(f"Saved {pt_path}")

    # ONNX export
    try:
        dummy = torch.zeros(1, 16, 2)
        onnx_path = args.out / "delay_lstm.onnx"
        torch.onnx.export(
            lstm,
            dummy,
            str(onnx_path),
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
            opset_version=17,
        )
        print(f"Saved {onnx_path}")
    except Exception as e:
        print(f"ONNX export skipped: {e}")


if __name__ == "__main__":
    main()
