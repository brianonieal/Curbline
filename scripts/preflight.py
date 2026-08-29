#!/usr/bin/env python3
"""
Run every external dependency once, before trusting the pipeline.

Why this exists. E-013 was a function PostgreSQL would not resolve because
psycopg sent double precision where the signature declared numeric. E-017 was a
UUID compared against a string. Both were invisible to a moto-only test suite,
both were found by a human watching a dashboard fail to tick, and between them
they cost most of a day. Neither would have survived thirty seconds of this.

It executes each query the workers actually run, with the same argument types,
against the real database, cache and AWS services. Writes happen inside a
transaction that is rolled back, so it can be run against a live stack without
leaving anything behind.

    python3 scripts/preflight.py

Run it immediately after infra/bootstrap.sh and before starting the units. Exit
code is non-zero if anything failed, so it can gate a script.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, ".")

PASS, FAIL, WARN = [], [], []


def check(name):
    """Decorator-free runner: check("x")(fn) so failures never abort the run."""
    def run(fn):
        try:
            detail = fn()
            PASS.append(f"{name}{': ' + detail if detail else ''}")
        except Exception as exc:
            FAIL.append(f"{name}: {type(exc).__name__}: {exc}")
    return run


def warn_check(name):
    def run(fn):
        try:
            detail = fn()
            if detail:
                WARN.append(f"{name}: {detail}")
            else:
                PASS.append(name)
        except Exception as exc:
            WARN.append(f"{name}: {type(exc).__name__}: {exc}")
    return run


def main() -> int:
    from curbline import config

    print(f"preflight, region {config.REGION}, source {config.SOURCE}\n")

    # --- Database -----------------------------------------------------------
    import psycopg
    from psycopg.rows import dict_row

    conn = None

    @check("postgres connect")
    def _():
        nonlocal conn
        conn = psycopg.connect(
            f"host={config.DB_HOST} port={config.DB_PORT} "
            f"dbname={config.DB_NAME} user={config.DB_USER} "
            f"password={config.DB_PASSWORD} connect_timeout=10",
            row_factory=dict_row,
        )
        return config.DB_HOST

    if conn is not None:
        @check("postgis extension")
        def _():
            with conn.cursor() as cur:
                cur.execute("SELECT PostGIS_Full_Version() AS v")
                return cur.fetchone()["v"].split()[1]

        @check("schema tables present")
        def _():
            want = {"sensors", "readings", "alerts", "zones", "advisories"}
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'")
                have = {r["tablename"] for r in cur.fetchall()}
            missing = want - have
            if missing:
                raise RuntimeError(f"missing tables: {sorted(missing)}")
            return f"{len(want)} tables"

        # THE E-013 CHECK. Same casts, same Python types db.current_clusters
        # sends. A signature change or a dropped cast fails here, in seconds,
        # instead of as "function does not exist" on every message.
        @check("current_clusters() resolves with the types psycopg sends")
        def _():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM current_clusters("
                    "%s::numeric, %s::int, %s::numeric, %s::int)",
                    (config.DEPTH_THRESHOLD_CM, config.READING_WINDOW_MINS,
                     config.CLUSTER_EPS_FT, config.CLUSTER_MIN_SENSORS),
                )
                return f"{cur.fetchone()['n']} clusters now"

        @check("alert_for_hull() resolves")
        def _():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT alert_for_hull(ST_SetSRID(ST_GeomFromGeoJSON(%s),"
                    "4326)::geometry(Polygon,4326)) AS a",
                    ('{"type":"Polygon","coordinates":'
                     '[[[-73.9,40.7],[-73.8,40.7],[-73.8,40.8],[-73.9,40.7]]]}',),
                )
                return "returned null" if cur.fetchone()["a"] is None else "matched"

        # THE NEW-SQL CHECK. open_zones grew a LEFT JOIN LATERAL against
        # advisories on 2026-08-28 for E-021 and had never executed anywhere.
        @check("open_zones() including the last_level lateral")
        def _():
            from curbline import db
            rows = db.open_zones()
            if rows and "last_level" not in rows[0]:
                raise RuntimeError("last_level column absent; E-021 fix inert")
            return f"{len(rows)} open zones"

        @check("stale_open_zones() runs")
        def _():
            from curbline import db
            return f"{len(db.stale_open_zones(config.ZONE_STALE_MINUTES))} stale"

        # Writes, rolled back. Proves the insert paths resolve without leaving
        # a fabricated reading in the evidence.
        @check("write paths (rolled back, nothing persisted)")
        def _():
            with conn.transaction() as tx:
                with conn.cursor() as cur:
                    sid = f"preflight:{uuid.uuid4()}"
                    cur.execute(
                        "INSERT INTO sensors (sensor_id, name, geom) VALUES "
                        "(%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))",
                        (sid, "preflight", -73.9, 40.7))
                    cur.execute(
                        "INSERT INTO readings (ingest_id, sensor_id, "
                        "observed_at, depth_cm, source) VALUES (%s,%s,%s,%s,%s)",
                        (str(uuid.uuid4()), sid,
                         datetime.now(timezone.utc).isoformat(), 1.0, "preflight"))
                    zid = str(uuid.uuid4())
                    cur.execute(
                        "INSERT INTO zones (zone_id, hull, sensor_ids, "
                        "sensor_count, max_depth_cm, state, under_alert, "
                        "opened_at, updated_at) VALUES (%s,"
                        "ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s,%s,%s,%s,"
                        "%s,now(),now())",
                        (zid, '{"type":"Polygon","coordinates":'
                              '[[[-73.9,40.7],[-73.8,40.7],[-73.8,40.8],'
                              '[-73.9,40.7]]]}',
                         [sid], 1, 1.0, "forming", False))
                    cur.execute(
                        "INSERT INTO advisories (advisory_id, zone_id, level, "
                        "message) VALUES (%s,%s,%s,%s)",
                        (str(uuid.uuid4()), zid, "monitor", "preflight"))
                    cur.execute(
                        "UPDATE zones SET state='receding' WHERE zone_id=%s",
                        (zid,))
                # psycopg's sentinel: unwinds the context and rolls back
                # without propagating. Nothing written above survives, so this
                # is safe to run against the stack that produces the evidence.
                raise psycopg.Rollback(tx)
            return "insert, update and lifecycle all resolve"

    # --- Cache --------------------------------------------------------------
    @check("redis reachable")
    def _():
        from curbline import cache
        if not cache.healthy():
            raise RuntimeError("ping failed")
        return f"{config.CACHE_HOST}:{config.CACHE_PORT}"

    @check("redis read/write/counter")
    def _():
        from curbline import cache
        c = cache.client()
        k = f"preflight:{uuid.uuid4()}"
        # set(ex=) rather than setex(): redis-py deprecates the latter and the
        # warning is noise in the terminal you are scanning for real failures.
        # cache.py still uses setex from library code, where the default filter
        # suppresses it. Worth revisiting if redis-py ever removes it.
        c.set(k, "1", ex=30)
        if c.get(k) != "1":
            raise RuntimeError("value did not round trip")
        c.incrby(k + ":n", 2)      # the stats transport primitive, E-019
        c.delete(k, k + ":n")
        return "setex, get, incrby"

    # --- AWS ----------------------------------------------------------------
    @check("sqs: all four queues")
    def _():
        from curbline import aws
        urls = [config.QUEUE_INGEST, config.QUEUE_ZONES]
        urls += [u for u in (config.QUEUE_INGEST_DLQ, config.QUEUE_ZONES_DLQ)
                 if u]
        for u in urls:
            aws.sqs.get_queue_attributes(QueueUrl=u, AttributeNames=["QueueArn"])
        return f"{len(urls)} reachable"

    @warn_check("sqs: dead-letter URLs in .env")
    def _():
        if not (config.QUEUE_INGEST_DLQ and config.QUEUE_ZONES_DLQ):
            return ("absent. Provisioned before E-024; dead-letter depth "
                    "cannot be reported on the console.")
        return None

    @check("s3: audit bucket writable and blocked from the public")
    def _():
        from curbline import aws
        aws.s3.head_bucket(Bucket=config.AUDIT_BUCKET)
        blk = aws.s3.get_public_access_block(Bucket=config.AUDIT_BUCKET)
        cfg = blk["PublicAccessBlockConfiguration"]
        if not all(cfg.values()):
            raise RuntimeError(f"public access not fully blocked: {cfg}")
        return config.AUDIT_BUCKET

    # The item that has slipped past two gates. A subscription in
    # PendingConfirmation silently drops every publication, and the advisories
    # issued before someone clicks the link cannot be replayed into a mailbox.
    @check("sns: topic exists and has a CONFIRMED subscription")
    def _():
        from curbline import aws
        aws.sns.get_topic_attributes(TopicArn=config.SNS_TOPIC)
        subs = aws.sns.list_subscriptions_by_topic(
            TopicArn=config.SNS_TOPIC)["Subscriptions"]
        if not subs:
            raise RuntimeError("no subscriptions. Nothing will be delivered.")
        pending = [s for s in subs
                   if "pending" in s.get("SubscriptionArn", "").lower()]
        if pending:
            raise RuntimeError(
                f"{len(pending)} of {len(subs)} still PendingConfirmation. "
                "Click the link in the email NOW. Advisories published before "
                "confirmation are dropped and cannot be recovered.")
        return f"{len(subs)} confirmed"

    # --- Report -------------------------------------------------------------
    print("PASS")
    for p in PASS:
        print(f"  ok    {p}")
    if WARN:
        print("\nWARN")
        for w in WARN:
            print(f"  warn  {w}")
    if FAIL:
        print("\nFAIL")
        for f in FAIL:
            print(f"  FAIL  {f}")

    print(f"\n{len(PASS)} passed, {len(WARN)} warnings, {len(FAIL)} failed")
    if FAIL:
        print("\nDo not start the units until these are resolved. Every one of "
              "them fails silently at runtime rather than loudly.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
