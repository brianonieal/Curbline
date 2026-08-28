#!/usr/bin/env python3
"""
Component B: correlator.

Consumes the ingest queue, persists readings and alerts to PostGIS, then runs
the spatial work that is the whole point of the application: cluster adjacent
inundated sensors into candidate zones, and test each zone against active
National Weather Service warning polygons.

Publishes candidate zones to the zones queue.

    python3 -m workers.correlator
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import uuid
from typing import Any

from psycopg.errors import ForeignKeyViolation

from curbline import aws, cache, config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [correlator] %(message)s",
)
log = logging.getLogger(__name__)

# Clustering is the expensive query. Running it on every single reading would
# hammer the database for no benefit, since a zone cannot meaningfully change
# between two readings seconds apart. Debounce it.
CLUSTER_MIN_INTERVAL_SECONDS = 20
_last_cluster_run = 0.0


def stable_zone_id(sensor_ids: list[str]) -> str:
    """
    Derive a zone's identity from its member sensors.

    A zone has no natural key. Using a UUID4 per detection would create a brand
    new zone every cycle and make lifecycle tracking impossible. Hashing the
    sorted member set means the same flooded block keeps the same zone_id
    across cycles, so state transitions and duration actually mean something.

    The tradeoff, and it is a real one: a zone that gains or loses one sensor
    becomes a different zone_id. For a coursework-scale sensor network that is
    acceptable. At production scale this wants spatial overlap matching against
    the previous cycle's hulls instead.
    """
    digest = hashlib.sha1("|".join(sorted(sensor_ids)).encode()).digest()
    return str(uuid.UUID(bytes=digest[:16]))


def _ensure_sensor(body: dict[str, Any]) -> None:
    """Write the sensor row and warm the cache entry that describes it."""
    db.upsert_sensor(body["sensor_id"], body["name"], body["lon"], body["lat"])
    cache.invalidate_sensor(body["sensor_id"])
    cache.sensor(body["sensor_id"])


def handle_reading(body: dict[str, Any]) -> None:
    sensor_id = body["sensor_id"]

    # Read-through cache. Sensor metadata is consulted on every reading and
    # changes almost never, which is what makes it worth caching.
    known = cache.sensor(sensor_id)
    if known is None:
        _ensure_sensor(body)

    try:
        claimed = db.claim_reading(
            ingest_id=body["ingest_id"],
            sensor_id=sensor_id,
            observed_at=body["observed_at"],
            depth_cm=body["depth_cm"],
            source=body["source"],
        )
    except ForeignKeyViolation:
        # The cache reported this sensor as known and Postgres disagrees. The
        # cache is a copy of the row, never proof the row exists, and the two
        # diverge for ordinary reasons: a restored database, a manual delete, a
        # cache that outlived the table it describes. Treating the cache as an
        # existence oracle turns that divergence into a message that fails on
        # every redelivery until it reaches the dead-letter queue. Repair the
        # row, then retry once. See E-016.
        log.warning("cache claimed sensor %s exists, database disagrees; "
                    "repairing", sensor_id)
        cache.invalidate_sensor(sensor_id)
        _ensure_sensor(body)
        claimed = db.claim_reading(
            ingest_id=body["ingest_id"],
            sensor_id=sensor_id,
            observed_at=body["observed_at"],
            depth_cm=body["depth_cm"],
            source=body["source"],
        )

    if not claimed:
        # Duplicate delivery. Expected under at-least-once, not an error.
        log.debug("duplicate reading %s skipped", body["ingest_id"])


def handle_alert(body: dict[str, Any]) -> None:
    db.upsert_alert(
        alert_id=body["alert_id"],
        event=body["event"],
        severity=body.get("severity"),
        headline=body.get("headline"),
        effective=body.get("effective"),
        expires=body.get("expires"),
        geometry=body.get("geometry"),
    )


def maybe_cluster() -> None:
    global _last_cluster_run
    now = time.monotonic()
    if now - _last_cluster_run < CLUSTER_MIN_INTERVAL_SECONDS:
        return
    _last_cluster_run = now

    clusters = db.current_clusters()
    if not clusters:
        log.info("no clusters above threshold")
        return

    for row in clusters:
        sensor_ids = list(row["sensor_ids"])
        zone_id = stable_zone_id(sensor_ids)
        aws.send(config.QUEUE_ZONES, {
            "zone_id": zone_id,
            "sensor_ids": sensor_ids,
            "sensor_count": row["sensor_count"],
            "max_depth_cm": float(row["max_depth_cm"]),
            "hull_geojson": row["hull_geojson"],
            "alert_id": row["alert_id"],
            "detected_at": time.time(),
        })

    log.info("published %d candidate zones (cache hits=%d misses=%d errors=%d)",
             len(clusters), cache.STATS["hits"],
             cache.STATS["misses"], cache.STATS["errors"])


def handle(body: dict[str, Any]) -> None:
    kind = body.get("kind")
    if kind == "reading":
        handle_reading(body)
    elif kind == "alert":
        handle_alert(body)
    else:
        # Unknown message shape. Raising would retry it five times and then
        # dead-letter it, which is the right outcome for a genuine bug but
        # noisy for a stray message. Log and drop.
        log.warning("unknown message kind %r, dropping", kind)
        return

    maybe_cluster()


def main() -> int:
    shutdown = aws.Shutdown()
    log.info("consuming %s", config.QUEUE_INGEST)
    aws.consume(config.QUEUE_INGEST, handle, shutdown)
    log.info("correlator stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
