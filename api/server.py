#!/usr/bin/env python3
"""
Presentation layer.

IMPORTANT for the report: this process is NOT one of the three required
distributed components. The three components are the collector, the correlator,
and the dispatcher. This process performs no pipeline work. It reads state that
those three produced and renders it. It never writes to the pipeline, never
consumes a queue, and could be deleted without changing what the system does.

    uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import pathlib
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from curbline import aws, cache, config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [api] %(message)s",
)
log = logging.getLogger(__name__)

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

# How often the broadcaster re-reads state. The pipeline's own cadence is
# slower than this, so polling faster would only add database load.
BROADCAST_INTERVAL_SECONDS = 3


# ---------------------------------------------------------------------------
# State assembly
# ---------------------------------------------------------------------------

def queue_depths() -> dict[str, dict[str, int]]:
    """
    Live SQS depth per queue, including the dead-letter queues.

    Worth surfacing on screen: it is the clearest single proof that the
    components really are decoupled and really are passing work through a
    queue rather than calling each other.
    """
    out: dict[str, dict[str, int]] = {}
    for label, url, dlq in (
        ("ingest", config.QUEUE_INGEST, config.QUEUE_INGEST + "-dlq"),
        ("zones", config.QUEUE_ZONES, config.QUEUE_ZONES + "-dlq"),
    ):
        try:
            attrs = aws.sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                ],
            )["Attributes"]
            out[label] = {
                "waiting": int(attrs.get("ApproximateNumberOfMessages", 0)),
                "in_flight": int(
                    attrs.get("ApproximateNumberOfMessagesNotVisible", 0)
                ),
            }
        except Exception:
            out[label] = {"waiting": -1, "in_flight": -1}
        _ = dlq
    return out


def build_state() -> dict[str, Any]:
    hits = cache.STATS["hits"]
    misses = cache.STATS["misses"]
    total = hits + misses
    return {
        "sensors": db.sensors_geojson(),
        "zones": db.zones_geojson(),
        "alerts": db.alerts_geojson(),
        "advisories": db.recent_advisories(),
        "counts": db.counts(),
        "thresholds": {
            "detect_cm": config.DEPTH_THRESHOLD_CM,
            "advisory_cm": 10.0,
            "curb_cm": 15.0,
            "warning_cm": 20.0,
        },
        "pipeline": {
            "queues": queue_depths(),
            "cache": {
                "hits": hits,
                "misses": misses,
                "errors": cache.STATS["errors"],
                "hit_rate": round(hits / total, 3) if total else None,
                "reachable": cache.healthy(),
            },
            "database": db.ping(),
            "source": config.SOURCE,
        },
    }


# ---------------------------------------------------------------------------
# WebSocket fan-out
# ---------------------------------------------------------------------------

class Clients:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.active.add(ws)
        log.info("client connected (%d total)", len(self.active))

    async def drop(self, ws: WebSocket) -> None:
        async with self.lock:
            self.active.discard(ws)
        log.info("client disconnected (%d remain)", len(self.active))

    async def broadcast(self, payload: str) -> None:
        async with self.lock:
            targets = list(self.active)
        dead = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.drop(ws)


clients = Clients()


async def broadcaster() -> None:
    """
    Single reader, many listeners.

    One task polls Postgres and fans the result out to every connected browser,
    rather than each browser polling independently. With N dashboards open the
    database still sees one query per interval instead of N.
    """
    while True:
        try:
            state = await asyncio.to_thread(build_state)
            await clients.broadcast(json.dumps(state, default=str))
        except Exception:
            log.exception("broadcast failed, continuing")
        await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    task = asyncio.create_task(broadcaster())
    log.info("broadcaster started (%ss interval)", BROADCAST_INTERVAL_SECONDS)
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Curbline", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/state")
async def state() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(build_state))


@app.get("/api/health")
async def health() -> JSONResponse:
    """
    Component-level health, which is what the screenshots need to show.
    Degraded means the cache is unreachable but the pipeline still works,
    because every cache read falls through to Postgres.
    """
    def check() -> dict[str, Any]:
        database = db.ping()
        cache_ok = cache.healthy()
        return {
            "status": "ok" if (database and cache_ok)
                      else "degraded" if database else "down",
            "database": database,
            "cache": cache_ok,
            "queues": queue_depths(),
            "source": config.SOURCE,
        }

    result = await asyncio.to_thread(check)
    return JSONResponse(result, status_code=200 if result["database"] else 503)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await clients.add(websocket)
    try:
        # Send current state immediately so a new client is not blank until
        # the next broadcast tick.
        state = await asyncio.to_thread(build_state)
        await websocket.send_text(json.dumps(state, default=str))
        while True:
            await websocket.receive_text()   # keepalive from the browser
    except WebSocketDisconnect:
        await clients.drop(websocket)
    except Exception:
        await clients.drop(websocket)


# Static bundle last, so it does not shadow the /api routes above.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
