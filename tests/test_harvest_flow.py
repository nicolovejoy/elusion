"""The harvest flow, against the compose Postgres and a temporary data directory.

OpenAlex is a stand-in that hands back made-up works per search term, and Kafka is a
list. Every test uses search terms and work ids of its own and deletes its rows after.
"""

import dataclasses
import uuid
from datetime import UTC

import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa

from elusion.collect.custody import harvest_custody
from elusion.collect.db import documents, make_engine
from elusion.collect.openalex import OpenAlexError, to_work
from elusion.collect.terms import Term
from elusion.config import settings
from elusion.review.corpus import load_texts
from tests.conftest import requires_service
from tests.test_openalex import work

pytestmark = [
    requires_service("localhost", 5433, "postgres"),
    requires_service("localhost", 4200, "prefect server"),
]


class FakeOpenAlex:
    """Stands in for `OpenAlex`: the works each query returns, and a record of the asking."""

    pages = 0
    remaining_usd = None

    def __init__(self, by_query: dict[str, list[dict]]) -> None:
        self.by_query = by_query
        self.asked: list[tuple[Term, str, int | None]] = []

    def search(self, term: Term, from_date: str, max_pages: int | None = None):
        self.asked.append((term, from_date, max_pages))
        yield from self.by_query[term.query]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Two search terms of this test's own, a temporary data directory, and a clean-up."""
    import elusion.flows.harvest as mod

    tag = uuid.uuid4().hex[:8]
    broad, narrow = Term(f'"{tag} broad"', "stemmed"), Term(f'"{tag} narrow"', "exact")
    monkeypatch.setattr(mod, "TERMS", (broad, narrow))
    monkeypatch.setattr(mod, "settings", dataclasses.replace(settings, data_dir=tmp_path))
    engine = make_engine(settings.database_url)
    yield engine, broad, narrow, tmp_path
    with engine.begin() as conn:
        mine = harvest_custody.c.query.in_([broad.query, narrow.query])
        ids = conn.execute(sa.select(harvest_custody.c.doc_id).where(mine)).scalars().all()
        conn.execute(sa.delete(documents).where(documents.c.doc_id.in_(ids)))
        conn.execute(sa.delete(harvest_custody).where(mine))


def numbers(n: int) -> list[int]:
    """`n` work numbers no other test or run has used."""
    base = uuid.uuid4().int % 10**12 * 100
    return [base + i for i in range(n)]


def custody(engine, *terms: Term) -> list[dict]:
    stmt = (
        sa.select(harvest_custody)
        .where(harvest_custody.c.query.in_([t.query for t in terms]))
        .order_by(harvest_custody.c.id)
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(stmt)]


def test_a_harvest_stores_each_work_once_and_every_hit(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, data_dir = env
    a, b, c = (work(n) for n in numbers(3))
    client = FakeOpenAlex({broad.query: [a, b], narrow.query: [b, c]})
    published: list[tuple[str, dict]] = []

    summary = harvest(client=client, publish=published.extend)

    docs = {raw["id"]: to_work(raw) for raw in (a, b, c)}
    assert (summary.hits, summary.new_documents) == (4, 3)
    assert summary.skipped == 0
    # every hit is a custody row: b was returned by both terms
    rows = custody(engine, broad, narrow)
    assert [(r["query"], r["work_id"]) for r in rows] == [
        (broad.query, docs[a["id"]]["work_id"]),
        (broad.query, docs[b["id"]]["work_id"]),
        (narrow.query, docs[b["id"]]["work_id"]),
        (narrow.query, docs[c["id"]]["work_id"]),
    ]
    # each work is one document: its text on disk, its id published once
    assert load_texts(data_dir / "abstracts") == {d["doc_id"]: d["text"] for d in docs.values()}
    assert sorted(key for key, _ in published) == sorted(d["doc_id"] for d in docs.values())
    assert published[0][1] == {"doc_id": published[0][0], "year": 2026}


def test_the_custody_row_says_how_and_when_the_work_was_found(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, _ = env
    (a,) = (work(n) for n in numbers(1))
    summary = harvest(
        client=FakeOpenAlex({broad.query: [a], narrow.query: [a]}), publish=lambda m: None
    )

    stemmed, exact = custody(engine, broad, narrow)
    doc = to_work(a)
    for row in (stemmed, exact):
        assert row["harvest_id"] == summary.harvest_id
        assert row["route"] == "search"
        assert row["filter"] == "from_publication_date:2025-01-01,has_abstract:true"
        assert row["openalex_corpus"] == "core"
        assert (row["doc_id"], row["content_hash"]) == (doc["doc_id"], doc["content_hash"])
        assert row["harvested_at"].utcoffset() is not None
    assert (stemmed["search_form"], exact["search_form"]) == ("stemmed", "exact")
    # one harvest, one timestamp, in UTC
    assert stemmed["harvested_at"] == exact["harvested_at"]
    assert stemmed["harvested_at"].astimezone(UTC).utcoffset().total_seconds() == 0


def test_documents_rows_belong_to_the_openalex_corpus(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, _ = env
    (a,) = (work(n) for n in numbers(1))
    harvest(client=FakeOpenAlex({broad.query: [a], narrow.query: []}), publish=lambda m: None)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(documents).where(documents.c.doc_id == to_work(a)["doc_id"])
        ).one()
    assert (row.corpus, row.cik, row.company, row.year) == ("openalex", None, None, 2026)
    assert row.word_count == len(to_work(a)["text"].split())


def test_a_second_harvest_adds_custody_rows_and_nothing_else(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, data_dir = env
    a, b = (work(n) for n in numbers(2))
    found = {broad.query: [a, b], narrow.query: [b]}
    first = harvest(client=FakeOpenAlex(found), publish=lambda m: None)
    before = pq.read_table(data_dir / "abstracts" / "year=2026.parquet")
    published: list[tuple[str, dict]] = []

    second = harvest(client=FakeOpenAlex(found), publish=published.extend)

    assert (second.hits, second.new_documents) == (3, 0)
    assert published == []
    assert pq.read_table(data_dir / "abstracts" / "year=2026.parquet").equals(before)
    rows = custody(engine, broad, narrow)
    assert len(rows) == 6
    assert {r["harvest_id"] for r in rows} == {first.harvest_id, second.harvest_id}
    assert first.harvest_id != second.harvest_id


def test_a_changed_abstract_is_a_new_document_and_the_old_one_stays(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, data_dir = env
    (n,) = numbers(1)
    old, new = work(n, "The abstract as first served."), work(n, "The abstract, corrected.")
    harvest(client=FakeOpenAlex({broad.query: [old], narrow.query: []}), publish=lambda m: None)
    published: list[tuple[str, dict]] = []

    summary = harvest(
        client=FakeOpenAlex({broad.query: [new], narrow.query: []}), publish=published.extend
    )

    old_id, new_id = to_work(old)["doc_id"], to_work(new)["doc_id"]
    assert summary.new_documents == 1
    assert [key for key, _ in published] == [new_id]
    texts = load_texts(data_dir / "abstracts")
    assert texts[old_id] == to_work(old)["text"] and texts[new_id] == to_work(new)["text"]
    with engine.connect() as conn:
        kept = conn.execute(
            sa.select(documents.c.doc_id).where(documents.c.doc_id.in_([old_id, new_id]))
        )
        assert sorted(kept.scalars()) == sorted([old_id, new_id])


def test_a_work_without_an_abstract_is_counted_and_left_out(env) -> None:
    from elusion.flows.harvest import harvest

    engine, broad, narrow, _ = env
    a, b = (work(n) for n in numbers(2))
    b["abstract_inverted_index"] = None

    summary = harvest(
        client=FakeOpenAlex({broad.query: [a, b], narrow.query: []}), publish=lambda m: None
    )

    assert (summary.hits, summary.skipped, summary.new_documents) == (1, 1, 1)
    assert len(custody(engine, broad, narrow)) == 1


def test_each_term_is_asked_once_with_the_window_and_the_page_limit(env) -> None:
    from elusion.flows.harvest import harvest

    _, broad, narrow, _ = env
    client = FakeOpenAlex({broad.query: [], narrow.query: []})
    harvest(client=client, publish=lambda m: None, max_pages=2)
    assert client.asked == [(broad, "2025-01-01", 2), (narrow, "2025-01-01", 2)]


def test_no_key_and_no_client_is_refused_before_anything_is_written(env, monkeypatch) -> None:
    import elusion.flows.harvest as mod

    engine, broad, narrow, _ = env
    monkeypatch.setattr(mod, "settings", dataclasses.replace(mod.settings, openalex_api_key=None))
    with pytest.raises(OpenAlexError, match="OPENALEX_API_KEY"):
        mod.harvest(publish=lambda m: None)
    assert custody(engine, broad, narrow) == []
