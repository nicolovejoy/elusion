"""Abstract text storage: one Parquet file per publication year, added to and never rewritten
from scratch, because a harvest only ever brings what is new."""

import pyarrow.parquet as pq

from elusion.collect.abstracts import add_works, doc_ids_by_work
from elusion.collect.openalex import to_work
from elusion.review.corpus import load_texts
from tests.test_openalex import work


def works(*numbers: int, year: int = 2026) -> list[dict]:
    return [
        to_work({**work(n, f"Abstract number {n}."), "publication_year": year}) for n in numbers
    ]


def test_add_works_writes_one_file_per_year_that_load_texts_can_read(tmp_path) -> None:
    batch = works(1, 2) + works(3, year=2025)
    assert add_works(tmp_path, batch) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["year=2025.parquet", "year=2026.parquet"]
    assert load_texts(tmp_path) == {w["doc_id"]: w["text"] for w in batch}


def test_add_works_keeps_what_is_there_and_adds_only_the_new(tmp_path) -> None:
    first, second = works(1, 2), works(2, 3)
    add_works(tmp_path, first)
    assert add_works(tmp_path, second) == 1
    assert add_works(tmp_path, second) == 0
    table = pq.read_table(tmp_path / "year=2026.parquet")
    assert table.num_rows == 3
    assert sorted(table["work_id"].to_pylist()) == ["W1", "W2", "W3"]


def test_the_file_holds_the_work_and_its_hash_beside_the_text(tmp_path) -> None:
    (w,) = works(5)
    add_works(tmp_path, [w])
    (row,) = pq.read_table(tmp_path / "year=2026.parquet").to_pylist()
    assert row == {
        "doc_id": w["doc_id"],
        "work_id": "W5",
        "title": "Work 5",
        "year": 2026,
        "publication_date": "2026-02-01",
        "language": "en",
        "type": "article",
        "doi": None,
        "content_hash": w["content_hash"],
        "text": w["text"],
    }


def test_a_work_listed_twice_in_one_batch_is_written_once(tmp_path) -> None:
    assert add_works(tmp_path, works(1) + works(1)) == 1


def test_no_temporary_file_is_left_behind(tmp_path) -> None:
    add_works(tmp_path, works(1))
    assert [p.name for p in tmp_path.iterdir()] == ["year=2026.parquet"]


def test_doc_ids_by_work_reads_every_year_file(tmp_path) -> None:
    batch = works(1, 2) + works(3, year=2025)
    add_works(tmp_path, batch)
    assert doc_ids_by_work(tmp_path) == {w["work_id"]: {w["doc_id"]} for w in batch}
    assert set(doc_ids_by_work(tmp_path)) == {"W1", "W2", "W3"}


def test_doc_ids_by_work_keeps_every_document_of_a_work(tmp_path) -> None:
    """A work whose abstract changed between two harvests is stored as two documents."""
    (old,) = works(1)
    new = {**old, "doc_id": "another-doc", "text": "A changed abstract."}
    add_works(tmp_path, [old, new])
    assert doc_ids_by_work(tmp_path) == {"W1": {old["doc_id"], "another-doc"}}


def test_doc_ids_by_work_of_a_missing_directory_is_empty(tmp_path) -> None:
    assert doc_ids_by_work(tmp_path / "nope") == {}
