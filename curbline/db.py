"""Postgres access. Connection pooling, and the spatial queries in one place."""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=(
                f"host={config.DB_HOST} port={config.DB_PORT} "
                f"dbname={config.DB_NAME} user={config.DB_USER} "
                f"password={config.DB_PASSWORD} connect_timeout=10"
            ),
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextlib.contextmanager
def cursor() -> Iterator[psycopg.Cursor]:
    with pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def upsert_sensor(sensor_id: str, name: str, lon: float, lat: float) -> None:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensors (sensor_id, name, geom)
            VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT (sensor_id) DO UPDATE
              SET name = EXCLUDED.name,
                  geom = EXCLUDED.geom,
                  updated_at = now()
            """,
            (sensor_id, name, lon, lat),
        )


def get_sensor(sensor_id: str) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute(
            """
            SELECT sensor_id, name,
                   ST_X(geom) AS lon, ST_Y(geom) AS lat
            FROM sensors WHERE sensor_id = %s
            """,
            (sensor_id,),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Readings. claim_reading is the idempotency gate.
# ---------------------------------------------------------------------------

def claim_reading(
    ingest_id: str, sensor_id: str, observed_at: str,
    depth_cm: float, source: str,
) -> bool:
    """
    Insert a reading, returning True if this call won the race and False if the
    unit was already processed. SQS delivers at least once, so every consuming
    stage must be able to see the same message twice without double-acting.
    A duplicate is a skip, not an error.
    """
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO readings
                (ingest_id, sensor_id, observed_at, depth_cm, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (ingest_id) DO NOTHING
            """,
            (ingest_id, sensor_id, observed_at, depth_cm, source),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def upsert_alert(
    alert_id: str, event: str, severity: str | None, headline: str | None,
    effective: str | None, expires: str | None, geometry: dict | None,
) -> None:
    """
    geometry may be None. NWS zone-based products carry no polygon and instead
    reference UGC zones. Storing NULL is correct; those alerts simply cannot
    participate in spatial correlation.
    """
    geojson = json.dumps(geometry) if geometry else None
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts
                (alert_id, event, severity, headline, effective, expires, geom)
            VALUES (%s, %s, %s, %s, %s, %s,
                    CASE WHEN %s IS NULL THEN NULL
                         ELSE ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                    END)
            ON CONFLICT (alert_id) DO UPDATE
              SET expires = EXCLUDED.expires,
                  severity = EXCLUDED.severity,
                  geom = EXCLUDED.geom,
                  updated_at = now()
            """,
            (alert_id, event, severity, headline, effective, expires,
             geojson, geojson),
        )


# ---------------------------------------------------------------------------
# The spatial core
# ---------------------------------------------------------------------------

def current_clusters() -> list[dict[str, Any]]:
    """Run DBSCAN over currently-inundated sensors and return zone candidates."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT cluster_id, sensor_ids, sensor_count, max_depth_cm,
                   ST_AsGeoJSON(hull) AS hull_geojson,
                   alert_for_hull(hull) AS alert_id
            FROM current_clusters(%s, %s, %s, %s)
            """,
            (config.DEPTH_THRESHOLD_CM, config.READING_WINDOW_MINS,
             config.CLUSTER_EPS_FT, config.CLUSTER_MIN_SENSORS),
        )
        return cur.fetchall()


def open_zones() -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT zone_id, sensor_ids, sensor_count, max_depth_cm, state,
                   under_alert, alert_id, opened_at, updated_at,
                   ST_AsGeoJSON(hull) AS hull_geojson
            FROM zones WHERE state <> 'closed'
            """
        )
        return cur.fetchall()


def upsert_zone(
    zone_id: str, hull_geojson: str, sensor_ids: list[str],
    max_depth_cm: float, state: str, alert_id: str | None,
) -> None:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO zones (zone_id, hull, sensor_ids, sensor_count,
                               max_depth_cm, state, under_alert, alert_id,
                               opened_at, updated_at)
            VALUES (%s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (zone_id) DO UPDATE
              SET hull = EXCLUDED.hull,
                  sensor_ids = EXCLUDED.sensor_ids,
                  sensor_count = EXCLUDED.sensor_count,
                  max_depth_cm = EXCLUDED.max_depth_cm,
                  state = EXCLUDED.state,
                  under_alert = EXCLUDED.under_alert,
                  alert_id = EXCLUDED.alert_id,
                  updated_at = now(),
                  closed_at = CASE WHEN EXCLUDED.state = 'closed'
                                   THEN now() ELSE NULL END
            """,
            (zone_id, hull_geojson, sensor_ids, len(sensor_ids),
             max_depth_cm, state, alert_id is not None, alert_id),
        )


def record_advisory(
    zone_id: str, level: str, message: str,
    sns_message_id: str | None, audit_key: str | None,
) -> str:
    advisory_id = str(uuid.uuid4())
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO advisories
                (advisory_id, zone_id, level, message, sns_message_id, audit_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (advisory_id, zone_id, level, message, sns_message_id, audit_key),
        )
    return advisory_id


# ---------------------------------------------------------------------------
# Read models for the presentation layer.
# These are the only queries the API process runs. It never writes.
# ---------------------------------------------------------------------------

def sensors_geojson() -> dict[str, Any]:
    """Every known sensor with its most recent reading, as a FeatureCollection."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT s.sensor_id, s.name,
                   ST_AsGeoJSON(s.geom)::json AS geometry,
                   lr.depth_cm, lr.observed_at,
                   z.zone_id
            FROM sensors s
            LEFT JOIN latest_readings lr USING (sensor_id)
            LEFT JOIN LATERAL (
                SELECT zone_id FROM zones
                WHERE state <> 'closed' AND s.sensor_id = ANY(sensor_ids)
                LIMIT 1
            ) z ON TRUE
            """
        )
        rows = cur.fetchall()

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "sensor_id": r["sensor_id"],
                "name": r["name"],
                "depth_cm": float(r["depth_cm"]) if r["depth_cm"] is not None else None,
                "observed_at": r["observed_at"].isoformat() if r["observed_at"] else None,
                "zone_id": r["zone_id"],
            },
        } for r in rows],
    }


def zones_geojson() -> dict[str, Any]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT zone_id, ST_AsGeoJSON(hull)::json AS geometry,
                   sensor_ids, sensor_count, max_depth_cm, state,
                   under_alert, alert_id, opened_at, updated_at
            FROM zones WHERE state <> 'closed'
            ORDER BY max_depth_cm DESC
            """
        )
        rows = cur.fetchall()

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "zone_id": str(r["zone_id"]),
                "sensor_ids": list(r["sensor_ids"]),
                "sensor_count": r["sensor_count"],
                "max_depth_cm": float(r["max_depth_cm"]),
                "state": r["state"],
                "under_alert": r["under_alert"],
                "alert_id": r["alert_id"],
                "opened_at": r["opened_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
            },
        } for r in rows],
    }


def alerts_geojson() -> dict[str, Any]:
    """Active alerts that carry a polygon. Zone-based products are excluded
    here because they have no geometry to draw."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT alert_id, event, severity, headline, expires,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM alerts
            WHERE geom IS NOT NULL AND expires > now()
            """
        )
        rows = cur.fetchall()

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "alert_id": r["alert_id"],
                "event": r["event"],
                "severity": r["severity"],
                "headline": r["headline"],
                "expires": r["expires"].isoformat() if r["expires"] else None,
            },
        } for r in rows],
    }


def recent_advisories(limit: int = 40) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT a.advisory_id, a.zone_id, a.level, a.message, a.issued_at,
                   a.audit_key, z.state, z.sensor_count, z.max_depth_cm,
                   z.under_alert
            FROM advisories a
            JOIN zones z USING (zone_id)
            ORDER BY a.issued_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [{
            "advisory_id": str(r["advisory_id"]),
            "zone_id": str(r["zone_id"]),
            "level": r["level"],
            "message": r["message"],
            "issued_at": r["issued_at"].isoformat(),
            "audit_key": r["audit_key"],
            "state": r["state"],
            "sensor_count": r["sensor_count"],
            "max_depth_cm": float(r["max_depth_cm"]),
            "under_alert": r["under_alert"],
        } for r in cur.fetchall()]


def counts() -> dict[str, int]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM sensors)                        AS sensors,
              (SELECT count(*) FROM readings)                       AS readings,
              (SELECT count(*) FROM zones WHERE state <> 'closed')  AS open_zones,
              (SELECT count(*) FROM advisories)                     AS advisories,
              (SELECT count(*) FROM alerts WHERE expires > now())   AS active_alerts
            """
        )
        return {k: int(v) for k, v in cur.fetchone().items()}


def ping() -> bool:
    try:
        with cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
    except Exception:
        return False
