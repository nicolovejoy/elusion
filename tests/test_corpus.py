from elusion.collect.parquet import write_year, year_path
from elusion.review.corpus import load_texts


def _doc(doc_id: str, year: int, text: str) -> dict:
    return {
        "doc_id": doc_id,
        "cik": "1",
        "company": None,
        "year": year,
        "paragraph_index": 0,
        "text": text,
    }


def test_load_texts_reads_every_year(tmp_path) -> None:
    write_year([_doc("a", 2019, "alpha"), _doc("b", 2019, "beta")], year_path(tmp_path, 2019))
    write_year([_doc("c", 2020, "gamma")], year_path(tmp_path, 2020))
    (tmp_path / "unrelated.parquet").write_bytes(b"not parquet")
    assert load_texts(tmp_path) == {"a": "alpha", "b": "beta", "c": "gamma"}
