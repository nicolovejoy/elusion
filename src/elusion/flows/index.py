"""Prefect flow `build_index`: put the screened-in abstracts in Postgres and embed them.

The index is the pile `ask` searches. It holds the abstracts that one review (a model and
a prompt version) scored at or above a threshold, minus the papers held back as the gold
set, each with its full-text column and one embedding per embedding model. An *embedding*
is a list of numbers a model produces for a text, so that texts with similar meaning get
nearby lists; it is the part of the index that costs money.

Three rules from the project hold here. The gold set never enters the index: its ids (the
gold CSV and the judge-refusals file) are taken out, and a paper that was indexed before
the gold set grew is removed again. No label is read, only the list of ids. A (paper,
embedding model) with a vector is never sent to the model again, so a second run costs
nothing. And before the first call the flow projects the cost of every text still to embed
and refuses to start when it is above the limit (the review flow's `BudgetExceeded`).

The texts go out in batches, and each batch is stored before the next is sent, so a crash
keeps every finished batch and the rerun embeds only the rest.
"""

from __future__ import annotations

from pathlib import Path

from prefect import flow, get_run_logger
from sqlalchemy.engine import Engine

from elusion.ask import index
from elusion.ask.embed import BATCH, Embedder
from elusion.board.batch import held_back_ids, load_papers
from elusion.collect import db
from elusion.config import settings
from elusion.corpora import OPENALEX, corpus_for
from elusion.evaluate.gold import gold_path
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import cost_usd


@flow(name="build_index")
def build_index(
    criterion: str,
    embedder: Embedder,
    *,
    review_model: str,
    prompt_version: str = "v1",
    threshold: int = 3,
    engine: Engine | None = None,
    abstracts_dir: Path | None = None,
    gold_csv: Path | None = None,
    max_usd: float | None = None,
) -> dict:
    """Index and embed the screened-in abstracts of `criterion`. Returns the counts."""
    engine = engine or db.make_engine(settings.database_url)
    index.ensure_schema(engine)
    if corpus_for(criterion) != OPENALEX:
        raise ValueError(
            f"criterion {criterion!r} is not about abstracts: ask searches abstracts only"
        )
    max_usd = settings.max_run_usd if max_usd is None else max_usd
    pair_key = f"{criterion}/{prompt_version}"

    wanted = set(index.screened_in_ids(engine, review_model, pair_key, threshold))
    if not wanted:
        raise ValueError(
            f"no score of {review_model} under {pair_key} is at or above {threshold}: "
            "check the model and the prompt version"
        )
    held = held_back_ids(gold_csv or gold_path(criterion, settings.data_dir))

    existing = index.indexed_pair(engine)
    this_run = (review_model, pair_key, threshold)
    if existing is not None and existing != this_run:
        raise index.IndexPairMismatch(
            f"the index was built from {existing} and this run is {this_run}; "
            "refusing to mix two screenings in one index"
        )

    keep = wanted - held
    papers = [
        p
        for p in load_papers(abstracts_dir or OPENALEX.text_dir(settings.data_dir))
        if p.doc_id in keep
    ]
    no_text = len(keep) - len(papers)

    removed = index.drop_abstracts(engine, held)
    added = index.add_abstracts(
        engine, papers, review_model=review_model, prompt_version=pair_key, threshold=threshold
    )

    todo = index.unembedded(engine, embedder.model)
    already = index.count_indexed(engine) - len(todo)
    projected = sum(cost_usd(embedder.model, len(text) // 3 + 1, 0) for _, text in todo)
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(todo)} texts is above "
            f"the limit of ${max_usd:.6f}; refusing to start"
        )

    summary = {
        "screened_in": len(wanted),
        "held_back_removed": removed,
        "no_text": no_text,
        "rows_added": added,
        "already_embedded": already,
        "embedded": 0,
        "input_tokens": 0,
        "cost_usd": 0.0,
    }
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        result = embedder.embed([text for _, text in batch])
        if result.model != embedder.model:
            get_run_logger().warning(
                "the pinned embedding model is %s but the API served %s",
                embedder.model,
                result.model,
            )
        index.add_vectors(
            engine,
            embedder.model,
            {doc_id: v for (doc_id, _), v in zip(batch, result.vectors, strict=True)},
        )
        summary["embedded"] += len(batch)
        summary["input_tokens"] += result.input_tokens
        summary["cost_usd"] += result.cost_usd

    get_run_logger().info("index of %s: %s", pair_key, summary)
    return summary
