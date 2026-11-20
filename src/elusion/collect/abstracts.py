"""Abstract storage: one Parquet file per publication year under data/abstracts/.

The same layout as the 10-K corpus (`year=2026.parquet`, a `doc_id` and a `text` column),
so `review.corpus.load_texts` reads either directory. The other columns are what the
report needs to name a work: its OpenAlex id, title and DOI.

A harvest adds to these files. The 10-K collect rewrites a whole year, because it
downloads a whole year; a harvest brings whatever the searches return today, so a file
is read, the documents it lacks are appended, and it is written back. The new file is
written beside the old one and then renamed over it, and a rename is atomic: a crash
leaves the old file or the new one, never half of one.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from elusion.collect.openalex import Work
from elusion.collect.parquet import year_path

SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("work_id", pa.string()),
        ("title", pa.string()),
        ("year", pa.int16()),
        ("publication_date", pa.string()),
        ("language", pa.string()),
        ("type", pa.string()),
        ("doi", pa.string()),
        ("content_hash", pa.string()),
        ("text", pa.string()),
    ]
)


def add_works(abstracts_dir: Path, works: Iterable[Work]) -> int:
    """Add each work its year's file does not already hold. Returns the rows added."""
    by_year: dict[int, dict[str, Work]] = {}
    for w in works:
        by_year.setdefault(w["year"], {})[w["doc_id"]] = w
    added = 0
    for year, incoming in sorted(by_year.items()):
        path = year_path(abstracts_dir, year)
        existing = pq.read_table(path, schema=SCHEMA) if path.exists() else SCHEMA.empty_table()
        have = set(existing["doc_id"].to_pylist())
        new = [w for doc_id, w in incoming.items() if doc_id not in have]
        if not new:
            continue
        table = pa.concat_tables([existing, pa.Table.from_pylist(new, schema=SCHEMA)])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
        added += len(new)
    return added


def doc_ids_by_work(abstracts_dir: Path) -> dict[str, set[str]]:
    """Every `doc_id` stored for each OpenAlex `work_id`, over every year file.

    A document id is derived from the abstract's text, so a work whose abstract changed
    between two harvests is stored as two documents; that is why the value is a set.
    """
    by_work: dict[str, set[str]] = {}
    for path in sorted(abstracts_dir.glob("year=*.parquet")):
        table = pq.read_table(path, columns=["doc_id", "work_id"])
        for doc_id, work_id in zip(
            table["doc_id"].to_pylist(), table["work_id"].to_pylist(), strict=True
        ):
            by_work.setdefault(work_id, set()).add(doc_id)
    return by_work
