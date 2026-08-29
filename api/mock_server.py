#!/usr/bin/env python3
"""
Mock console server. No AWS, no database, no cache, no credentials.

This exists to break a scheduling dependency. Provisioning is the highest-risk
part of this build, and without this file the frontend cannot be started or
demonstrated until RDS, ElastiCache and the queues are all up. With it, the UI
can be built and reviewed on a laptop while provisioning runs in parallel, and
it gives you something to show if AWS is having a bad morning.

It serves the exact same payload shape as api/server.py, driven by a scripted
storm that escalates and then recedes, so every visual state (forming, active,
receding, corroborated, warning) is reachable on demand.

    python3 api/mock_server.py
    open http://localhost:8000

This is a development tool. It is not part of the graded pipeline and must not
be used to produce evidence screenshots.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import pathlib
import random
import time
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

# One storm cycle, in seconds. Short enough to demo, long enough to read.
CYCLE = 180.0
START = time.time()

# The dead-letter indicator is hidden while zero, so exercising it needs a way
# to force it without a real poison message.
MOCK_DLQ = int(os.environ.get("CURBLINE_MOCK_DLQ", "0"))

# 8000 stays the default: the deployed console is reached on it and the security
# group opens it. Overridable because a developer machine may already have 8000
# taken or firewalled, and this server exists precisely so frontend work does
# not have to wait on anything.
PORT = int(os.environ.get("CURBLINE_MOCK_PORT", "8000"))

THRESHOLDS = {
    "detect_cm": 5.0, "advisory_cm": 10.0,
    "curb_cm": 15.0, "warning_cm": 20.0,
}

# Two real clusters plus scattered dry sensors, at true NYC coordinates.
SENSORS = [
    ("mock:q1", "Jamaica Ave & 150th",   -73.7975, 40.7020, 1.00),
    ("mock:q2", "Jamaica Ave & 153rd",   -73.7940, 40.7025, 0.78),
    ("mock:q3", "Hillside Ave & 150th",  -73.7968, 40.7062, 1.35),
    ("mock:q4", "90th Ave & 150th",      -73.7981, 40.6995, 0.55),
    ("mock:b1", "Van Brunt & Beard",     -74.0165, 40.6748, 0.70),
    ("mock:b2", "Van Brunt & Reed",      -74.0152, 40.6761, 0.82),
    ("mock:x1", "Bruckner & Zerega",     -73.8430, 40.8290, 0.30),
    ("mock:m1", "FDR & Montgomery",      -73.9782, 40.7115, 0.10),
    ("mock:m2", "W 125th & 12th Ave",    -73.9570, 40.8190, 0.08),
    ("mock:s1", "Bay St & Victory Blvd", -74.0760, 40.6410, 0.12),
]

QUEENS = {"mock:q1", "mock:q2", "mock:q3", "mock:q4"}
REDHOOK = {"mock:b1", "mock:b2"}

_advisories: list[dict] = []
_zone_states: dict[str, str] = {}


def storm_intensity() -> float:
    """Rises to a peak, then recedes. Sine over the cycle, clamped positive."""
    phase = ((time.time() - START) % CYCLE) / CYCLE
    return max(0.0, math.sin(phase * math.pi)) * 29.0


def readings() -> list[tuple]:
    peak = storm_intensity()
    out = []
    for sid, name, lon, lat, gain in SENSORS:
        jitter = random.uniform(-0.4, 0.4)
        out.append((sid, name, lon, lat, max(0.0, peak * gain + jitter)))
    return out


def hull_for(points: list[tuple[float, float]]) -> list:
    """Rough buffered hull. The real system does this in PostGIS; this only
    needs to look right."""
    if not points:
        return []
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    pad = 0.0016
    return [[
        [min(lons) - pad, min(lats) - pad],
        [max(lons) + pad, min(lats) - pad],
        [max(lons) + pad, max(lats) + pad],
        [min(lons) - pad, max(lats) + pad],
        [min(lons) - pad, min(lats) - pad],
    ]]


def build_state() -> dict:
    rows = readings()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    sensors = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "sensor_id": sid, "name": name,
                "depth_cm": round(depth, 1), "observed_at": now,
                "zone_id": None,
            },
        } for sid, name, lon, lat, depth in rows],
    }

    wet = {r[0]: r for r in rows if r[4] >= THRESHOLDS["detect_cm"]}

    zone_features, advisory_rows = [], []
    for label, members in (("queens", QUEENS), ("redhook", REDHOOK)):
        present = [wet[s] for s in members if s in wet]
        if len(present) < 2:
            _zone_states.pop(label, None)
            continue

        zid = str(uuid.uuid5(uuid.NAMESPACE_DNS, label))
        depth = max(r[4] for r in present)
        prev = _zone_states.get(label)
        state = "forming" if prev is None else "active"
        _zone_states[label] = state

        # Only the Queens cluster sits under a warning polygon, which makes
        # the corroborated and uncorroborated cases both visible at once.
        under_alert = label == "queens" and depth >= 8

        zone_features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": hull_for([(r[2], r[3]) for r in present])},
            "properties": {
                "zone_id": zid,
                "sensor_ids": [r[0] for r in present],
                "sensor_count": len(present),
                "max_depth_cm": round(depth, 1),
                "state": state,
                "under_alert": under_alert,
                "alert_id": "urn:oid:mock.ffw" if under_alert else None,
                "opened_at": now, "updated_at": now,
            },
        })

        if state != "forming":
            level = ("warning" if depth >= 20 or (depth >= 12 and under_alert)
                     else "advisory" if depth >= 10 or (len(present) >= 3 and under_alert)
                     else "monitor")
            advisory_rows.append({
                "advisory_id": f"{zid}:{level}",
                "zone_id": zid, "level": level,
                "message": f"{level.upper()} for {label}",
                "issued_at": now, "audit_key": f"advisories/mock/{label}.json",
                "state": state, "sensor_count": len(present),
                "max_depth_cm": round(depth, 1), "under_alert": under_alert,
            })

    for row in advisory_rows:
        if not any(a["advisory_id"] == row["advisory_id"] for a in _advisories):
            _advisories.insert(0, row)
        else:
            for a in _advisories:
                if a["advisory_id"] == row["advisory_id"]:
                    a.update(row)
    del _advisories[40:]

    # Annotated because the inferred value type is the join of str and list,
    # which makes .append() below unresolvable. gate-check runs mypy scoped to
    # attribute existence, and an inference artifact in the mock is not worth
    # weakening that gate for.
    alerts: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    if any(f["properties"]["under_alert"] for f in zone_features):
        alerts["features"].append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [-73.82, 40.69], [-73.77, 40.69],
                [-73.77, 40.72], [-73.82, 40.72], [-73.82, 40.69],
            ]]},
            "properties": {
                "alert_id": "urn:oid:mock.ffw",
                "event": "Flash Flood Warning", "severity": "Severe",
                "headline": "Flash Flood Warning for southeast Queens",
                "expires": now,
            },
        })

    hits = int((time.time() - START) * 3)
    misses = max(1, len(SENSORS))
    return {
        "sensors": sensors,
        "zones": {"type": "FeatureCollection", "features": zone_features},
        "alerts": alerts,
        "advisories": _advisories,
        "counts": {
            "sensors": len(SENSORS),
            "readings": int((time.time() - START) / 3) * len(SENSORS),
            "open_zones": len(zone_features),
            "advisories": len(_advisories),
            "active_alerts": len(alerts["features"]),
        },
        "thresholds": THRESHOLDS,
        "pipeline": {
            "queues": {
                "ingest": {"waiting": random.randint(0, 6),
                           "in_flight": random.randint(0, 2)},
                "zones": {"waiting": random.randint(0, 2), "in_flight": 0},
                # Always zero here. The dead-letter indicator is meant to be
                # invisible in normal operation, and a mock that randomly
                # tripped it would teach the wrong thing about the real one.
                # Set CURBLINE_MOCK_DLQ=1 to exercise the visible state.
                "ingest-dlq": {"waiting": MOCK_DLQ, "in_flight": 0},
                "zones-dlq": {"waiting": 0, "in_flight": 0},
            },
            "cache": {
                "hits": hits, "misses": misses, "errors": 0,
                "hit_rate": round(hits / (hits + misses), 3),
                "reachable": True,
            },
            "database": True,
            "source": "mock",
        },
    }


clients: set[WebSocket] = set()


async def broadcaster() -> None:
    while True:
        payload = json.dumps(build_state())
        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                clients.discard(ws)
        await asyncio.sleep(2)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    task = asyncio.create_task(broadcaster())
    print(f"mock console on http://localhost:{PORT}  (storm cycle {CYCLE:.0f}s)")
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Curbline (mock)", lifespan=lifespan)


@app.get("/api/state")
async def state() -> JSONResponse:
    return JSONResponse(build_state())


@app.get("/api/health")
async def health() -> JSONResponse:
    # workers mirrors the real endpoint's shape. There are no worker processes
    # behind this server, so reporting them live would be a lie; None is the
    # "unknown" state the real one uses when it cannot get an answer.
    return JSONResponse({"status": "ok", "database": True, "cache": True,
                         "workers": {"collector": None, "correlator": None,
                                     "dispatcher": None},
                         "source": "mock"})


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        await websocket.send_text(json.dumps(build_state()))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
    except Exception:
        clients.discard(websocket)


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
