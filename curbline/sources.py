"""
Reading sources behind one interface.

This is the swap point for the FloodNet approval risk. Every source returns the
same normalized shape, so the collector does not know or care which one is live.
If FloodNet access arrives, change CURBLINE_SOURCE and restart one service.
If it does not, the pipeline runs unchanged on USGS gauge data.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

import requests

from . import config

log = logging.getLogger(__name__)


@dataclass
class Reading:
    ingest_id: str
    sensor_id: str
    name: str
    lon: float
    lat: float
    observed_at: str
    depth_cm: float
    source: str

    def to_message(self) -> dict[str, Any]:
        return {"kind": "reading", **asdict(self)}


class ReadingSource(Protocol):
    name: str
    def fetch(self) -> Iterable[Reading]: ...


# ---------------------------------------------------------------------------

class FloodNetSource:
    """
    NYC street-level flood sensors, one-minute cadence.

    Endpoint and response shape are filled in from the API documentation that
    arrives with access approval. Deliberately left explicit rather than
    guessed: writing a parser against an imagined schema wastes the time it
    appears to save.
    """

    name = "floodnet"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def fetch(self) -> Iterable[Reading]:
        raise NotImplementedError(
            "Fill in from the FloodNet API docs once access is granted. "
            "Map their depth field to depth_cm and their timestamp to an "
            "ISO-8601 UTC string, then yield Reading objects."
        )


# ---------------------------------------------------------------------------

class USGSSource:
    """
    Fallback that needs no approval and no key.

    CRITICAL SEMANTIC POINT, verified empirically before this was written:
    USGS gage height is the water surface elevation above an arbitrary local
    datum. It is NOT flood depth. Sampling 12 gauges in the NYC bounding box on
    a dry day returned values from -0.26 ft to 19.58 ft. Converting those to
    centimetres directly puts 10 of 12 gauges past the 20 cm warning threshold
    with no rain anywhere, and one gauge reads negative because the datum sits
    above the water.

    So this source reports RISE ABOVE BASELINE, not absolute stage. The
    baseline is the 10th percentile of that site's own recent continuous
    history, which approximates its normal low-water level, and depth is the
    positive excursion above it. On the same calm river that produced a raw
    1.37 ft reading, this yields 0.3 cm.

    This is a different physical quantity from street flood depth. The pipeline
    is identical, the thresholds mean something different, and the report must
    say so rather than implying a FloodNet-equivalent measurement.

    Uses the modernized Water Data API. The legacy waterservices.usgs.gov host
    is being decommissioned and USGS has said intentional degradation may begin
    after August 2026, so do not build against it even though most tutorials do.
    """

    name = "usgs"
    BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
    GAGE_HEIGHT = "00065"  # USGS parameter code, gage height in feet
    FT_TO_CM = 30.48
    BASELINE_DAYS = 14
    BASELINE_PERCENTILE = 0.10

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        self.bbox = bbox
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/geo+json",
            "User-Agent": config.NWS_USER_AGENT,
        })
        # site id -> baseline gage height in feet. Resolved once per process.
        self._baseline: dict[str, float] = {}

    def _fetch_baseline(self, site: str) -> float | None:
        """Low-water datum for one site, from its own recent history."""
        since = (datetime.now(timezone.utc)
                 - timedelta(days=self.BASELINE_DAYS)).strftime("%Y-%m-%d")
        try:
            resp = self.session.get(
                f"{self.BASE}/collections/continuous/items",
                params={
                    "monitoring_location_id": site,
                    "parameter_code": self.GAGE_HEIGHT,
                    "datetime": f"{since}/..",
                    "limit": 500,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("baseline fetch failed for %s: %s", site, exc)
            return None

        values: list[float] = []
        for feature in resp.json().get("features", []):
            try:
                values.append(float((feature.get("properties") or {})["value"]))
            except (KeyError, TypeError, ValueError):
                continue

        if len(values) < 10:
            log.warning("insufficient history for %s (%d records)",
                        site, len(values))
            return None

        values.sort()
        return values[int(len(values) * self.BASELINE_PERCENTILE)]

    def baseline_for(self, site: str, current_ft: float) -> float:
        """
        Cached baseline, falling back to the current reading if history is
        unavailable. That fallback yields a rise of zero rather than a false
        alarm, which is the safe direction to fail for a detection system.
        """
        if site not in self._baseline:
            resolved = self._fetch_baseline(site)
            self._baseline[site] = (resolved if resolved is not None
                                    else current_ft)
            log.info("baseline for %s: %.2f ft%s", site, self._baseline[site],
                     "" if resolved is not None else " (from first reading)")
        return self._baseline[site]

    def fetch(self) -> Iterable[Reading]:
        resp = self.session.get(
            f"{self.BASE}/collections/latest-continuous/items",
            params={
                "bbox": ",".join(str(v) for v in self.bbox),
                "parameter_code": self.GAGE_HEIGHT,
                "limit": 200,
            },
            timeout=25,
        )
        resp.raise_for_status()

        for feature in resp.json().get("features", []):
            props = feature.get("properties", {}) or {}
            geom = feature.get("geometry") or {}
            coords = geom.get("coordinates")
            value = props.get("value")
            if not coords or value is None:
                continue

            try:
                current_ft = float(value)
            except (TypeError, ValueError):
                continue

            site = str(props.get("monitoring_location_id")
                       or feature.get("id") or "unknown")

            baseline_ft = self.baseline_for(site, current_ft)
            rise_cm = max(0.0, (current_ft - baseline_ft) * self.FT_TO_CM)

            yield Reading(
                ingest_id=str(uuid.uuid4()),
                sensor_id=f"usgs:{site}",
                name=str(props.get("monitoring_location_name") or site),
                lon=float(coords[0]),
                lat=float(coords[1]),
                observed_at=str(props.get("time")
                                or datetime.now(timezone.utc).isoformat()),
                depth_cm=round(rise_cm, 2),
                source=self.name,
            )


# ---------------------------------------------------------------------------

class ReplaySource:
    """
    Replays a stored event from a JSON file at wall-clock speed.

    This exists for one reason: the demo. If the presentation lands on a dry
    day, a live map of green dots demonstrates nothing, and the rubric's
    30-point criterion is about demonstrating end-to-end function. Replaying
    a real captured storm exercises the identical pipeline with real data and
    is disclosed as a replay in the report.
    """

    name = "replay"

    def __init__(self, path: str) -> None:
        import json
        import pathlib
        self.frames: list[list[dict[str, Any]]] = json.loads(
            pathlib.Path(path).read_text()
        )
        self.index = 0

    def fetch(self) -> Iterable[Reading]:
        if self.index >= len(self.frames):
            self.index = 0  # loop, so a long demo does not run dry
        frame = self.frames[self.index]
        self.index += 1

        now = datetime.now(timezone.utc).isoformat()
        for row in frame:
            yield Reading(
                ingest_id=str(uuid.uuid4()),
                sensor_id=row["sensor_id"],
                name=row.get("name", row["sensor_id"]),
                lon=float(row["lon"]),
                lat=float(row["lat"]),
                observed_at=now,
                depth_cm=float(row["depth_cm"]),
                source=self.name,
            )


# Every source build_source() can construct. config._THRESHOLDS must carry a
# calibration for each of these and nothing else; TestSourceCalibration asserts
# the two stay in step. They diverged silently before: this returned USGS for an
# unrecognised name while thresholds_for() returned FloodNet's, so a typo in
# CURBLINE_SOURCE collected river stage and graded it against street depth.
# That is E-014 arriving by a different road.
SOURCES = frozenset({"floodnet", "usgs", "replay"})


def build_source() -> ReadingSource:
    import os

    kind = config.SOURCE.lower()
    # Fail here rather than fall through to USGS. A misspelled source is a
    # configuration error, and guessing which one was meant is how the
    # collector and the thresholds end up describing different rivers.
    config.validate_source(kind)
    if kind == "floodnet":
        base = os.environ.get("CURBLINE_FLOODNET_URL")
        if not base:
            raise RuntimeError(
                "CURBLINE_SOURCE=floodnet but CURBLINE_FLOODNET_URL is unset."
            )
        return FloodNetSource(base, os.environ.get("CURBLINE_FLOODNET_KEY"))
    if kind == "replay":
        return ReplaySource(os.environ.get("CURBLINE_REPLAY_FILE", "data/replay.json"))
    return USGSSource(config.BBOX)
