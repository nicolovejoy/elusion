"""Prefect flow `harvest`: OpenAlex search terms -> abstracts in Parquet, Postgres, Kafka.

The second corpus's `collect`. For each search term in `collect.terms` it pages through
OpenAlex, rebuilds each abstract, and stores what it found in four places:

1. `data/abstracts/year=*.parquet`: the text, for a document not seen before.
2. the `abstracts.ingested` topic: the id of each new document, which is how the review
   flow finds its work.
3. `documents`: one metadata row for each new document, under the corpus `openalex`.
4. `harvest_custody`: one row for every hit, new document or not.

A document is *new* when `documents` has no row for its id. The id is made from the
work's id and a hash of its text, so the same work with the same abstract is never new
twice. That is what makes a re-run safe: it adds this harvest's custody rows and
nothing else, and no abstract is published, and so reviewed, a second time.

The order of the four writes is chosen for a crash between any two of them. Text goes
first, so an id is never published before its text can be read. The `documents` row
goes after the topic, because that row is the "already done" mark: if the run dies
between the two, the next run publishes those ids again. A repeated message costs
nothing, since the review flow skips an id that already has a score; a lost one would
leave an abstract unreviewed with nothing to show it.

Each term is fetched and stored before the next is asked for, so a failure on the
twelfth term keeps the first eleven.

After the terms come the *citation samples*: for each anchor paper in `collect.terms`,
a seeded random sample of the works that cite it, stored the same way with the route
`citation` in place of `search`. An anchor is sampled once. A later harvest finds its
`citation` rows and does not draw again, so the seeds stay the ones first drawn even
if OpenAlex would now answer the same seed differently.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from elusion.collect import abstracts, custody, db, kafka
from elusion.collect.openalex import OpenAlex, Work, citing_filter, search_filter, to_work
from elusion.collect.terms import (
    ANCHORS,
    FROM_DATE,
    OPENALEX_CORPUS,
    SAMPLE_SEED,
    SEEDS_PER_ANCHOR,
    TERMS,
    Anchor,
    Term,
)
from elusion.config import settings
from elusion.corpora import OPENALEX

Publish = Callable[[list[tuple[str, dict]]], Any]


@dataclass(frozen=True)
class HarvestSummary:
    harvest_id: str
    harvested_at: datetime
    hits: int  # search custody rows written: works returned, counted once per term
    seeds: int  # citation custody rows written: works drawn from those citing an anchor
    new_documents: int  # abstracts not seen before, each published to the topic once
    skipped: int  # works returned without an abstract or a year
    pages: int  # requests OpenAlex answered, each one metered

    def __str__(self) -> str:
        return (
            f"harvest {self.harvest_id} at {self.harvested_at:%Y-%m-%d %H:%M:%S} UTC: "
            f"{self.hits} search hits and {self.seeds} citation seeds over {self.pages} "
            f"pages, {self.new_documents} new documents "
            f"stored and published, {self.skipped} skipped (no abstract or no year)"
        )


# NO_CACHE: Prefect 3 would otherwise hash the task inputs (the client) for caching.
# The client already waits out a 429 and retries a server error; a task retry is for
# whatever is left, and it starts the term's paging again from the first page.
@task(cache_policy=NO_CACHE, retries=2, retry_delay_seconds=60)
def fetch_term(client: Any, term: Term, max_pages: int | None) -> tuple[list[Work], int]:
    """Every work `term` returns, as documents, and how many works had to be skipped."""
    works: list[Work] = []
    skipped = 0
    for raw in client.search(term, FROM_DATE, max_pages):
        work = to_work(raw)
        if work is None:
            skipped += 1
        else:
            works.append(work)
    get_run_logger().info(
        "%s works, %s skipped, $%s of today's allowance left: %s [%s]",
        len(works),
        skipped,
        client.remaining_usd,
        term.query,
        term.form,
    )
    return works, skipped


@dataclass(frozen=True)
class Origin:
    """How a batch of works was found: what every one of its custody rows says."""

    route: str  # search | citation
    query: str  # the search string, or the anchor's work id
    filter: str
    search_form: str | None = None
    sample_seed: int | None = None


@task(cache_policy=NO_CACHE, retries=2, retry_delay_seconds=60)
def fetch_seeds(client: Any, anchor: Anchor) -> tuple[list[Work], int]:
    """The seeded sample of works citing `anchor`, as documents, and how many were skipped."""
    raws = client.citing_sample(anchor.work_id, FROM_DATE, SEEDS_PER_ANCHOR, SAMPLE_SEED)
    works = [w for w in map(to_work, raws) if w is not None]
    get_run_logger().info(
        "%s seeds, %s skipped: works citing %s (%s)",
        len(works),
        len(raws) - len(works),
        anchor.work_id,
        anchor.label,
    )
    return works, len(raws) - len(works)


@task(cache_policy=NO_CACHE)
def store_hits(
    works: list[Work], origin: Origin, harvest_id: str, harvested_at: datetime, publish: Publish
) -> tuple[int, int]:
    """Store one query's works; returns (custody rows written, new documents)."""
    engine = db.make_engine(settings.database_url)
    by_id = {w["doc_id"]: w for w in works}  # a cursor can serve one work twice
    known = db.known_doc_ids(engine, by_id)
    new = [w for doc_id, w in by_id.items() if doc_id not in known]

    abstracts.add_works(OPENALEX.text_dir(settings.data_dir), new)
    publish([(w["doc_id"], {"doc_id": w["doc_id"], "year": w["year"]}) for w in new])
    db.upsert_documents(
        engine,
        (
            {
                "doc_id": w["doc_id"],
                "cik": None,
                "company": None,
                "year": w["year"],
                "paragraph_index": 0,
                "text": w["text"],
            }
            for w in new
        ),
        corpus=OPENALEX.name,
    )
    hits = custody.record(
        engine,
        (
            {
                "harvest_id": harvest_id,
                "harvested_at": harvested_at,
                "route": origin.route,
                "query": origin.query,
                "search_form": origin.search_form,
                "sample_seed": origin.sample_seed,
                "filter": origin.filter,
                "openalex_corpus": OPENALEX_CORPUS,
                "work_id": w["work_id"],
                "doc_id": w["doc_id"],
                "content_hash": w["content_hash"],
            }
            for w in by_id.values()
        ),
    )
    get_run_logger().info(
        "%s %s rows recorded, %s new documents: %s", hits, origin.route, len(new), origin.query
    )
    return hits, len(new)


def _publish_to_kafka(messages: list[tuple[str, dict]]) -> int:
    return kafka.publish_doc_ids(settings.kafka_bootstrap, messages, OPENALEX.topic)


@flow(name="harvest")
def harvest(
    max_pages: int | None = None,
    seeds_only: bool = False,
    client: Any = None,
    publish: Publish | None = None,
) -> HarvestSummary:
    """Harvest every term in `collect.terms`, then each anchor's sample. Safe to re-run.

    `max_pages` caps the pages asked per term, for a first look at a few hundred works.
    `seeds_only` skips the searches and draws only the citation samples still missing.
    `client` and `publish` are for the tests, which pass stand-ins for OpenAlex and Kafka.
    """
    client = client or OpenAlex(settings.openalex_api_key)  # refuses to exist without a key
    if publish is None:
        kafka.ensure_topic(settings.kafka_bootstrap, OPENALEX.topic)
        publish = _publish_to_kafka
    engine = db.make_engine(settings.database_url)
    db.ensure_schema(engine)
    custody.ensure_schema(engine)

    harvest_id = uuid.uuid4().hex
    harvested_at = datetime.now(UTC)
    hits = seeds = new_documents = skipped = 0
    for term in () if seeds_only else TERMS:
        works, term_skipped = fetch_term(client, term, max_pages)
        origin = Origin("search", term.query, search_filter(FROM_DATE), search_form=term.form)
        term_hits, term_new = store_hits(works, origin, harvest_id, harvested_at, publish)
        hits += term_hits
        new_documents += term_new
        skipped += term_skipped
    drawn = custody.drawn_anchors(engine)
    for anchor in ANCHORS:
        if anchor.work_id in drawn:
            continue
        works, anchor_skipped = fetch_seeds(client, anchor)
        origin = Origin(
            "citation",
            anchor.work_id,
            citing_filter(anchor.work_id, FROM_DATE),
            sample_seed=SAMPLE_SEED,
        )
        anchor_seeds, anchor_new = store_hits(works, origin, harvest_id, harvested_at, publish)
        seeds += anchor_seeds
        new_documents += anchor_new
        skipped += anchor_skipped
    summary = HarvestSummary(
        harvest_id=harvest_id,
        harvested_at=harvested_at,
        hits=hits,
        seeds=seeds,
        new_documents=new_documents,
        skipped=skipped,
        pages=client.pages,
    )
    get_run_logger().info("%s", summary)
    return summary
