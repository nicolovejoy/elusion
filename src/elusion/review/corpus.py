"""Paragraph text lookup for the review flow: doc_id -> text from the Parquet corpus.

Kafka messages carry only ids, and Postgres holds only metadata, so the text the LLM reads
comes from data/corpus/year=*.parquet. Because Parquet is columnar we read just the two
columns we need. The whole map is loaded once per flow run: about 1.6M paragraphs, roughly
1 GB of Python strings in memory. That is acceptable on a laptop and simpler than indexed
lookups; at a larger scale this would become a keyed store or a join in Spark.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pyarrow.parquet as pq

log = logging.getLogger(__name__)


def load_texts(corpus_dir: Path) -> dict[str, str]:
    """Read doc_id and text from every year=*.parquet under `corpus_dir`."""
    start = time.monotonic()
    texts: dict[str, str] = {}
    for path in sorted(corpus_dir.glob("year=*.parquet")):
        table = pq.read_table(path, columns=["doc_id", "text"])
        texts.update(zip(table["doc_id"].to_pylist(), table["text"].to_pylist(), strict=True))
    log.info("loaded %s texts from %s in %.1fs", len(texts), corpus_dir, time.monotonic() - start)
    return texts
