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
