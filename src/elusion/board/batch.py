"""The papers a first read works on: loaded from Parquet, narrowed by a word search.

A *word search* is the cheapest filter there is: keep a paper when its abstract contains
one of the concept's search terms, with no model involved. The first read only pays for
papers that pass it. The gold set is a different matter: `held_back_ids` names the papers
the owner has labelled for evaluation, so a caller can keep them out of anything a model
sees.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from elusion.evaluate.gold import read_rows, refusals_path

_COLUMNS = ["doc_id", "work_id", "title", "publication_date", "text"]


@dataclass(frozen=True)
class Paper:
    doc_id: str
    work_id: str
    title: str
    publication_date: str
    text: str


def load_papers(abstracts_dir: Path) -> list[Paper]:
    """Every paper in every `year=*.parquet` file under `abstracts_dir`, once per `doc_id`:
    when two files hold the same id, the first in file order is kept."""
    papers: dict[str, Paper] = {}
    for path in sorted(abstracts_dir.glob("year=*.parquet")):
        table = pq.read_table(path, columns=_COLUMNS)
        for row in table.to_pylist():
            papers.setdefault(row["doc_id"], Paper(**row))
    return list(papers.values())


def word_search(papers: Iterable[Paper], terms: Sequence[str]) -> list[Paper]:
    """The papers whose text contains any term, ignoring case, sorted by `doc_id`."""
    if not terms:
        raise ValueError("a word search needs at least one term")
    needles = [t.lower() for t in terms]
    found = [p for p in papers if any(n in p.text.lower() for n in needles)]
    return sorted(found, key=lambda p: p.doc_id)


def held_back_ids(gold_csv: Path) -> set[str]:
    """Every `doc_id` in the gold CSV, and in the judge-refusals file beside it (a paper the
    judge refused to answer about was still drawn for evaluation, whatever model refused it
    and under whichever prompt version). The empty set when neither file exists."""
    ids = {row.doc_id for row in read_rows(gold_csv)}
    refusals = refusals_path(gold_csv)
    if refusals.is_file():
        with refusals.open(encoding="utf-8") as f:
            ids |= {json.loads(line)["doc_id"] for line in f if line.strip()}
    return ids
