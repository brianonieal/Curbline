"""
Read-through cache over the slow-changing reference data.

The rule that matters: the system must stay correct when the cache is empty
or the cache is down. Every miss and every Redis failure degrades to a direct
Postgres read. The cache is a latency optimization, never a source of truth,
and nothing is ever written to Redis that does not already exist in Postgres.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import redis

from . import config, db

log = logging.getLogger(__name__)

_client: redis.Redis | None = None

# Unflushed deltas for THIS process, not a total. Incremented on the hot path,
# drained by flush_stats(). Reading this dict to answer "what is the hit rate"
# is the E-019 defect: cache.sensor() is called only by the correlator, so the
# API's copy is always zero and reported a plausible-looking wrong number.
# Anything outside this module wants read_stats().
STATS = {"hits": 0, "misses": 0, "errors": 0}

# Where the aggregate lives. Redis is the natural home: the counters describe
# the cache, every process already holds a connection, and INCRBY is atomic so
# concurrent workers cannot lose each other's counts.
_STATS_KEYS = {
    "hits": "stats:cache:hits",
    "misses": "stats:cache:misses",
    "errors": "stats:cache:errors",
}


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=config.CACHE_HOST,
            port=config.CACHE_PORT,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    return _client


def _get(key: str) -> str | None:
    try:
        value = client().get(key)
    except redis.RedisError as exc:
        STATS["errors"] += 1
        log.warning("cache read failed for %s, falling through to db: %s", key, exc)
        return None
    if value is None:
        STATS["misses"] += 1
    else:
        STATS["hits"] += 1
    return value


def _set(key: str, value: str, ttl: int) -> None:
    try:
        client().setex(key, ttl, value)
    except redis.RedisError as exc:
        STATS["errors"] += 1
        log.warning("cache write failed for %s, continuing: %s", key, exc)


def read_through(
    key: str,
    loader: Callable[[], Any],
    ttl: int | None = None,
) -> Any:
    """Return cached JSON for key, or load it, cache it, and return it."""
    raw = _get(key)
    if raw is not None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt entry. Drop it and reload rather than failing the request.
            log.warning("corrupt cache entry at %s, reloading", key)

    value = loader()
    if value is not None:
        _set(key, json.dumps(value, default=str),
             ttl if ttl is not None else config.CACHE_TTL_SECONDS)
    return value


def sensor(sensor_id: str) -> dict[str, Any] | None:
    """
    Sensor metadata is read on every single reading and changes when a sensor
    is redeployed, which is rare. That ratio is what justifies caching it,
    rather than caching something because the assignment asks for a cache.
    """
    return read_through(f"sensor:{sensor_id}", lambda: db.get_sensor(sensor_id))


def invalidate_sensor(sensor_id: str) -> None:
    try:
        client().delete(f"sensor:{sensor_id}")
    except redis.RedisError as exc:
        log.warning("cache invalidate failed for %s: %s", sensor_id, exc)


def flush_stats() -> None:
    """
    Publish this process's unflushed counts so another process can read them.

    Called from the worker loop between batches rather than from _get, because
    the hot path should not pay a network roundtrip to measure itself. The cost
    is that the dashboard trails real activity by up to one poll interval, which
    for a status bar is not a cost at all.

    On failure the deltas are kept. The flush target is the cache, so a failed
    flush usually means the cache is down, which is exactly when the error count
    is worth having. Dropping it here would under-report the incident that
    produced it.
    """
    pending = {k: v for k, v in STATS.items() if v}
    if not pending:
        return
    try:
        c = client()
        for name, delta in pending.items():
            c.incrby(_STATS_KEYS[name], delta)
    except redis.RedisError as exc:
        log.warning("stats flush failed, retaining %s for the next attempt: %s",
                    pending, exc)
        return
    for name in pending:
        STATS[name] -= pending[name]


def read_stats() -> dict[str, Any]:
    """
    The published aggregate across every process that has flushed.

    hit_rate is None rather than 0.0 when nothing has been recorded or the cache
    is unreachable. Those are unknowns, and a status bar showing 0% for an
    unknown is the same class of quiet wrongness as E-019 itself.
    """
    try:
        raw = client().mget([_STATS_KEYS["hits"],
                             _STATS_KEYS["misses"],
                             _STATS_KEYS["errors"]])
    except redis.RedisError:
        return {"hits": 0, "misses": 0, "errors": 0, "hit_rate": None}

    hits, misses, errors = (int(v) if v is not None else 0 for v in raw)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "errors": errors,
        "hit_rate": round(hits / total, 3) if total else None,
    }


# ---------------------------------------------------------------------------
# Worker liveness. Redis is the only store all four processes already share, so
# it carries the heartbeat as well as the counters above. This module is
# therefore the Redis-backed shared state layer, not only a read-through cache.
# ---------------------------------------------------------------------------

WORKERS = ("collector", "correlator", "dispatcher")

# Comfortably longer than a 20 second long poll plus a slow cycle, short enough
# that a stopped worker shows up while someone is still looking at the screen.
HEARTBEAT_TTL_SECONDS = 120


def beat(component: str) -> None:
    """
    Record that this component is still working.

    Never raises. A worker that cannot report liveness must keep doing its
    actual job; failing the pipeline to update a status indicator would make
    the indicator the most fragile part of the system.
    """
    try:
        client().setex(
            f"heartbeat:{component}",
            HEARTBEAT_TTL_SECONDS,
            datetime.now(timezone.utc).isoformat(),
        )
    except redis.RedisError as exc:
        log.warning("heartbeat write failed for %s: %s", component, exc)


def worker_liveness() -> dict[str, bool | None]:
    """
    True if the component beat recently, False if its key has expired, None if
    the answer is unavailable.

    None is a real third state and is not allowed to collapse into False. An
    unreachable Redis means the evidence is missing, not that three workers
    died, and reporting the second would send someone to debug the wrong thing.
    """
    try:
        raw = client().mget([f"heartbeat:{w}" for w in WORKERS])
    except redis.RedisError:
        return {w: None for w in WORKERS}
    return {w: v is not None for w, v in zip(WORKERS, raw)}


def healthy() -> bool:
    try:
        return bool(client().ping())
    except redis.RedisError:
        return False
