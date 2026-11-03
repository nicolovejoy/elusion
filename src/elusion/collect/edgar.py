"""Read EDGAR-CORPUS filings for one year, streamed from the HuggingFace Hub.

The dataset is three JSONL files per year (train/test/validate, an artefact of its
original NLP use; for us they are just one corpus). Each row is one 10-K with every
item as a string. `datasets` streams the files so the ~1.8 GB per year is never
written to disk; only Item 1A is kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from datasets import load_dataset

REPO = "eloukas/edgar-corpus"
SPLITS = ("train", "test", "validate")


@dataclass(frozen=True)
class Filing:
    cik: str
    year: int
    filename: str
    risk_factors: str  # Item 1A text, possibly empty


def iter_filings(year: int, limit: int | None = None) -> Iterator[Filing]:
    """Yield every filing for `year`, at most `limit` of them."""
    files = [f"hf://datasets/{REPO}/{year}/{split}.jsonl" for split in SPLITS]
    ds = load_dataset("json", data_files={"all": files}, split="all", streaming=True)
    for n, row in enumerate(ds):
        if limit is not None and n >= limit:
            return
        yield Filing(
            cik=str(row["cik"]),
            year=int(row["year"]),
            filename=row["filename"],
            risk_factors=row.get("section_1A") or "",
        )
