#!/usr/bin/env python3
"""
Build a replay fixture from recorded FloodNet readings.

SOURCE DATASETS, both NYC Open Data, downloaded 2026-08-29:

  FloodNet: Street Flooding Events Measured by FloodNet Sensors
      2,929 rows, 296 sensors, events from 2020-11-16 to 2026-08-11 (GMT).
      Each row is one flood event at one sensor. The row is a summary
      (start, end, peak, durations), but it carries the underlying series
      inline in two columns: "Time Series Depth Values (inches)" and
      "Time Series Depth Timestamps (seconds)", the latter an offset from
      the flood start. 353,771 depth samples in total, median cadence 63 s.
      Nothing here is interpolated or synthesised; these are the samples
      FloodNet recorded.

  FloodNet: Sensor Deployment Metadata
      479 rows. The events file carries NO coordinates, so location comes
      from here, joined on "Sensor ID".

EVENT SELECTED: 2025-10-30, the largest multi-sensor day in the download.
109 sensors crossed 5 cm; 126 have samples in the window
17:23:54 GMT on 10-30 through 01:44:02 GMT on 10-31, 8.3 hours.

UNITS: the source is inches. Converted to centimetres by x 2.54.

Why no baseline correction is applied, which is the opposite of USGS (E-001):
these values are already flood depth above ground, not range to the water
surface. Verified rather than assumed: 478 of the first 500 events start at
exactly 0.00, and there is not one negative sample anywhere in the file. A
USGS-style per-site p10 baseline would be wrong here.

SENSOR DROPPED: BK-w-st-kent-st-31i7yc has events but no row in the
deployment metadata, so it has no coordinates. It is dropped rather than
placed at a guessed location. It has no samples on 2025-10-30, so it does
not affect this fixture, and the guard stays for any other date.

WHAT THIS PRESERVES AND WHAT IT DOES NOT. Real sensor ids, real coordinates
and real recorded depths, unmodified. It does not preserve the original
clock: frames are subsampled at a fixed interval, and ReplaySource stamps
observed_at with time.now() and advances one frame per poll. So the replay
reproduces the shape and spacing of the event, compressed, not the event as
it occurred. Say so in the report.

    .venv/bin/python data/convert_floodnet.py \
        --events  <events.csv> \
        --metadata <metadata.csv> \
        --out data/replay.floodnet.json
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

IN_TO_CM = 2.54

# Events file carries no location. Without a metadata row a sensor cannot be
# placed, and a guessed coordinate is worse than an absent sensor.
NO_METADATA_DROP = "BK-w-st-kent-st-31i7yc"

# A frame is one moment across the network. A sensor joins a frame only if it
# has a real sample within this tolerance of the tick. Nothing is forward
# filled beyond it, because a stale reading presented as current is the same
# lie as an invented one.
MATCH_TOLERANCE_SECONDS = 300


def parse_gmt(value: str) -> datetime:
    return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").replace(
        tzinfo=timezone.utc
    )


def load_metadata(path: str) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = row["Sensor ID"]
            if not row["Latitude"] or not row["Longitude"]:
                continue
            out[sid] = {
                "name": row["Sensor Name"],
                "lat": float(row["Latitude"]),
                "lon": float(row["Longitude"]),
                "borough": row["Borough"],
                "nta": row["NTA"],
            }
    return out


def load_samples(
    path: str, meta: dict, day_start: datetime, day_end: datetime
) -> dict[str, list[tuple[datetime, float]]]:
    """Expand each event row's inline series into absolute-time samples."""
    samples: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    dropped_no_meta: set[str] = set()

    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row["Flood Start Datetime (GMT)"]:
                continue
            start = parse_gmt(row["Flood Start Datetime (GMT)"])
            end = parse_gmt(row["Flood End Datetime (GMT)"])
            if end < day_start or start >= day_end:
                continue

            sid = row["Sensor ID"]
            if sid not in meta:
                dropped_no_meta.add(sid)
                continue

            depths = json.loads(row["Time Series Depth Values (inches)"])
            offsets = json.loads(row["Time Series Depth Timestamps (seconds)"])
            for inches, offset in zip(depths, offsets):
                samples[sid].append(
                    (start + timedelta(seconds=offset), inches * IN_TO_CM)
                )

    for sid in sorted(dropped_no_meta):
        print(f"  dropped {sid}: no deployment metadata, cannot be placed",
              file=sys.stderr)
    for series in samples.values():
        series.sort()
    return samples


def build_frames(samples: dict, meta: dict, interval_minutes: int) -> list[list[dict]]:
    every = [t for series in samples.values() for t, _ in series]
    first, last = min(every), max(every)

    ticks: list[datetime] = []
    tick = first
    step = timedelta(minutes=interval_minutes)
    while tick <= last:
        ticks.append(tick)
        tick += step

    frames: list[list[dict]] = []
    for tick in ticks:
        frame: list[dict] = []
        for sid, series in samples.items():
            nearest = min(series, key=lambda s: abs((s[0] - tick).total_seconds()))
            if abs((nearest[0] - tick).total_seconds()) > MATCH_TOLERANCE_SECONDS:
                continue
            info = meta[sid]
            frame.append({
                "sensor_id": sid,
                "name": info["name"],
                "lon": info["lon"],
                "lat": info["lat"],
                "depth_cm": round(nearest[1], 2),
            })
        if frame:
            frames.append(frame)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--date", default="2025-10-30",
                    help="UTC day the event starts on")
    ap.add_argument("--interval-minutes", type=int, default=10,
                    help="frame spacing. ReplaySource plays one frame per "
                         "poll, so this sets how fast the event replays")
    ap.add_argument("--out", default="data/replay.floodnet.json")
    args = ap.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    meta = load_metadata(args.metadata)
    if NO_METADATA_DROP in meta:
        print(f"  note: {NO_METADATA_DROP} now has metadata, guard is stale",
              file=sys.stderr)

    samples = load_samples(args.events, meta, day, day + timedelta(days=1))
    if not samples:
        print(f"no samples on {args.date}", file=sys.stderr)
        return 1

    frames = build_frames(samples, meta, args.interval_minutes)

    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frames))

    readings = sum(len(f) for f in frames)
    span = max(t for s in samples.values() for t, _ in s) - \
        min(t for s in samples.values() for t, _ in s)
    peak = max(d for s in samples.values() for _, d in s)
    print(f"event {args.date}: {span.total_seconds()/3600:.1f} h, "
          f"{len(samples)} sensors, peak {peak:.1f} cm")
    print(f"wrote {len(frames)} frames, {readings} readings, "
          f"{path.stat().st_size/1024:.0f} KB to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
