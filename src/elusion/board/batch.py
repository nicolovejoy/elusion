"""The papers a first read works on: loaded from Parquet, narrowed by a word search.

A *word search* is the cheapest filter there is: keep a paper when its abstract contains
one of the concept's search terms, with no model involved. The first read only pays for
papers that pass it. The gold set is a different matter: `held_back_ids` names the papers
the owner has labelled for evaluation, so a caller can keep them out of anything a model
sees.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from elusion.evaluate.gold import read_rows

_COLUMNS = ["doc_id", "work_id", "title", "publication_date", "text"]


@dataclass(frozen=True)
class Paper:
    doc_id: str
    work_id: str
    title: str
    publication_date: str
    text: str


def load_papers(abstracts_dir: Path) -> list[Paper]:
    """Every paper in every `year=*.parquet` file under `abstracts_dir`."""
    papers = []
    for path in sorted(abstracts_dir.glob("year=*.parquet")):
        table = pq.read_table(path, columns=_COLUMNS)
        papers.extend(Paper(**row) for row in table.to_pylist())
    return papers


def word_search(papers: Iterable[Paper], terms: Sequence[str]) -> list[Paper]:
    """The papers whose text contains any term, ignoring case, sorted by `doc_id`."""
    if not terms:
        raise ValueError("a word search needs at least one term")
    needles = [t.lower() for t in terms]
    found = [p for p in papers if any(n in p.text.lower() for n in needles)]
    return sorted(found, key=lambda p: p.doc_id)


def held_back_ids(gold_csv: Path) -> set[str]:
    """Every `doc_id` in the gold CSV; the empty set when the file does not exist."""
    return {row.doc_id for row in read_rows(gold_csv)}
