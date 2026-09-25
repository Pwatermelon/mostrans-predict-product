#!/usr/bin/env python3
"""Локальный smoke-тест без Docker: парсер NDTP + признаки + map matching."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.features.engine import FeatureEngine, RouteSegment
from app.ndtp.parser import parse_json_line


def main() -> None:
    routes = json.loads((ROOT / "data" / "routes.json").read_text(encoding="utf-8"))
    segs: list[RouteSegment] = []
    for r in routes["routes"]:
        pts = r["stops"]
        for i, p in enumerate(pts):
            nxt = pts[(i + 1) % len(pts)]
            segs.append(
                RouteSegment(
                    p["id"], r["id"], p["name"], p["lat"], p["lon"], nxt["lat"], nxt["lon"], 22
                )
            )
    eng = FeatureEngine(segs)
    line = (ROOT / "data" / "sample_telemetry.jsonl").read_text(encoding="utf-8").splitlines()[0]
    frame = parse_json_line(line)
    assert frame is not None
    frame.ts = time.time()
    feat = eng.update(frame)
    assert feat.matched_segment_id
    print("OK", feat.vehicle_id, feat.matched_segment_name, feat.speed_kmh)
    print("tabular", feat.tabular())


if __name__ == "__main__":
    main()
