from pathlib import Path

from elusion.collect.parquet import Document, read_year, write_year, year_path


def _doc(i: int) -> Document:
    return {
        "doc_id": f"{i:016x}",
        "cik": "88121",
        "company": None if i % 2 else "Seaboard",
        "year": 2019,
        "paragraph_index": i,
        "text": f"paragraph {i}",
    }


def test_roundtrip_and_overwrite(tmp_path: Path) -> None:
    path = year_path(tmp_path, 2019)
    assert write_year([_doc(i) for i in range(3)], path) == 3
    assert write_year([_doc(0)], path) == 1  # second write replaces, not appends
    rows = read_year(path)
    assert rows == [_doc(0)]
    assert rows[0]["company"] == "Seaboard"
