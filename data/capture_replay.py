#!/usr/bin/env python3
"""
Capture live readings into a replay file.

This exists to defuse the demo risk. The rubric's largest single criterion is
demonstrating the project working end to end, and a live map of dry sensors on
a clear day demonstrates nothing. Leave this running through any rain event,
and you have a real captured storm to drive the presentation.

The replay is real data, recorded, not synthesized. Say so in the report.

    python3 data/capture_replay.py --minutes 90 --out data/replay.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

from curbline import config
from curbline.sources import build_source


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--interval", type=int, default=config.SENSOR_POLL_SECONDS)
    ap.add_argument("--out", default="data/replay.json")
    args = ap.parse_args()

    source = build_source()
    frames: list[list[dict]] = []
    deadline = time.time() + args.minutes * 60

    print(f"capturing from {source.name} for {args.minutes} min")
    while time.time() < deadline:
        frame = [{
            "sensor_id": r.sensor_id,
            "name": r.name,
            "lon": r.lon,
            "lat": r.lat,
            "depth_cm": r.depth_cm,
        } for r in source.fetch()]

        frames.append(frame)
        wet = sum(1 for r in frame if r["depth_cm"] >= config.DEPTH_THRESHOLD_CM)
        print(f"  frame {len(frames):3d}  {len(frame):3d} readings  {wet} wet")
        time.sleep(args.interval)

    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frames))
    print(f"wrote {len(frames)} frames to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
