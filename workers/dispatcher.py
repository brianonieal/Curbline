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
import threading
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
    # Thresholds come from config, not literals, because they move with the
    # reading source (D-005). These were 20/12/10 inline, which silently meant
    # FloodNet street depth no matter what the collector was actually reading.
    if (max_depth_cm >= config.WARNING_THRESHOLD_CM
            or (max_depth_cm >= config.CORROBORATED_WARNING_CM and under_alert)):
        return "warning"
    if (max_depth_cm >= config.ADVISORY_THRESHOLD_CM
            or (sensor_count >= 3 and under_alert)):
        return "advisory"
    return "monitor"


def next_state(previous: str | None) -> str:
    """
    Zone lifecycle for a zone that is currently clustering.

    A zone that appears for the first time is 'forming' rather than 'active'.
    That one-cycle delay is intentional: it suppresses single-cycle sensor
    noise from generating an advisory. The cost is roughly one poll interval
    of latency on a genuine event, which is an acceptable trade against
    crying wolf.

    Every message on the zones queue describes a cluster that exists right now,
    so arriving here at all means the zone is still wet. There is no branch to
    recede on, and there used to be: `sensor_count < CLUSTER_MIN_SENSORS` can
    never hold, because current_clusters() is called with
    p_minpoints := CLUSTER_MIN_SENSORS and discards noise, so every row it
    returns already has at least that many members. Recession is the absence of
    a message, which is sweep_state's job. See E-020.
    """
    if previous is None:
        return "forming"
    return "active"


def sweep_state(previous: str) -> str | None:
    """
    The other half of the lifecycle: what a zone becomes when it stops being
    republished. Returns None for a zone that needs no transition.

    Two steps rather than one, mirroring the forming delay on the way in. A
    single missed cycle is a gap in the data, not the end of a flood.
    """
    if previous in ("forming", "active"):
        return "receding"
    if previous == "receding":
        return "closed"
    return None


def should_notify(previous: dict[str, Any] | None, state: str,
                  level: str, under_alert: bool) -> bool:
    """
    Whether this cycle's zone warrants an advisory.

    `level` is in the key, and its absence was E-021: a zone escalating from
    advisory to warning while staying active with unchanged corroboration
    compared equal to its previous cycle and was suppressed. The comment above
    the old check said "unchanged level" while the code never looked at one.

    A previous zone with last_level None has never issued an advisory. That is
    not equal to any real level and must not silence the first one.
    """
    if state == "forming":
        return False
    if previous is None:
        return True
    return not (
        previous["state"] == state
        and previous.get("last_level") == level
        and previous["under_alert"] == under_alert
    )


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

    # Key on str. zones.zone_id is a UUID column, so the database layer can
    # return uuid.UUID while the queue body always carries a string. UUID(x) is
    # never equal to str(x), so keying on the raw value finds nothing, every
    # zone looks new, next_state returns "forming" forever and no advisory is
    # ever sent. The SQL now casts, and this keeps the lookup correct even if a
    # future query forgets to. See E-017.
    existing = {str(z["zone_id"]): z for z in db.open_zones()}
    previous = existing.get(str(zone_id))
    previous_state = previous["state"] if previous else None

    state = next_state(previous_state)
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

    if not should_notify(previous, state, level, under_alert):
        log.info("zone %s unchanged (%s at %s), suppressing duplicate advisory",
                 zone_id, state, level)
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


def sweep_zones() -> None:
    """
    Retire zones the clustering has stopped finding.

    This has to run on a timer rather than per message. The case that matters
    is a flood ending: the clusters disappear, so no zone messages arrive, so a
    message-driven sweep would never fire on exactly the zones that need
    closing. That is why the dispatcher now has a cadence as well as a queue.
    """
    stale = db.stale_open_zones(config.ZONE_STALE_MINUTES)
    for zone in stale:
        nxt = sweep_state(zone["state"])
        if nxt is None:
            continue
        db.set_zone_state(zone["zone_id"], nxt)
        log.info("zone %s not reclustered for %d min: %s -> %s",
                 zone["zone_id"], config.ZONE_STALE_MINUTES,
                 zone["state"], nxt)


def main() -> int:
    shutdown = aws.Shutdown()

    # Same shape as the collector's alert thread: a second cadence that must
    # keep running even while the main loop is blocked on a long poll.
    sweeper = threading.Thread(
        target=aws.poll_loop,
        args=(sweep_zones, config.ZONE_SWEEP_SECONDS, shutdown),
        daemon=True,
    )
    sweeper.start()

    log.info("consuming %s", config.QUEUE_ZONES)
    aws.consume(config.QUEUE_ZONES, handle, shutdown)
    sweeper.join(timeout=5)
    log.info("dispatcher stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
