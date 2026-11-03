"""Bucket events from Kafka -> the ingestion queue.

    uv add confluent-kafka
    uv run python -m app.ingestion.kafka_consumer        (KAFKA_BOOTSTRAP=kafka:9092)

Why Kafka between the bucket and us, instead of the webhook (POST /ingest/events/s3)?
    - buffering   a bulk upload of 50,000 files is 50,000 events in a few minutes. A webhook must take
                  them as they come (or MinIO retries and drops); a topic stores them and we read at
                  the pace the workers can index.
    - outages     backend down for a deploy? Events wait in the topic (retention: days) instead of
                  being lost; on restart the consumer continues from its last committed offset.
    - fan-out     other systems can read the same topic in their own consumer group (an audit trail,
                  a virus scanner) without touching this one.
    - replay      reset the group's offsets to re-run the events of a time window.

How this consumer handles the delivery problems every Kafka consumer has:
    commit after  enable.auto.commit=false: an event's offset is committed only after it was handled.
                  A crash in between means it's delivered again: at-least-once, never lost. Handling
                  is idempotent (events.py), so a repeat does nothing harmful.
    retries       a failing event (MinIO briefly down) is retried with backoff, up to
                  KAFKA_MAX_ATTEMPTS; meanwhile the partition waits, which keeps events in order
                  (a delete must not overtake the create before it).
    dead letter   an event that still fails, or isn't valid JSON, goes to KAFKA_DLQ_TOPIC with the
                  error, and the consumer moves on: one poison message can't stop all ingestion.
    scaling       consumers with the same group id split the topic's partitions between them: run as
                  many consumers as there are partitions (more would sit idle). Events of one object key
                  go to one partition (MinIO keys them by object), so their order is kept.
    shutdown      SIGTERM finishes the current event, commits, and leaves the group cleanly, so the
                  partitions move to the other consumers at once instead of after a session timeout.

MinIO publishes to Kafka with a notification target, once:
    mc admin config set local notify_kafka:ingest brokers=kafka:9092 topic=bucket-events
    mc admin service restart local
    mc event add local/company-docs arn:minio:sqs::ingest:kafka --event put,delete
AWS: S3 -> EventBridge -> (a pipe) -> Amazon MSK, or skip Kafka and consume SQS the same way.
"""

import json
import logging
import os
import signal
import socket
import time

from app.core import config, telemetry
from app.core.config import KAFKA_BOOTSTRAP, KAFKA_DLQ_TOPIC, KAFKA_GROUP, KAFKA_MAX_ATTEMPTS, KAFKA_TOPIC
from app.core.logging import set_request_id, setup_logging
from app.ingestion import events

log = logging.getLogger("kafka-consumer")
NAME = os.getenv("HOSTNAME") or socket.gethostname()

_stopping = False


def _stop(signum, _frame) -> None:
    global _stopping
    _stopping = True
    log.info("got %s, stopping after the current event", signal.Signals(signum).name)


def _clients():
    try:
        from confluent_kafka import Consumer, Producer
    except ImportError as e:
        raise SystemExit("the Kafka consumer needs: uv add confluent-kafka") from e
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": KAFKA_GROUP,
        "client.id": NAME,
        "enable.auto.commit": False,         # we commit after handling: at-least-once
        "auto.offset.reset": "earliest",     # a new group starts at the oldest retained event, not "now"
        "max.poll.interval.ms": 900_000,     # one event may take minutes (large download): don't get kicked out
        "partition.assignment.strategy": "cooperative-sticky",  # rebalances move only the partitions they must
    })
    dlq = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "client.id": f"{NAME}-dlq", "acks": "all"})
    return consumer, dlq


def _dead_letter(dlq, msg, error: str) -> None:
    """Park the event with why it failed, keyed like the original, for a person (or a replay) later."""
    dlq.produce(KAFKA_DLQ_TOPIC, key=msg.key(), value=msg.value(), headers=[
        ("error", error[:1000].encode()), ("source-topic", msg.topic().encode()),
        ("source-partition", str(msg.partition()).encode()), ("source-offset", str(msg.offset()).encode()),
    ])
    dlq.flush(10)  # before the original's offset is committed, or the event could be lost twice over


def process(msg, dlq) -> None:
    """Handle one event, retrying; dead-letter it if it keeps failing. Returns when it's safe to commit."""
    set_request_id(f"kafka-{msg.partition()}-{msg.offset()}")  # every log line about this event carries it
    try:
        body = json.loads(msg.value())
    except (TypeError, ValueError) as e:  # not JSON: retrying can't fix it
        log.warning("not an S3 event (offset %s): %s", msg.offset(), e)
        _dead_letter(dlq, msg, f"invalid JSON: {e}")
        return
    for attempt in range(1, KAFKA_MAX_ATTEMPTS + 1):
        try:
            with telemetry.span("ingest.kafka_event", partition=msg.partition(), offset=msg.offset(), attempt=attempt):
                result = events.handle(body)
            log.info("event %s/%s: %s", msg.partition(), msg.offset(), result)
            return
        except Exception as e:
            if attempt == KAFKA_MAX_ATTEMPTS or _stopping:
                log.exception("event %s/%s failed %d times; dead-lettered", msg.partition(), msg.offset(), attempt)
                _dead_letter(dlq, msg, f"{type(e).__name__}: {e}")
                return
            delay = min(60, 2 ** attempt)  # 2, 4, 8, 16 ... seconds: give a restarting MinIO time
            log.warning("event %s/%s attempt %d failed (%s); retrying in %ss", msg.partition(), msg.offset(),
                        attempt, e, delay)
            time.sleep(delay)


def main() -> None:
    setup_logging()
    config.validate()
    if not KAFKA_BOOTSTRAP:
        raise SystemExit("set KAFKA_BOOTSTRAP (e.g. kafka:9092)")
    telemetry.setup_tracing("mychatapp-kafka-consumer")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    consumer, dlq = _clients()
    consumer.subscribe([KAFKA_TOPIC])
    log.info("consumer %s reading %s as group %s", NAME, KAFKA_TOPIC, KAFKA_GROUP)
    try:
        while not _stopping:
            msg = consumer.poll(1.0)  # also runs the group protocol (heartbeats, rebalances): call it often
            if msg is None:
                continue
            if msg.error():
                log.warning("kafka: %s", msg.error())  # e.g. a broker restarting; the client reconnects itself
                continue
            process(msg, dlq)
            consumer.commit(message=msg, asynchronous=False)  # only now is the event done
    finally:
        consumer.close()  # leaves the group: its partitions are reassigned right away
        dlq.flush(10)
        telemetry.shutdown()
        log.info("consumer %s stopped", NAME)


if __name__ == "__main__":
    main()
