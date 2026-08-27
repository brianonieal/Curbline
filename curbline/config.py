"""Configuration, loaded from the environment. No secrets in source."""

from __future__ import annotations

import os
import pathlib


def _load_dotenv() -> None:
    """Read .env at the repo root if present. Real env vars always win."""
    path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _req(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Run infra/provision.py, or source .env."
        )
    return value


REGION = os.environ.get("CURBLINE_REGION", "us-east-1")

DB_HOST = _req("CURBLINE_DB_HOST")
DB_PORT = int(os.environ.get("CURBLINE_DB_PORT", "5432"))
DB_NAME = os.environ.get("CURBLINE_DB_NAME", "curbline")
DB_USER = os.environ.get("CURBLINE_DB_USER", "curbline")
DB_PASSWORD = _req("CURBLINE_DB_PASSWORD")

CACHE_HOST = _req("CURBLINE_CACHE_HOST")
CACHE_PORT = int(os.environ.get("CURBLINE_CACHE_PORT", "6379"))
CACHE_TTL_SECONDS = int(os.environ.get("CURBLINE_CACHE_TTL", "300"))

QUEUE_INGEST = _req("CURBLINE_QUEUE_INGEST")
QUEUE_ZONES = _req("CURBLINE_QUEUE_ZONES")
SNS_TOPIC = _req("CURBLINE_SNS_TOPIC")
AUDIT_BUCKET = _req("CURBLINE_AUDIT_BUCKET")

# Detection thresholds. Every one of these is a decision, so they live in one
# place and get named in the report rather than buried as literals in a query.
#
# THRESHOLDS ARE SOURCE-SPECIFIC. The defaults below are calibrated for
# FloodNet, which measures standing water depth on a street surface, where
# 5 cm is above sensor noise and 20 cm is most of a curb.
#
# The USGS fallback measures a river's rise above its own low-water baseline.
# That is a different physical quantity on a different scale: a 20 cm rise on
# the Passaic is an ordinary Tuesday, not an emergency. Sampling the NYC-area
# bounding box with FloodNet thresholds put 9 of 29 gauges at "warning" during
# normal conditions. Raise these substantially when running on USGS, and say
# in the report which source each screenshot came from.
#
#   floodnet : 5 / 10 / 20 cm      (street depth, defaults below)
#   usgs     : 60 / 90 / 120 cm    (stage rise; starting point, tune on your data)
DEPTH_THRESHOLD_CM = float(os.environ.get("CURBLINE_DEPTH_THRESHOLD_CM", "5.0"))
READING_WINDOW_MINS = int(os.environ.get("CURBLINE_READING_WINDOW_MINS", "15"))
CLUSTER_EPS_FT = float(os.environ.get("CURBLINE_CLUSTER_EPS_FT", "1640"))
CLUSTER_MIN_SENSORS = int(os.environ.get("CURBLINE_CLUSTER_MIN_SENSORS", "2"))

# Poll cadences, seconds.
SENSOR_POLL_SECONDS = int(os.environ.get("CURBLINE_SENSOR_POLL", "60"))
ALERT_POLL_SECONDS = int(os.environ.get("CURBLINE_ALERT_POLL", "300"))

# Which reading source the collector uses. floodnet | usgs | replay
SOURCE = os.environ.get("CURBLINE_SOURCE", "usgs")

# NWS requires a User-Agent that identifies the caller. Requests without one
# are rejected. Put a real contact address here.
NWS_USER_AGENT = os.environ.get(
    "CURBLINE_NWS_UA", "curbline-jhu-coursework (bonieal1@jh.edu)"
)

# Bounding box for the study area: NYC five boroughs, roughly.
BBOX = (-74.30, 40.47, -73.68, 40.93)  # min_lon, min_lat, max_lon, max_lat
