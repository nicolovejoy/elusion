import uuid

import sqlalchemy as sa

from elusion.collect.db import (
    count_documents,
    documents,
    ensure_schema,
    make_engine,
    upsert_documents,
)
from elusion.config import settings
from tests.conftest import requires_service

pytestmark = requires_service("localhost", 5433, "postgres")


def test_upsert_is_idempotent() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    cik = uuid.uuid4().hex[:10]
    docs = [
        {
            "doc_id": uuid.uuid4().hex[:16],
            "cik": cik,
            "company": None,
            "year": 1999,
            "paragraph_index": i,
            "text": "a b c",
        }
        for i in range(3)
    ]
    try:
        assert upsert_documents(engine, docs) == 3
        assert upsert_documents(engine, docs) == 0
        assert count_documents(engine, 1999) >= 3
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(documents).where(documents.c.cik == cik))


def test_company_counts_sorted_by_count_then_name() -> None:
    from elusion.collect.db import company_counts

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    cik = uuid.uuid4().hex[:10]
    names = ["Zeta Co", "Acme Corp", "Acme Corp", "Beta Inc", None, "Ignored Ltd"]
    docs = [
        {
            "doc_id": uuid.uuid4().hex[:16],
            "cik": cik,
            "company": name,
            "year": 1999,
            "paragraph_index": i,
            "text": "a b c",
        }
        for i, name in enumerate(names)
    ]
    try:
        upsert_documents(engine, docs)
        ids = [d["doc_id"] for d in docs[:5]]
        assert company_counts(engine, ids) == [
            ("Acme Corp", 2),
            ("(no company name)", 1),
            ("Beta Inc", 1),
            ("Zeta Co", 1),
        ]
        assert company_counts(engine, []) == []
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(documents).where(documents.c.cik == cik))


def _doc(year: int, **extra) -> dict:
    return {
        "doc_id": uuid.uuid4().hex[:16],
        "year": year,
        "paragraph_index": 0,
        "word_count": 3,
        **extra,
    }


def test_a_row_without_a_corpus_is_a_10k_row_and_an_abstract_needs_no_filer() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    old_style = _doc(1998, cik="1")  # what every row written before the column looked like
    abstract = _doc(1998, corpus="openalex")  # no cik: an abstract has no filer
    ids = [old_style["doc_id"], abstract["doc_id"]]
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(documents), [old_style])
            conn.execute(sa.insert(documents), [abstract])
            found = dict(
                conn.execute(
                    sa.select(documents.c.doc_id, documents.c.corpus).where(
                        documents.c.doc_id.in_(ids)
                    )
                ).all()
            )
        assert found == {old_style["doc_id"]: "edgar", abstract["doc_id"]: "openalex"}
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(documents).where(documents.c.doc_id.in_(ids)))


def test_count_documents_counts_one_corpus_when_two_share_a_year() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    rows = [_doc(1997, cik="1"), _doc(1997, cik="1"), _doc(1997, corpus="openalex")]
    ids = [r["doc_id"] for r in rows]
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(documents), rows[:2])
            conn.execute(sa.insert(documents), rows[2:])
        assert count_documents(engine, 1997, corpus="edgar") == 2
        assert count_documents(engine, 1997, corpus="openalex") == 1
        assert count_documents(engine, 1997) == 3  # no corpus: every row of the year
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(documents).where(documents.c.doc_id.in_(ids)))
