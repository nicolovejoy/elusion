import uuid

from confluent_kafka import Consumer, TopicPartition

from elusion.collect.kafka import ensure_topic, publish_doc_ids
from elusion.config import settings
from tests.conftest import requires_service

pytestmark = requires_service("localhost", 9092, "redpanda")


def test_publish_lands_on_topic() -> None:
    topic = f"test.{uuid.uuid4().hex[:8]}"
    ensure_topic(settings.kafka_bootstrap, topic)
    ids = [uuid.uuid4().hex[:16] for _ in range(5)]
    assert publish_doc_ids(settings.kafka_bootstrap, ((i, {"doc_id": i}) for i in ids), topic) == 5

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": topic,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.assign([TopicPartition(topic, 0, 0)])
    got = []
    while len(got) < 5:
        msg = consumer.poll(10)
        assert msg is not None and not msg.error(), "timed out waiting for messages"
        got.append(msg.key().decode())
    consumer.close()
    assert got == ids
