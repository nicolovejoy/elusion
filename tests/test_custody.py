"""The counts the report reads from `harvest_custody`, against the compose Postgres.

Every test writes rows under queries carrying its own tag and deletes them after.
"""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from elusion.collect import custody
from elusion.collect.custody import harvest_custody
from elusion.collect.db import make_engine
from elusion.config import settings
from tests.conftest import requires_service

pytestmark = requires_service("localhost", 5433, "postgres")


@pytest.fixture
def env():
    engine = make_engine(settings.database_url)
    custody.ensure_schema(engine)
    tag = uuid.uuid4().hex[:8]
    yield engine, tag
    with engine.begin() as conn:
        conn.execute(sa.delete(harvest_custody).where(harvest_custody.c.query.like(f"%{tag}%")))


def hit(tag: str, harvest: str, query: str, work: str, route: str = "search") -> dict:
    return {
        "harvest_id": f"{tag}{harvest}",
        "harvested_at": datetime.now(UTC),
        "route": route,
        "query": f"{tag} {query}",
        "search_form": "stemmed" if route == "search" else None,
        "sample_seed": None if route == "search" else 7,
        "filter": "f",
        "openalex_corpus": "core",
        "work_id": f"W{tag}{work}",
        "doc_id": uuid.uuid4().hex[:16],
        "content_hash": "0" * 64,
    }


def test_record_keeps_one_row_for_a_work_a_query_returned_twice(env) -> None:
    engine, tag = env
    row = hit(tag, "h1", "term", "1")
    assert custody.record(engine, [row, dict(row)]) == 1
    assert custody.record(engine, [row]) == 0


def test_works_per_query_counts_a_work_once_however_many_harvests_found_it(env) -> None:
    engine, tag = env
    custody.record(
        engine,
        [
            hit(tag, "h1", "broad", "1"),
            hit(tag, "h1", "broad", "2"),
            hit(tag, "h2", "broad", "1"),  # the second harvest finds work 1 again
            hit(tag, "h2", "broad", "3"),
            hit(tag, "h1", "narrow", "1"),
            hit(tag, "h1", "anchor", "4", route="citation"),
        ],
    )
    mine = [row for row in custody.works_per_query(engine) if tag in row.query]
    assert [(r.route, r.query, r.search_form, r.works) for r in mine] == [
        ("search", f"{tag} broad", "stemmed", 3),
        ("search", f"{tag} narrow", "stemmed", 1),
        ("citation", f"{tag} anchor", None, 1),
    ]


def test_seed_works_says_which_seeds_a_search_term_also_returned(env) -> None:
    engine, tag = env
    custody.record(
        engine,
        [
            hit(tag, "h1", "broad", "1"),
            hit(tag, "h1", "anchor-a", "1", route="citation"),  # also found by a term
            hit(tag, "h1", "anchor-a", "2", route="citation"),  # found by citation only
            hit(tag, "h1", "anchor-b", "2", route="citation"),  # the same seed, second anchor
        ],
    )
    mine = {w: s for w, s in custody.seed_works(engine).items() if tag in w}
    assert mine == {
        f"W{tag}1": custody.Seed(anchors=(f"{tag} anchor-a",), by_search=True),
        f"W{tag}2": custody.Seed(anchors=(f"{tag} anchor-a", f"{tag} anchor-b"), by_search=False),
    }
