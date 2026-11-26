"""The first read's inputs: abstracts loaded from Parquet, the word search, the held-back ids."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from elusion.board.batch import Paper, held_back_ids, load_papers, word_search
from elusion.collect.abstracts import SCHEMA
from elusion.evaluate.gold import GoldRow, append_rows


def write_year(directory: Path, year: int, papers: list[dict]) -> None:
    rows = [
        {
            "work_id": f"W{p['doc_id']}",
            "title": f"title {p['doc_id']}",
            "year": year,
            "publication_date": f"{year}-03-01",
            "language": "en",
            "type": "article",
            "doi": None,
            "content_hash": "h",
            **p,
        }
        for p in papers
    ]
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), directory / f"year={year}.parquet")


def paper(doc_id: str, text: str) -> Paper:
    return Paper(doc_id, f"W{doc_id}", f"title {doc_id}", "2024-03-01", text)


def test_load_papers_reads_every_year_file(tmp_path) -> None:
    write_year(tmp_path, 2023, [{"doc_id": "b", "text": "Second."}])
    write_year(tmp_path, 2024, [{"doc_id": "a", "text": "First."}])
    papers = load_papers(tmp_path)
    assert sorted(papers, key=lambda p: p.doc_id) == [
        Paper("a", "Wa", "title a", "2024-03-01", "First."),
        Paper("b", "Wb", "title b", "2023-03-01", "Second."),
    ]


def test_word_search_is_case_insensitive_and_sorted_by_doc_id() -> None:
    papers = [
        paper("c", "A Stopping Rule for review."),
        paper("a", "when to STOP a review"),
        paper("b", "nothing here"),
    ]
    found = word_search(papers, ["stopping rule", "stop a"])
    assert [p.doc_id for p in found] == ["a", "c"]


def test_word_search_refuses_no_terms() -> None:
    with pytest.raises(ValueError):
        word_search([paper("a", "text")], [])


def test_held_back_ids_are_the_doc_ids_in_the_gold_csv(tmp_path) -> None:
    csv = tmp_path / "gold" / "c.csv"
    append_rows(
        csv,
        [
            GoldRow("a", 1, "flagged", "human", "", "r", "2026-11-01T00:00:00Z"),
            GoldRow("b", 0, "discard", "m", "judge-v1", "r", "2026-11-01T00:00:00Z"),
            GoldRow("a", 1, "flagged", "m", "judge-v1", "r", "2026-11-01T00:00:00Z"),
        ],
    )
    assert held_back_ids(csv) == {"a", "b"}


def test_held_back_ids_without_a_file_is_empty(tmp_path) -> None:
    assert held_back_ids(tmp_path / "gold" / "missing.csv") == set()
