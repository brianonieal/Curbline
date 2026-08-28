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
        assert next_state(None, 3) == "forming"

    def test_forming_promotes_to_active(self):
        from workers.dispatcher import next_state
        assert next_state("forming", 3) == "active"

    def test_active_recedes_when_sensors_drop_below_minimum(self):
        from workers.dispatcher import next_state
        assert next_state("active", 1) == "receding"

    def test_receding_recovers_if_sensors_return(self):
        from workers.dispatcher import next_state
        assert next_state("receding", 3) == "active"


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
