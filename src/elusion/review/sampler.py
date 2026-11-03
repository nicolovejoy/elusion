"""Where the review flow gets its document ids: the `documents.ingested` Kafka topic.

A Kafka *consumer group* is a named reader of a topic. The broker stores, per group and
partition, the *committed offset*: the position of the next message that group has not
yet processed. A consumer that joins the group resumes from that offset; a group the
broker has never seen starts from `auto.offset.reset` (here: the earliest message).

We name the group after (criterion, model, prompt version). A new prompt version is a
new group, so it reads the topic from the beginning; re-running an old version resumes
where it stopped. Auto-commit is off: the flow calls `commit()` only after a batch's
scores are safely in Postgres. If the process dies in between, those messages are
delivered again and the idempotency check in Postgres skips the ones already paid for.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from confluent_kafka import Consumer, KafkaException, TopicPartition

from elusion.collect.kafka import TOPIC

JOIN_TIMEOUT_S = 30.0  # joining a group (rebalance) can take a few seconds on first poll


# runtime_checkable: Prefect builds a pydantic schema from the flow signature, which
# isinstance-checks parameters; a plain Protocol cannot be used with isinstance.
@runtime_checkable
class IdSource(Protocol):
    def take(self, n: int, idle_timeout_s: float = 5.0) -> list[str]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


def group_id(criterion: str, model: str, prompt_version: str) -> str:
    return f"review.{criterion}.{model}.{prompt_version}".replace("/", ".")


class KafkaIdSource:
    """Reads doc ids (message keys) from a topic under one consumer group.

    Offsets are tracked explicitly: for each partition we remember the offset of the last
    message handed out plus one, and `commit()` sends exactly those, synchronously.
    """

    def __init__(self, bootstrap: str, group_id: str, topic: str = TOPIC) -> None:
        self._assigned = False
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        self._consumer.subscribe([topic], on_assign=self._on_assign)
        self._next: dict[tuple[str, int], int] = {}

    def _on_assign(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        self._assigned = True

    def take(self, n: int, idle_timeout_s: float = 5.0) -> list[str]:
        """Up to `n` ids; fewer if no message arrives for `idle_timeout_s` (topic dry).

        The idle clock starts only once the group has assigned us partitions, so a slow
        group join is not mistaken for an empty topic.
        """
        ids: list[str] = []
        start = time.monotonic()
        last = start
        while len(ids) < n:
            msg = self._consumer.poll(0.5)
            now = time.monotonic()
            if msg is None:
                if not self._assigned:
                    if now - start > JOIN_TIMEOUT_S:
                        raise TimeoutError(f"no partition assignment after {JOIN_TIMEOUT_S}s")
                    last = now
                elif now - last >= idle_timeout_s:
                    break
                continue
            if msg.error():
                raise KafkaException(msg.error())
            ids.append(msg.key().decode("utf-8"))
            self._next[(msg.topic(), msg.partition())] = msg.offset() + 1
            last = now
        return ids

    def commit(self) -> None:
        """Synchronously commit the position after every id taken so far."""
        if not self._next:
            return
        self._consumer.commit(
            offsets=[TopicPartition(t, p, o) for (t, p), o in self._next.items()],
            asynchronous=False,
        )

    def close(self) -> None:
        self._consumer.close()
