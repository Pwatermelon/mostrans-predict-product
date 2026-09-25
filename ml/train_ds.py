#!/usr/bin/env python3
"""Train CatBoost (+ blend) on hackathon dataset and write validate submission.

Usage:
  .venv/bin/python ml/train_ds.py --data data/hackathon
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from features_ds import FEATURE_COLS, FeatureBuilder, load_split, score_hackathon


def build_matrix(root: Path, split: str, cache_dir: Path) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"features_{split}.parquet"
    if cache.exists():
        print(f"load cache {cache}")
        return pd.read_parquet(cache)
    print(f"building features for {split}...")
    traffic, schedule, points = load_split(root, split)
    print(f"  traffic={len(traffic)} schedule={len(schedule)} points={len(points)}")
    fb = FeatureBuilder(traffic, schedule)
    feats = fb.build(points)
    feats.to_parquet(cache, index=False)
    print(f"  saved {cache} rows={len(feats)}")
    return feats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/hackathon"))
    ap.add_argument("--out", type=Path, default=Path("artifacts"))
    ap.add_argument("--cache", type=Path, default=Path("data/cache"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    train = build_matrix(args.data, "train", args.cache)
    test = build_matrix(args.data, "test", args.cache)

    X_train = train[FEATURE_COLS].fillna(0).values
    y_train = train["target_delay_s"].values
    X_test = test[FEATURE_COLS].fillna(0).values
    y_test = test["target_delay_s"].values

    # baseline cur_dev
    for name, y_true, cur in [
        ("train", y_train, train["cur_dev_s"].values),
        ("test", y_test, test["cur_dev_s"].values),
    ]:
        m = score_hackathon(y_true, cur)
        print(f"baseline cur_dev {name}: MAE={m['mae']:.2f} mae_zero={m['mae_zero']:.2f}")

    model = CatBoostRegressor(
        iterations=1200,
        depth=6,
        learning_rate=0.05,
        loss_function="MAE",
        eval_metric="MAE",
        random_seed=42,
        verbose=100,
        l2_leaf_reg=3.0,
        subsample=0.85,
        allow_writing_files=False,
    )
    model.fit(X_train, y_train, eval_set=(X_test, y_test), use_best_model=True)

    pred_test = model.predict(X_test)
    # blend with cur_dev (strong prior)
    best = {"w": 1.0, "mae": 1e9}
    for w in np.linspace(0.0, 1.0, 21):
        blend = w * pred_test + (1 - w) * test["cur_dev_s"].values
        mae = float(np.mean(np.abs(y_test - blend)))
        if mae < best["mae"]:
            best = {"w": float(w), "mae": mae}
    print(f"best blend weight model={best['w']:.2f} MAE={best['mae']:.2f}")

    pred_blend = best["w"] * pred_test + (1 - best["w"]) * test["cur_dev_s"].values
    metrics = {
        "test_mae_model": float(np.mean(np.abs(y_test - pred_test))),
        "test_mae_cur": float(np.mean(np.abs(y_test - test["cur_dev_s"].values))),
        "test_mae_blend": best["mae"],
        "blend_w": best["w"],
        "test_score_est_blend": score_hackathon(y_test, pred_blend)["score_est"],
        "feature_importance": dict(
            sorted(
                zip(FEATURE_COLS, map(float, model.get_feature_importance())),
                key=lambda x: -x[1],
            )[:15]
        ),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    model_path = args.out / "delay_catboost_ds.cbm"
    model.save_model(str(model_path))
    (args.out / "train_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "blend_w.txt").write_text(str(best["w"]), encoding="utf-8")

    # validate submission
    print("building validate features...")
    val = build_matrix(args.data, "validate", args.cache)
    X_val = val[FEATURE_COLS].fillna(0).values
    pred_val = model.predict(X_val)
    pred_val = best["w"] * pred_val + (1 - best["w"]) * val["cur_dev_s"].values

    sample = pd.read_csv(args.data / "sample_submission.csv", sep=";")
    pred_map = dict(zip(val["sample_id"], pred_val))
    sample["prediction"] = sample["sample_id"].map(pred_map)
    if sample["prediction"].isna().any():
        missing = sample[sample["prediction"].isna()]["sample_id"].tolist()
        raise SystemExit(f"missing predictions: {missing[:5]}...")
    out_csv = args.out / "submission.csv"
    sample.to_csv(out_csv, sep=";", index=False)
    print(f"wrote {out_csv} rows={len(sample)}")
    print(sample.head())


if __name__ == "__main__":
    main()
