#!/usr/bin/env python3
"""Final model: train on train+test, predict validate → artifacts/submission.csv"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from features_ds import FEATURE_COLS, score_hackathon

ROOT = Path(__file__).resolve().parent
# локально: ml/predict_validate.py → repo root = parent.parent
# в Docker: /app/predict_validate.py → root = /app
if (ROOT / "data" / "hackathon").exists():
    pass
elif (ROOT.parent / "data" / "hackathon").exists():
    ROOT = ROOT.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "artifacts"
DATA = ROOT / "data" / "hackathon"


def main() -> None:
    train = pd.read_parquet(CACHE / "features_train.parquet")
    test = pd.read_parquet(CACHE / "features_test.parquet")
    val = pd.read_parquet(CACHE / "features_validate.parquet")
    full = pd.concat([train, test], ignore_index=True)

    X = full[FEATURE_COLS].fillna(0).values
    y = full["target_delay_s"].values
    X_te = test[FEATURE_COLS].fillna(0).values
    y_te = test["target_delay_s"].values

    model = CatBoostRegressor(
        iterations=1500,
        depth=6,
        learning_rate=0.04,
        loss_function="MAE",
        eval_metric="MAE",
        random_seed=42,
        verbose=200,
        l2_leaf_reg=2.5,
        subsample=0.9,
        allow_writing_files=False,
    )
    # hold out test for early stopping even though we train on full after
    model_es = CatBoostRegressor(
        iterations=1500,
        depth=6,
        learning_rate=0.04,
        loss_function="MAE",
        eval_metric="MAE",
        random_seed=42,
        verbose=200,
        l2_leaf_reg=2.5,
        subsample=0.9,
        allow_writing_files=False,
    )
    X_tr = train[FEATURE_COLS].fillna(0).values
    y_tr = train["target_delay_s"].values
    model_es.fit(X_tr, y_tr, eval_set=(X_te, y_te), use_best_model=True)
    best_iter = model_es.get_best_iteration() or 1000
    print("best_iter", best_iter)

    model = CatBoostRegressor(
        iterations=max(200, best_iter + 50),
        depth=6,
        learning_rate=0.04,
        loss_function="MAE",
        random_seed=42,
        verbose=200,
        l2_leaf_reg=2.5,
        subsample=0.9,
        allow_writing_files=False,
    )
    model.fit(X, y)

    pred_te = model.predict(X_te)
    m = score_hackathon(y_te, pred_te)
    print("final test MAE", m)

    pred_val = model.predict(val[FEATURE_COLS].fillna(0).values)
    sample = pd.read_csv(DATA / "sample_submission.csv", sep=";")
    pred_map = dict(zip(val["sample_id"].astype(str), pred_val))
    sample["sample_id"] = sample["sample_id"].astype(str)
    sample["prediction"] = sample["sample_id"].map(pred_map)
    assert not sample["prediction"].isna().any()

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "submission.csv"
    sample.to_csv(out, sep=";", index=False)
    model.save_model(str(OUT / "delay_catboost_ds.cbm"))
    (OUT / "train_metrics.json").write_text(
        json.dumps(
            {
                "test_mae": m["mae"],
                "mae_zero_test": m["mae_zero"],
                "score_est": m["score_est"],
                "best_iter": best_iter,
                "n_train_full": len(full),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    print(sample.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
