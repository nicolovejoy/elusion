"""Publish document ids to the `documents.ingested` topic.

Kafka is a durable, ordered log of messages grouped into topics; Redpanda speaks the
same protocol. Here collect is the producer and the review flow (phase 2) is the
consumer. Messages are keyed by doc_id so a topic with compaction enabled would keep
exactly one entry per document. The producer buffers in memory and `flush()` blocks
until the broker has acknowledged every message, so a non-zero return means loss.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

TOPIC = "documents.ingested"


def ensure_topic(bootstrap: str, topic: str = TOPIC, partitions: int = 1) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    if topic in admin.list_topics(timeout=10).topics:
        return
    futures = admin.create_topics([NewTopic(topic, num_partitions=partitions)])
    for f in futures.values():
        f.result(timeout=30)  # raises if creation failed


def publish_doc_ids(
    bootstrap: str, messages: Iterable[tuple[str, dict]], topic: str = TOPIC
) -> int:
    """Produce one message per (doc_id, payload). Returns the number acknowledged."""
    producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
    sent = 0
    for key, payload in messages:
        # poll(0) services delivery callbacks and keeps the buffer from filling up.
        producer.produce(topic, key=key, value=json.dumps(payload).encode())
        producer.poll(0)
        sent += 1
    unflushed = producer.flush(timeout=60)
    if unflushed:
        raise RuntimeError(f"{unflushed} messages were not acknowledged by the broker")
    return sent
