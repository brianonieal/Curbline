#!/usr/bin/env python3
"""
Component A: collector.

Polls the sensor source and the National Weather Service, normalizes both into
one message shape, and publishes to the ingest queue. Holds no application
state and touches neither the database nor the cache, so it can be restarted
at any moment without consequence.

    python3 -m workers.collector
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid

import requests

from curbline import aws, config
from curbline.sources import build_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [collector] %(message)s",
)
log = logging.getLogger(__name__)

NWS_ALERTS = "https://api.weather.gov/alerts/active"

# Products worth correlating against street flooding. Anything else is noise.
FLOOD_EVENTS = {
    "Flash Flood Warning",
    "Flash Flood Statement",
    "Flood Warning",
    "Flood Advisory",
    "Flood Watch",
    "Coastal Flood Warning",
    "Coastal Flood Advisory",
    "Coastal Flood Watch",
}


def poll_sensors(source) -> None:
    count = 0
    for reading in source.fetch():
        aws.send(config.QUEUE_INGEST, reading.to_message())
        count += 1
    log.info("published %d readings from source=%s", count, source.name)


def poll_alerts() -> None:
    """
    Fetch active NWS alerts for the study area.

    Two things bite here. First, api.weather.gov rejects requests without a
    User-Agent identifying the caller. Second, many alerts arrive with
    geometry: null because they are zone-based rather than storm-based; those
    are stored with a NULL polygon and cannot participate in spatial
    correlation. That is a real limitation to state in the report, not a bug
    to paper over.
    """
    resp = requests.get(
        NWS_ALERTS,
        params={"area": "NY", "status": "actual", "message_type": "alert"},
        headers={"User-Agent": config.NWS_USER_AGENT,
                 "Accept": "application/geo+json"},
        timeout=20,
    )
    resp.raise_for_status()

    published = without_geometry = 0
    for feature in resp.json().get("features", []):
        props = feature.get("properties", {}) or {}
        event = props.get("event")
        if event not in FLOOD_EVENTS:
            continue

        geometry = feature.get("geometry")
        if geometry is None:
            without_geometry += 1

        aws.send(config.QUEUE_INGEST, {
            "kind": "alert",
            "ingest_id": str(uuid.uuid4()),
            "alert_id": props.get("id") or feature.get("id"),
            "event": event,
            "severity": props.get("severity"),
            "headline": props.get("headline"),
            "effective": props.get("effective"),
            "expires": props.get("expires") or props.get("ends"),
            "geometry": geometry,
        })
        published += 1

    log.info("published %d flood alerts (%d had no polygon)",
             published, without_geometry)


def main() -> int:
    shutdown = aws.Shutdown()
    source = build_source()
    log.info("reading source: %s", source.name)

    # Sensors and alerts move at very different speeds, so they get separate
    # cadences rather than being forced onto one slow loop.
    alerts = threading.Thread(
        target=aws.poll_loop,
        args=(poll_alerts, config.ALERT_POLL_SECONDS, shutdown),
        daemon=True,
    )
    alerts.start()

    aws.poll_loop(
        lambda: poll_sensors(source), config.SENSOR_POLL_SECONDS, shutdown
    )
    alerts.join(timeout=5)
    log.info("collector stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
