"""Corpus storage: one Parquet file per filing year under data/corpus/.

Parquet is columnar and typed, so a later job can read just `text` or just `cik`
without parsing the rest, and the same file is readable by pandas, DuckDB and Spark.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("cik", pa.string()),
        ("company", pa.string()),  # null when no name is known
        ("year", pa.int16()),
        ("paragraph_index", pa.int32()),
        ("text", pa.string()),
    ]
)


class Document(TypedDict):
    doc_id: str
    cik: str
    company: str | None
    year: int
    paragraph_index: int
    text: str


def year_path(corpus_dir: Path, year: int) -> Path:
    return corpus_dir / f"year={year}.parquet"


def write_year(docs: Iterable[Document], path: Path) -> int:
    """Write `docs` to `path`, replacing any existing file. Returns the row count."""
    table = pa.Table.from_pylist(list(docs), schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
    return table.num_rows


def read_year(path: Path) -> list[Document]:
    return pq.read_table(path, schema=SCHEMA).to_pylist()  # type: ignore[return-value]
