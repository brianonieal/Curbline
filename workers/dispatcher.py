#!/usr/bin/env python3
"""
Component C: dispatcher.

Consumes candidate zones, runs the zone lifecycle state machine, decides
whether an advisory is warranted, writes the immutable audit record to S3,
publishes the advisory to SNS, and persists the result.

Ordering is deliberate: the audit record is written BEFORE the notification
goes out. If S3 fails, the handler raises, the message is not deleted, and
the unit is retried. No advisory is ever sent without a durable record of the
evidence behind it.

    python3 -m workers.dispatcher
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from curbline import aws, config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [dispatcher] %(message)s",
)
log = logging.getLogger(__name__)

# Advisory ladder. Depth alone is not enough: a confirmed NWS warning over the
# same footprint is corroborating evidence from an independent source, so it
# raises the level. Two independent signals agreeing is the whole argument for
# correlating them.
ADVISORY_LEVELS = ["monitor", "advisory", "warning"]


def decide_level(max_depth_cm: float, sensor_count: int,
                 under_alert: bool) -> str:
    if max_depth_cm >= 20 or (max_depth_cm >= 12 and under_alert):
        return "warning"
    if max_depth_cm >= 10 or (sensor_count >= 3 and under_alert):
        return "advisory"
    return "monitor"


def next_state(previous: str | None, sensor_count: int) -> str:
    """
    Zone lifecycle.

    A zone that appears for the first time is 'forming' rather than 'active'.
    That one-cycle delay is intentional: it suppresses single-cycle sensor
    noise from generating an advisory. The cost is roughly one poll interval
    of latency on a genuine event, which is an acceptable trade against
    crying wolf.
    """
    if previous is None:
        return "forming"
    if previous == "forming":
        return "active"
    if previous in ("active", "receding"):
        return "active" if sensor_count >= config.CLUSTER_MIN_SENSORS else "receding"
    return "active"


def build_message(zone_id: str, level: str, body: dict[str, Any],
                  state: str) -> str:
    depth_in = float(body["max_depth_cm"]) / 2.54
    lines = [
        f"CURBLINE {level.upper()} - street flooding zone {state}",
        "",
        f"Zone:          {zone_id}",
        f"Sensors wet:   {body['sensor_count']}",
        f"Max depth:     {body['max_depth_cm']:.1f} cm ({depth_in:.1f} in)",
        f"NWS alert:     {body.get('alert_id') or 'none matching this footprint'}",
        "",
        "Recommended action:",
    ]
    if level == "warning":
        lines.append("  Close affected roadway segments. Notify OEM liaison.")
    elif level == "advisory":
        lines.append("  Stage barricades. Advise avoiding the area.")
    else:
        lines.append("  Monitor. No field action required yet.")
    return "\n".join(lines)


def handle(body: dict[str, Any]) -> None:
    zone_id = body["zone_id"]
    sensor_ids = list(body["sensor_ids"])
    max_depth = float(body["max_depth_cm"])
    alert_id = body.get("alert_id")
    under_alert = alert_id is not None

    existing = {z["zone_id"]: z for z in db.open_zones()}
    previous = existing.get(zone_id)
    previous_state = previous["state"] if previous else None

    state = next_state(previous_state, body["sensor_count"])
    level = decide_level(max_depth, body["sensor_count"], under_alert)

    db.upsert_zone(
        zone_id=zone_id,
        hull_geojson=body["hull_geojson"],
        sensor_ids=sensor_ids,
        max_depth_cm=max_depth,
        state=state,
        alert_id=alert_id,
    )

    # A forming zone is persisted and drawn on the map but does not notify.
    # It is not yet corroborated across cycles.
    if state == "forming":
        log.info("zone %s forming, no advisory yet", zone_id)
        return

    # Do not re-notify at an unchanged level for an unchanged zone.
    if previous and previous["state"] == state and previous["under_alert"] == under_alert:
        log.info("zone %s unchanged (%s), suppressing duplicate advisory",
                 zone_id, state)
        return

    message = build_message(zone_id, level, body, state)

    # Audit first. If this raises, nothing is sent and the unit retries.
    audit_key = aws.write_audit(zone_id, {
        "zone_id": zone_id,
        "state": state,
        "level": level,
        "sensor_ids": sensor_ids,
        "max_depth_cm": max_depth,
        "alert_id": alert_id,
        "hull": body["hull_geojson"],
        "thresholds": {
            "depth_threshold_cm": config.DEPTH_THRESHOLD_CM,
            "cluster_eps_ft": config.CLUSTER_EPS_FT,
            "cluster_min_sensors": config.CLUSTER_MIN_SENSORS,
            "reading_window_mins": config.READING_WINDOW_MINS,
        },
        "message": message,
    })

    sns_id = aws.publish(f"Curbline {level}: zone {state}", {
        "zone_id": zone_id,
        "level": level,
        "state": state,
        "max_depth_cm": max_depth,
        "sensor_count": body["sensor_count"],
        "alert_id": alert_id,
        "audit_key": audit_key,
        "message": message,
    })

    advisory_id = db.record_advisory(
        zone_id=zone_id, level=level, message=message,
        sns_message_id=sns_id, audit_key=audit_key,
    )
    log.info("zone %s -> %s/%s advisory=%s audit=%s",
             zone_id, state, level, advisory_id, audit_key)


def main() -> int:
    shutdown = aws.Shutdown()
    log.info("consuming %s", config.QUEUE_ZONES)
    aws.consume(config.QUEUE_ZONES, handle, shutdown)
    log.info("dispatcher stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
