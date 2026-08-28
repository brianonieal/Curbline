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

# Which reading source the collector uses. floodnet | usgs | replay
# Declared before the thresholds because they are derived from it.
SOURCE = os.environ.get("CURBLINE_SOURCE", "usgs")

# Detection thresholds, centimetres. Every one is a decision, so they live in
# one place and get named in the report rather than buried as literals.
#
# THRESHOLDS ARE SOURCE-SPECIFIC (D-005). FloodNet measures standing water on a
# street surface, where 5 cm is above sensor noise and 20 cm is most of a curb.
# USGS measures a river rising above its own low-water baseline, a different
# physical quantity on a different scale: a 20 cm rise on the Passaic is an
# ordinary Tuesday. Sampling the NYC bounding box with FloodNet numbers put 9
# of 29 gauges at "warning" in normal conditions.
#
# These were previously FloodNet literals while SOURCE defaulted to usgs, so
# the shipped defaults contradicted D-005 and the first live run reported 17
# of 28 sensors wet with a deepest reading of 151.8 cm of stage rise. Deriving
# them from SOURCE is what makes the logged decision actually hold.
#
#   replay reuses the FloodNet numbers: data/replay.example.json carries street
#   depths of 1 to 3 cm, so it is FloodNet-shaped regardless of capture origin.
_THRESHOLDS = {
    #            detect  advisory  warning
    "floodnet": (   5.0,     10.0,    20.0),
    "usgs":     (  60.0,     90.0,   120.0),
    "replay":   (   5.0,     10.0,    20.0),
}


def thresholds_for(source: str) -> tuple[float, float, float]:
    """Detection, advisory and warning thresholds in cm for a reading source.

    An unknown source falls back to FloodNet, the more sensitive calibration.
    Over-reporting on an unrecognised source is the safe direction; silently
    applying river-scale thresholds to street data would suppress real water.
    """
    return _THRESHOLDS.get(source, _THRESHOLDS["floodnet"])


_detect, _advisory, _warning = thresholds_for(SOURCE)

DEPTH_THRESHOLD_CM = float(os.environ.get("CURBLINE_DEPTH_THRESHOLD_CM", _detect))
ADVISORY_THRESHOLD_CM = float(
    os.environ.get("CURBLINE_ADVISORY_THRESHOLD_CM", _advisory))
WARNING_THRESHOLD_CM = float(
    os.environ.get("CURBLINE_WARNING_THRESHOLD_CM", _warning))

# The bar for "warning" when an NWS polygon corroborates the same footprint.
# Expressed as advisory plus a fifth of the advisory-to-warning gap, which
# reproduces the 12.0 the dispatcher hardcoded for FloodNet exactly, so no
# FloodNet behaviour changes. The ratio is fitted to the existing decision
# rather than chosen freely.
CORROBORATED_WARNING_CM = float(os.environ.get(
    "CURBLINE_CORROBORATED_WARNING_CM",
    ADVISORY_THRESHOLD_CM + 0.2 * (WARNING_THRESHOLD_CM - ADVISORY_THRESHOLD_CM),
))
READING_WINDOW_MINS = int(os.environ.get("CURBLINE_READING_WINDOW_MINS", "15"))
CLUSTER_EPS_FT = float(os.environ.get("CURBLINE_CLUSTER_EPS_FT", "1640"))
CLUSTER_MIN_SENSORS = int(os.environ.get("CURBLINE_CLUSTER_MIN_SENSORS", "2"))

# Poll cadences, seconds.
SENSOR_POLL_SECONDS = int(os.environ.get("CURBLINE_SENSOR_POLL", "60"))
ALERT_POLL_SECONDS = int(os.environ.get("CURBLINE_ALERT_POLL", "300"))


# NWS requires a User-Agent that identifies the caller. Requests without one
# are rejected. Put a real contact address here.
NWS_USER_AGENT = os.environ.get(
    "CURBLINE_NWS_UA", "curbline-jhu-coursework (bonieal1@jh.edu)"
)

# Bounding box for the study area: NYC five boroughs, roughly.
BBOX = (-74.30, 40.47, -73.68, 40.93)  # min_lon, min_lat, max_lon, max_lat
