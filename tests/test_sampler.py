import uuid

import pytest
from confluent_kafka.admin import AdminClient

from elusion.collect.kafka import ensure_topic, publish_doc_ids
from elusion.config import settings
from elusion.review.sampler import KafkaIdSource, group_id
from tests.conftest import requires_service

pytestmark = requires_service("localhost", 9092, "redpanda")


@pytest.fixture
def topic():
    name = f"test.{uuid.uuid4().hex[:8]}"
    ensure_topic(settings.kafka_bootstrap, name)
    yield name
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for f in admin.delete_topics([name]).values():
        f.result(timeout=30)


def test_group_id_encodes_criterion_model_version() -> None:
    g = group_id("cloud-concentration", "claude-haiku-5-5", "cloud-concentration/v1")
    assert g == "review.cloud-concentration.claude-haiku-5-5.cloud-concentration.v1"


def test_commit_resumes_and_new_group_starts_over(topic) -> None:
    ids = [uuid.uuid4().hex[:16] for _ in range(7)]
    publish_doc_ids(settings.kafka_bootstrap, ((i, {"doc_id": i}) for i in ids), topic)
    group = f"test.{uuid.uuid4().hex[:8]}"

    src = KafkaIdSource(settings.kafka_bootstrap, group, topic=topic)
    assert src.take(3) == ids[:3]
    src.commit()
    src.close()

    src = KafkaIdSource(settings.kafka_bootstrap, group, topic=topic)
    assert src.take(10, idle_timeout_s=2.0) == ids[3:]
    src.close()  # not committed

    src = KafkaIdSource(settings.kafka_bootstrap, group + ".other", topic=topic)
    assert src.take(10, idle_timeout_s=2.0) == ids
    src.close()
