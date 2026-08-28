"""
Unit tests. None of these touch a real AWS account or incur spend: moto stands
in for SQS, SNS and S3, and the database layer is stubbed.

Coverage targets the four paths that actually break in a queue pipeline:
the happy path, duplicate delivery, a failing downstream, and a cold cache.

    pytest -q
"""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

# Config reads the environment at import time, so it has to be populated
# before anything under curbline is imported.
os.environ.setdefault("CURBLINE_DB_HOST", "localhost")
os.environ.setdefault("CURBLINE_DB_PASSWORD", "test")
os.environ.setdefault("CURBLINE_CACHE_HOST", "localhost")
os.environ.setdefault("CURBLINE_QUEUE_INGEST", "http://q/ingest")
os.environ.setdefault("CURBLINE_QUEUE_ZONES", "http://q/zones")
os.environ.setdefault("CURBLINE_SNS_TOPIC", "arn:aws:sns:us-east-1:1:t")
os.environ.setdefault("CURBLINE_AUDIT_BUCKET", "test-bucket")
# The advisory ladder below asserts FloodNet boundaries, so pin the source
# rather than inheriting the usgs default. Under D-005 the thresholds move
# with the source, which makes "which source" part of what these tests mean.
os.environ.setdefault("CURBLINE_SOURCE", "floodnet")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


# ---------------------------------------------------------------------------
# Zone identity
# ---------------------------------------------------------------------------

class TestZoneIdentity:
    def test_same_sensors_produce_same_id(self):
        from workers.correlator import stable_zone_id
        assert stable_zone_id(["b", "a"]) == stable_zone_id(["a", "b"])

    def test_id_is_order_independent(self):
        from workers.correlator import stable_zone_id
        ids = {stable_zone_id(p) for p in (
            ["q1", "q2", "q3"], ["q3", "q1", "q2"], ["q2", "q3", "q1"])}
        assert len(ids) == 1

    def test_different_membership_produces_different_id(self):
        """The known tradeoff, asserted so it is a documented property rather
        than a surprise: gaining a sensor makes it a new zone."""
        from workers.correlator import stable_zone_id
        assert stable_zone_id(["a", "b"]) != stable_zone_id(["a", "b", "c"])

    def test_id_is_a_valid_uuid(self):
        import uuid
        from workers.correlator import stable_zone_id
        uuid.UUID(stable_zone_id(["a", "b"]))


# ---------------------------------------------------------------------------
# Advisory ladder
# ---------------------------------------------------------------------------

class TestAdvisoryLadder:
    @pytest.mark.parametrize("depth,count,alert,expected", [
        (5.0,  2, False, "monitor"),
        (9.9,  2, False, "monitor"),
        (10.0, 2, False, "advisory"),
        (20.0, 2, False, "warning"),
        (12.0, 2, True,  "warning"),    # corroboration escalates
        (12.0, 2, False, "advisory"),   # same depth, no corroboration
        (5.0,  3, True,  "advisory"),   # breadth plus corroboration escalates
    ])
    def test_levels(self, depth, count, alert, expected):
        from workers.dispatcher import decide_level
        assert decide_level(depth, count, alert) == expected

    def test_nws_corroboration_never_lowers_a_level(self):
        from workers.dispatcher import decide_level
        order = ["monitor", "advisory", "warning"]
        for depth in (4.0, 9.0, 11.0, 19.0, 25.0):
            for count in (2, 3, 5):
                without = decide_level(depth, count, False)
                with_ = decide_level(depth, count, True)
                assert order.index(with_) >= order.index(without)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_new_zone_forms_before_it_activates(self):
        """The one-cycle delay that suppresses single-cycle sensor noise."""
        from workers.dispatcher import next_state
        assert next_state(None) == "forming"

    def test_forming_promotes_to_active(self):
        from workers.dispatcher import next_state
        assert next_state("forming") == "active"

    def test_a_republished_zone_stays_active(self):
        from workers.dispatcher import next_state
        assert next_state("active") == "active"

    def test_receding_recovers_when_the_zone_is_published_again(self):
        """
        Recession is decided by absence, not by the message. A zone that shows
        up on the queue again is by definition still clustering, so it recovers.
        """
        from workers.dispatcher import next_state
        assert next_state("receding") == "active"


class TestRecessionIsDrivenByAbsence:
    """
    E-020. next_state used to recede on `sensor_count < CLUSTER_MIN_SENSORS`,
    which cannot happen: current_clusters() is called with
    p_minpoints := CLUSTER_MIN_SENSORS and schema.sql filters
    `WHERE cid IS NOT NULL`, so every row it returns is a DBSCAN cluster with at
    least minpoints members. The comparison was a tautology, `receding` was
    unreachable, and no zone ever closed.

    A zone stops being flooded by disappearing from the cluster set, which is
    an event no per-message handler can observe. It has to be swept for.
    """

    def test_a_zone_the_pipeline_can_produce_never_recedes_by_count(self):
        """The regression guard: assert against reachable inputs only."""
        from workers.dispatcher import next_state
        from curbline import config
        # The smallest cluster current_clusters() can emit.
        assert config.CLUSTER_MIN_SENSORS >= 1
        assert next_state("active") == "active"

    def test_stale_active_zone_recedes(self):
        from workers.dispatcher import sweep_state
        assert sweep_state("active") == "receding"
        assert sweep_state("forming") == "receding"

    def test_stale_receding_zone_closes(self):
        from workers.dispatcher import sweep_state
        assert sweep_state("receding") == "closed"

    def test_a_closed_zone_is_left_alone(self):
        from workers.dispatcher import sweep_state
        assert sweep_state("closed") is None


class TestAdvisorySuppression:
    """
    E-021. The suppression key omitted `level`, so a zone escalating from
    advisory to warning while staying `active` with unchanged corroboration was
    treated as unchanged and never re-notified. Combined with E-020 that meant
    each zone issued at most one advisory ever, at the level it happened to
    carry when it first activated.
    """

    def test_escalation_notifies_even_when_state_is_unchanged(self):
        from workers.dispatcher import should_notify
        previous = {"state": "active", "last_level": "advisory",
                    "under_alert": False}
        assert should_notify(previous, "active", "warning", False) is True, (
            "rising water that crosses a threshold must reach a human"
        )

    def test_an_unchanged_level_is_suppressed(self):
        from workers.dispatcher import should_notify
        previous = {"state": "active", "last_level": "advisory",
                    "under_alert": False}
        assert should_notify(previous, "active", "advisory", False) is False

    def test_new_corroboration_notifies(self):
        from workers.dispatcher import should_notify
        previous = {"state": "active", "last_level": "advisory",
                    "under_alert": False}
        assert should_notify(previous, "active", "advisory", True) is True

    def test_a_forming_zone_never_notifies(self):
        from workers.dispatcher import should_notify
        assert should_notify(None, "forming", "warning", True) is False

    def test_a_zone_with_no_history_notifies(self):
        from workers.dispatcher import should_notify
        assert should_notify(None, "active", "monitor", False) is True

    def test_a_zone_that_never_issued_an_advisory_notifies(self):
        """
        last_level is NULL for a zone that formed, activated and was suppressed
        before any advisory was written. That must not compare equal to a real
        level and silence the first genuine one.
        """
        from workers.dispatcher import should_notify
        previous = {"state": "active", "last_level": None, "under_alert": False}
        assert should_notify(previous, "active", "monitor", False) is True


# ---------------------------------------------------------------------------
# Cache degradation. The property that matters: correctness never depends
# on Redis being up.
# ---------------------------------------------------------------------------

class TestCacheDegradation:
    def setup_method(self):
        from curbline import cache
        cache.STATS.update(hits=0, misses=0, errors=0)
        cache._client = None

    def test_cold_cache_falls_through_to_loader(self):
        from curbline import cache
        fake = mock.MagicMock()
        fake.get.return_value = None
        with mock.patch.object(cache, "client", return_value=fake):
            got = cache.read_through("k", lambda: {"v": 1})
        assert got == {"v": 1}
        assert cache.STATS["misses"] == 1

    def test_unreachable_cache_still_returns_correct_data(self):
        import redis
        from curbline import cache
        fake = mock.MagicMock()
        fake.get.side_effect = redis.RedisError("down")
        fake.setex.side_effect = redis.RedisError("down")
        with mock.patch.object(cache, "client", return_value=fake):
            got = cache.read_through("k", lambda: {"v": 2})
        assert got == {"v": 2}, "a dead cache must not change the answer"
        assert cache.STATS["errors"] >= 1

    def test_corrupt_entry_reloads_rather_than_raising(self):
        from curbline import cache
        fake = mock.MagicMock()
        fake.get.return_value = "{not json"
        with mock.patch.object(cache, "client", return_value=fake):
            assert cache.read_through("k", lambda: {"v": 3}) == {"v": 3}

    def test_hit_returns_cached_value_without_calling_loader(self):
        from curbline import cache
        fake = mock.MagicMock()
        fake.get.return_value = json.dumps({"v": 4})
        loader = mock.MagicMock()
        with mock.patch.object(cache, "client", return_value=fake):
            assert cache.read_through("k", loader) == {"v": 4}
        loader.assert_not_called()
        assert cache.STATS["hits"] == 1


# ---------------------------------------------------------------------------
# E-019: the cache counters live in whichever process did the work, and the
# process that displays them is not that process. These assert the transport,
# not the counting.
# ---------------------------------------------------------------------------

class TestCacheStatsTransport:
    def setup_method(self):
        from curbline import cache
        cache.STATS.update(hits=0, misses=0, errors=0)
        cache._client = None

    def test_flush_publishes_local_counts_and_clears_them(self):
        """A flush moves the deltas to Redis so another process can read them."""
        from curbline import cache
        cache.STATS.update(hits=7, misses=3, errors=1)
        fake = mock.MagicMock()
        with mock.patch.object(cache, "client", return_value=fake):
            cache.flush_stats()

        published = {
            c.args[0]: c.args[1] for c in fake.incrby.call_args_list
        }
        assert published == {
            "stats:cache:hits": 7,
            "stats:cache:misses": 3,
            "stats:cache:errors": 1,
        }
        assert cache.STATS == {"hits": 0, "misses": 0, "errors": 0}, (
            "deltas must be cleared or the next flush double-counts them"
        )

    def test_failed_flush_retains_the_deltas(self):
        """
        The flush target is the thing that just failed. Losing the deltas on a
        failed flush would silently under-report exactly when the cache is
        having problems, which is when the number matters most.
        """
        import redis
        from curbline import cache
        cache.STATS.update(hits=5, misses=2, errors=0)
        fake = mock.MagicMock()
        fake.incrby.side_effect = redis.RedisError("down")
        with mock.patch.object(cache, "client", return_value=fake):
            cache.flush_stats()
        assert cache.STATS["hits"] == 5, "a failed flush must not drop counts"
        assert cache.STATS["misses"] == 2

    def test_read_stats_returns_the_published_aggregate(self):
        from curbline import cache
        fake = mock.MagicMock()
        fake.mget.return_value = ["12", "4", "0"]
        with mock.patch.object(cache, "client", return_value=fake):
            got = cache.read_stats()
        assert got == {"hits": 12, "misses": 4, "errors": 0, "hit_rate": 0.75}

    def test_read_stats_survives_an_unreachable_cache(self):
        """
        The degradation screenshot depends on this. An unreachable cache makes
        the hit rate unknown, which is not the same as zero and must not be
        rendered as one.
        """
        import redis
        from curbline import cache
        fake = mock.MagicMock()
        fake.mget.side_effect = redis.RedisError("down")
        with mock.patch.object(cache, "client", return_value=fake):
            got = cache.read_stats()
        assert got["hit_rate"] is None
        assert got["hits"] == 0

    def test_never_published_counters_read_as_unknown_not_zero(self):
        from curbline import cache
        fake = mock.MagicMock()
        fake.mget.return_value = [None, None, None]
        with mock.patch.object(cache, "client", return_value=fake):
            got = cache.read_stats()
        assert got["hit_rate"] is None, (
            "no reads yet is unknown, not a 0% hit rate"
        )


# ---------------------------------------------------------------------------
# Worker liveness. An empty queue and a dead worker look identical from the
# API, so health has to ask the workers directly rather than infer them.
# ---------------------------------------------------------------------------

class TestWorkerHeartbeat:
    def setup_method(self):
        from curbline import cache
        cache._client = None

    def test_beat_writes_a_key_that_expires(self):
        from curbline import cache
        fake = mock.MagicMock()
        with mock.patch.object(cache, "client", return_value=fake):
            cache.beat("correlator")
        key, ttl, _value = fake.setex.call_args.args
        assert key == "heartbeat:correlator"
        assert ttl > 0, "a heartbeat that never expires cannot report death"

    def test_a_silent_worker_reads_as_not_live(self):
        """
        The point of the whole mechanism. A stopped worker stops refreshing its
        key, the key expires, and liveness has to report that rather than
        assuming the worker is fine because nothing said otherwise.
        """
        from curbline import cache
        fake = mock.MagicMock()
        fake.mget.return_value = ["2026-08-28T21:00:00Z", None, None]
        with mock.patch.object(cache, "client", return_value=fake):
            live = cache.worker_liveness()
        assert live["collector"] is True
        assert live["correlator"] is False
        assert live["dispatcher"] is False

    def test_unreachable_cache_reports_unknown_not_dead(self):
        """
        A dead Redis must not be reported as three dead workers. The workers
        may well be running; what is unavailable is the evidence.
        """
        import redis
        from curbline import cache
        fake = mock.MagicMock()
        fake.mget.side_effect = redis.RedisError("down")
        with mock.patch.object(cache, "client", return_value=fake):
            live = cache.worker_liveness()
        assert all(v is None for v in live.values()), (
            "unknown is a third state and must not collapse into False"
        )

    def test_beat_never_raises_into_the_pipeline(self):
        import redis
        from curbline import cache
        fake = mock.MagicMock()
        fake.setex.side_effect = redis.RedisError("down")
        with mock.patch.object(cache, "client", return_value=fake):
            cache.beat("collector")  # must not raise


# ---------------------------------------------------------------------------
# Worker loop semantics, against moto's SQS
# ---------------------------------------------------------------------------

@pytest.fixture
def sqs_queue():
    from moto import mock_aws
    with mock_aws():
        import boto3
        client = boto3.client("sqs", region_name="us-east-1")
        url = client.create_queue(QueueName="t")["QueueUrl"]
        yield client, url


class TestWorkerLoop:
    def test_message_is_deleted_only_after_the_handler_succeeds(self, sqs_queue):
        from curbline import aws
        client, url = sqs_queue
        client.send_message(QueueUrl=url, MessageBody=json.dumps({"n": 1}))

        shutdown = mock.MagicMock()
        shutdown.requested = False
        seen = []

        def handler(body):
            seen.append(body)
            shutdown.requested = True

        with mock.patch.object(aws, "sqs", client):
            aws.consume(url, handler, shutdown)

        assert seen == [{"n": 1}]
        left = client.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
        )["Attributes"]["ApproximateNumberOfMessages"]
        assert left == "0"

    def test_failing_handler_leaves_the_message_for_retry(self, sqs_queue):
        from curbline import aws
        client, url = sqs_queue
        client.send_message(QueueUrl=url, MessageBody=json.dumps({"n": 2}))

        shutdown = mock.MagicMock()
        shutdown.requested = False
        calls = []

        def handler(body):
            calls.append(body)
            shutdown.requested = True
            raise RuntimeError("downstream is down")

        with mock.patch.object(aws, "sqs", client):
            aws.consume(url, handler, shutdown)

        assert len(calls) == 1
        # Not deleted. It becomes visible again after the visibility timeout,
        # and after maxReceiveCount failures the redrive policy dead-letters it.
        attrs = client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages",
                            "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        assert (int(attrs.get("ApproximateNumberOfMessages", 0))
                + int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))) == 1


# ---------------------------------------------------------------------------
# Audit ordering. The invariant: no notification without a durable record.
# ---------------------------------------------------------------------------

class TestAuditOrdering:
    def test_audit_write_precedes_sns_publish(self):
        from workers import dispatcher

        order = []
        body = {
            "zone_id": "11111111-1111-1111-1111-111111111111",
            "sensor_ids": ["a", "b"], "sensor_count": 2,
            "max_depth_cm": 25.0, "alert_id": "nws:1",
            "hull_geojson": '{"type":"Polygon","coordinates":[]}',
        }

        with mock.patch.object(dispatcher.db, "open_zones",
                               return_value=[{
                                   "zone_id": body["zone_id"],
                                   "state": "forming",
                                   "under_alert": False,
                               }]), \
             mock.patch.object(dispatcher.db, "upsert_zone"), \
             mock.patch.object(dispatcher.db, "record_advisory",
                               return_value="adv-1"), \
             mock.patch.object(dispatcher.aws, "write_audit",
                               side_effect=lambda *a, **k: (
                                   order.append("audit"), "key")[1]), \
             mock.patch.object(dispatcher.aws, "publish",
                               side_effect=lambda *a, **k: (
                                   order.append("sns"), "msg")[1]):
            dispatcher.handle(body)

        assert order == ["audit", "sns"]

    def test_failed_audit_blocks_the_notification(self):
        from workers import dispatcher

        body = {
            "zone_id": "22222222-2222-2222-2222-222222222222",
            "sensor_ids": ["a", "b"], "sensor_count": 2,
            "max_depth_cm": 25.0, "alert_id": None,
            "hull_geojson": '{"type":"Polygon","coordinates":[]}',
        }

        with mock.patch.object(dispatcher.db, "open_zones",
                               return_value=[{
                                   "zone_id": body["zone_id"],
                                   "state": "forming",
                                   "under_alert": False,
                               }]), \
             mock.patch.object(dispatcher.db, "upsert_zone"), \
             mock.patch.object(dispatcher.aws, "write_audit",
                               side_effect=RuntimeError("s3 down")), \
             mock.patch.object(dispatcher.aws, "publish") as publish:
            with pytest.raises(RuntimeError):
                dispatcher.handle(body)

        publish.assert_not_called()


# ---------------------------------------------------------------------------
# Collector filtering
# ---------------------------------------------------------------------------

class TestAlertFiltering:
    def test_null_geometry_alerts_are_still_published(self):
        """Zone-based NWS products carry no polygon. They must survive ingest
        and be stored with a NULL geometry, not be silently dropped."""
        from workers import collector

        payload = {"features": [{
            "geometry": None,
            "properties": {
                "id": "urn:oid:1", "event": "Flood Watch",
                "severity": "Moderate", "headline": "h",
                "effective": "2026-08-27T00:00:00Z",
                "expires": "2026-08-28T00:00:00Z",
            },
        }]}

        response = mock.MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None

        with mock.patch.object(collector.requests, "get",
                               return_value=response), \
             mock.patch.object(collector.aws, "send") as send:
            collector.poll_alerts()

        assert send.call_count == 1
        assert send.call_args[0][1]["geometry"] is None

    def test_non_flood_events_are_dropped(self):
        from workers import collector

        payload = {"features": [{
            "geometry": None,
            "properties": {"id": "x", "event": "Air Quality Alert"},
        }]}
        response = mock.MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None

        with mock.patch.object(collector.requests, "get",
                               return_value=response), \
             mock.patch.object(collector.aws, "send") as send:
            collector.poll_alerts()

        send.assert_not_called()


class TestSourceCalibration:
    """D-005: detection thresholds move with the reading source.

    These assert the mapping function directly rather than config module state.
    config reads the environment once at import, so a per-test source cannot be
    exercised without reloading the module, and the mapping is the part that
    carries the decision anyway.
    """

    def test_floodnet_is_street_depth(self):
        from curbline.config import thresholds_for
        assert thresholds_for("floodnet") == (5.0, 10.0, 20.0)

    def test_usgs_is_an_order_of_magnitude_higher(self):
        from curbline.config import thresholds_for
        assert thresholds_for("usgs") == (60.0, 90.0, 120.0)

    def test_unknown_source_falls_back_to_the_sensitive_calibration(self):
        from curbline.config import thresholds_for
        assert thresholds_for("nonesuch") == thresholds_for("floodnet")

    def test_every_buildable_source_has_its_own_thresholds(self):
        """
        The two fallbacks disagree, and that is the E-014 failure again.
        thresholds_for() falls back to FloodNet's 5/10/20; build_source() falls
        back to USGS. A typo in CURBLINE_SOURCE would therefore collect river
        stage and grade it against street thresholds, which is the exact
        order-of-magnitude error D-005 exists to prevent. Neither fallback is
        wrong alone; they are wrong together, so the names must not drift apart.
        """
        from curbline.config import _THRESHOLDS
        from curbline import sources
        assert set(sources.SOURCES) == set(_THRESHOLDS), (
            "every source build_source() can construct needs a calibration, "
            "and every calibration needs a source, or the fallbacks diverge"
        )

    def test_an_unrecognised_source_is_rejected_rather_than_guessed(self):
        from curbline.config import validate_source
        with pytest.raises(ValueError, match="CURBLINE_SOURCE"):
            validate_source("floodnett")

    def test_a_recognised_source_validates_quietly(self):
        from curbline.config import validate_source
        for name in ("floodnet", "usgs", "replay"):
            validate_source(name)


class TestZoneLookupTypes:
    """E-017: psycopg returns uuid.UUID for a UUID column, the queue body carries
    a string, and UUID(x) != str(x). The existing TestAuditOrdering mocks
    open_zones with a string zone_id, which is exactly why it passed while no
    advisory had ever fired in production."""

    def test_uuid_keyed_open_zones_still_promotes_to_active(self):
        import uuid as _uuid
        from workers import dispatcher

        zid = "33333333-3333-3333-3333-333333333333"
        body = {
            "zone_id": zid,
            "sensor_ids": ["a", "b"], "sensor_count": 2,
            "max_depth_cm": 25.0, "alert_id": None,
            "hull_geojson": '{"type":"Polygon","coordinates":[]}',
        }
        written = {}

        with mock.patch.object(dispatcher.db, "open_zones",
                               return_value=[{
                                   "zone_id": _uuid.UUID(zid),
                                   "state": "forming",
                                   "under_alert": False,
                               }]),              mock.patch.object(dispatcher.db, "upsert_zone",
                               side_effect=lambda **k: written.update(k)),              mock.patch.object(dispatcher.db, "record_advisory",
                               return_value="adv-1"),              mock.patch.object(dispatcher.aws, "write_audit",
                               return_value="key"),              mock.patch.object(dispatcher.aws, "publish", return_value="msg"):
            dispatcher.handle(body)

        assert written["state"] == "active", (
            "a zone already forming must promote to active; keying the lookup "
            "on the raw UUID finds nothing and leaves it forming forever"
        )


class TestSensorCacheDivergence:
    """E-016: a cache hit describes a row, it does not prove the row exists."""

    def test_foreign_key_violation_repairs_the_sensor_and_retries(self):
        from psycopg.errors import ForeignKeyViolation
        from workers import correlator

        body = {
            "ingest_id": "ing-1", "sensor_id": "demo:q1", "name": "Q1",
            "lon": -73.79, "lat": 40.70,
            "observed_at": "2026-08-28T00:00:00Z",
            "depth_cm": 9.0, "source": "replay",
        }
        attempts = []

        def claim(**kwargs):
            attempts.append("claim")
            if len(attempts) == 1:
                raise ForeignKeyViolation("readings_sensor_id_fkey")
            return True

        with mock.patch.object(correlator.cache, "sensor",
                               return_value={"sensor_id": "demo:q1"}),              mock.patch.object(correlator.cache, "invalidate_sensor"),              mock.patch.object(correlator.db, "upsert_sensor") as upsert,              mock.patch.object(correlator.db, "claim_reading", side_effect=claim):
            correlator.handle_reading(body)

        assert upsert.called, "the sensor row must be rewritten after the violation"
        assert attempts == ["claim", "claim"], "the insert must be retried exactly once"

