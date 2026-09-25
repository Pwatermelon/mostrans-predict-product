#!/usr/bin/env python3
"""Генерация демо-сабмита validate.csv для раздела Data Science.

Формат: vehicle_id,ts,predicted_delay_sec,delay_prob
Замените на реальный прогон по validate-датасету организаторов.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "artifacts" / "submit_validate.csv"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "predicted_delay_sec"])
        for i in range(1, 501):
            # placeholder — переобучите на официальном validate
            w.writerow([i, round(max(0, rng.gauss(90, 40)), 2)])
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
