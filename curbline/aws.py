"""
AWS clients and the shared SQS worker loop.

Credentials come from the EC2 instance role. There are no keys in this repo,
no keys in .env, and no keys on disk. boto3 resolves the role automatically
from the instance metadata service.
"""

from __future__ import annotations

import json
import logging
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import boto3

from . import config

log = logging.getLogger(__name__)

_session = boto3.session.Session(region_name=config.REGION)
sqs = _session.client("sqs")
sns = _session.client("sns")
s3 = _session.client("s3")


class Shutdown:
    """
    SIGTERM handler so systemd can stop a worker without losing in-flight work.
    The loop finishes the message it is holding, then exits.
    """

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame) -> None:  # noqa: ARG002
        log.info("shutdown requested (signal %s), draining", signum)
        self.requested = True


def send(queue_url: str, body: dict[str, Any]) -> str:
    return sqs.send_message(
        QueueUrl=queue_url, MessageBody=json.dumps(body, default=str)
    )["MessageId"]


def publish(subject: str, message: dict[str, Any]) -> str:
    return sns.publish(
        TopicArn=config.SNS_TOPIC,
        Subject=subject[:100],
        Message=json.dumps(message, indent=2, default=str),
    )["MessageId"]


def write_audit(zone_id: str, payload: dict[str, Any]) -> str:
    """
    Write the immutable decision record to S3 before the unit is considered
    complete. If this fails the message is not deleted and the unit is retried,
    so no advisory is ever issued without a durable record of why.
    """
    now = datetime.now(timezone.utc)
    key = (
        f"advisories/{now:%Y/%m/%d}/{zone_id}/"
        f"{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    )
    s3.put_object(
        Bucket=config.AUDIT_BUCKET,
        Key=key,
        Body=json.dumps(payload, indent=2, default=str).encode(),
        ContentType="application/json",
    )
    return key


def consume(
    queue_url: str,
    handler: Callable[[dict[str, Any]], None],
    shutdown: Shutdown,
    max_messages: int = 10,
) -> None:
    """
    Long-polling SQS consumer.

    Contract, and every line of it matters:
      - WaitTimeSeconds=20 is long polling. Short polling burns request quota
        and money returning empty responses.
      - The message is deleted only after handler() returns without raising.
        A raised exception leaves the message on the queue, where it becomes
        visible again after the visibility timeout and is retried.
      - After maxReceiveCount failures the redrive policy moves it to the DLQ
        rather than looping forever.
      - handler() must be idempotent, because at-least-once delivery means it
        will occasionally see the same message twice.
    """
    while not shutdown.requested:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=20,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for msg in resp.get("Messages", []):
            try:
                body = json.loads(msg["Body"])
                handler(body)
            except Exception:
                log.exception(
                    "handler failed, leaving message for retry (receive #%s)",
                    msg.get("Attributes", {}).get("ApproximateReceiveCount"),
                )
                continue

            sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"]
            )

        if shutdown.requested:
            break


def poll_loop(
    fn: Callable[[], None],
    interval_seconds: int,
    shutdown: Shutdown,
) -> None:
    """Run fn on a fixed cadence, staying responsive to SIGTERM between ticks."""
    while not shutdown.requested:
        started = time.monotonic()
        try:
            fn()
        except Exception:
            log.exception("poll iteration failed, continuing")

        elapsed = time.monotonic() - started
        remaining = max(0.0, interval_seconds - elapsed)
        # Sleep in short slices so shutdown is not delayed by a long interval.
        while remaining > 0 and not shutdown.requested:
            nap = min(1.0, remaining)
            time.sleep(nap)
            remaining -= nap
