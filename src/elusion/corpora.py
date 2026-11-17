"""The corpora, and the one place where a criterion names the corpus it is reviewed against.

A *corpus* is a set of documents of one kind from one source: 10-K risk-factor paragraphs
from EDGAR, or paper abstracts from OpenAlex. Each has its own Kafka topic and its own
directory of Parquet text files, and every `documents` row says which it belongs to.

They are kept apart because of how the review flow finds its work. A new (criterion,
model, prompt version) is a new Kafka consumer group, and a new group reads its topic
from the first message. On a shared topic, a criterion written for abstracts would walk
1.6 million 10-K paragraphs and pay a model to screen each one against the wrong question.

A criterion belongs to exactly one corpus, named in `CRITERION_CORPUS` below. There is no
default and no command-line flag: a criterion that is not listed is refused before any
model is called, so a new criterion cannot be reviewed against the wrong corpus by
forgetting something.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Corpus:
    name: str  # the value of documents.corpus
    topic: str  # the Kafka topic its document ids are published to
    subdir: str  # its Parquet text directory, under ELUSION_DATA_DIR

    def text_dir(self, data_dir: Path) -> Path:
        return data_dir / self.subdir


EDGAR = Corpus(name="edgar", topic="documents.ingested", subdir="corpus")
OPENALEX = Corpus(name="openalex", topic="abstracts.ingested", subdir="abstracts")

CRITERION_CORPUS: dict[str, Corpus] = {
    "cloud-concentration": EDGAR,
    "auditable-decisions": OPENALEX,
}


class UnknownCriterion(LookupError):
    """A criterion that names no corpus."""


def corpus_for(criterion: str) -> Corpus:
    """The corpus `criterion` (a criterion name, the file's stem) is reviewed against."""
    try:
        return CRITERION_CORPUS[criterion]
    except KeyError:
        raise UnknownCriterion(
            f"criterion {criterion!r} names no corpus: add it to CRITERION_CORPUS in "
            "src/elusion/corpora.py"
        ) from None
