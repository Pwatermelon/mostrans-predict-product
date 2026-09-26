#!/usr/bin/env python3
"""Проверка parity: офлайн CatBoost DS ≡ HTTP POST /predict.

Usage:
  .venv/bin/python scripts/verify_stream_parity.py \\
    --data data/hackathon --model artifacts/public/delay_catboost_ds.cbm \\
    --url http://127.0.0.1:8001 --n 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml"))

from feature_schema import FEATURE_COLS, MODEL_ID  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "hackathon")
    ap.add_argument("--cache", type=Path, default=ROOT / "data" / "cache")
    ap.add_argument(
        "--model",
        type=Path,
        default=ROOT / "artifacts" / "public" / "delay_catboost_ds.cbm",
    )
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    feats_path = args.cache / "features_test.parquet"
    if not feats_path.exists():
        feats_path = args.cache / "features_validate.parquet"
    if not feats_path.exists():
        raise SystemExit(f"No feature cache at {args.cache} — run train_ds.py first")

    df = pd.read_parquet(feats_path).head(args.n)
    model = CatBoostRegressor()
    model.load_model(str(args.model))

    X = df[FEATURE_COLS].fillna(0).values
    offline = model.predict(X)

    diffs = []
    models = []
    with httpx.Client(timeout=10.0) as client:
        info = client.get(f"{args.url}/info").json()
        print("ML /info:", json.dumps(info, ensure_ascii=False)[:400])
        for i, (_, row) in enumerate(df.iterrows()):
            feats = {c: float(row[c]) if pd.notna(row[c]) else 0.0 for c in FEATURE_COLS}
            r = client.post(
                f"{args.url}/predict",
                json={"features": feats, "horizon_sec": int(feats.get("horizon_sec", 780) or 780)},
            )
            r.raise_for_status()
            body = r.json()
            online = float(body["predicted_delay_sec"])
            off = float(offline[i])
            diffs.append(abs(online - off))
            models.append(body.get("model"))

    max_diff = float(np.max(diffs))
    mean_diff = float(np.mean(diffs))
    ok = max_diff < 0.05 and all(m == MODEL_ID for m in models)
    print(
        json.dumps(
            {
                "n": len(diffs),
                "mean_abs_diff": round(mean_diff, 6),
                "max_abs_diff": round(max_diff, 6),
                "model_ids": sorted(set(models)),
                "expected_model": MODEL_ID,
                "parity_ok": ok,
            },
            indent=2,
        )
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
