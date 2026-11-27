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
from elusion.ask.embed import BATCH, BATCH_MAX_TOKENS, Embedder
from elusion.ask.models import EMBEDDING_DIM
from elusion.board.batch import held_back_ids, load_papers
from elusion.collect import db
from elusion.config import settings
from elusion.corpora import OPENALEX, corpus_for
from elusion.evaluate.gold import gold_path
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import PRICES, cost_usd


def estimate_tokens(text: str) -> int:
    """About three characters a token, plus one: the estimate behind the cost projection
    and the size of a batch."""
    return len(text) // 3 + 1


def batches(todo: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split `todo` into runs of at most `BATCH` texts and at most `BATCH_MAX_TOKENS`
    estimated tokens. One text always fits alone (a text is capped at 20,000 characters)."""
    out: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    tokens = 0
    for item in todo:
        size = estimate_tokens(item[1])
        if current and (len(current) >= BATCH or tokens + size > BATCH_MAX_TOKENS):
            out.append(current)
            current, tokens = [], 0
        current.append(item)
        tokens += size
    if current:
        out.append(current)
    return out


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
    if corpus_for(criterion) != OPENALEX:
        raise ValueError(
            f"criterion {criterion!r} is not about abstracts: ask searches abstracts only"
        )
    if embedder.model not in PRICES:
        raise ValueError(
            f"no price for embedding model {embedder.model!r}: add it to review/pricing.py "
            "(a price is never guessed); nothing was written"
        )
    engine = engine or db.make_engine(settings.database_url)
    index.ensure_schema(engine)
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

    # The spend guard comes before the first write: the texts still to embed are the indexed
    # rows with no vector for this model (less the ones about to be dropped) and the papers
    # not indexed yet.
    indexed = index.indexed_ids(engine)
    pending = [(d, t) for d, t in index.unembedded(engine, embedder.model) if d not in held]
    pending += [
        (p.doc_id, index.document_text(p.title or "", p.text))
        for p in papers
        if p.doc_id not in indexed
    ]
    projected = sum(cost_usd(embedder.model, estimate_tokens(t), 0) for _, t in pending)
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(pending)} texts is above "
            f"the limit of ${max_usd:.6f}; refusing to start"
        )

    removed = index.drop_abstracts(engine, held)
    added = index.add_abstracts(
        engine, papers, review_model=review_model, prompt_version=pair_key, threshold=threshold
    )

    todo = index.unembedded(engine, embedder.model)
    summary = {
        "screened_in": len(wanted),
        "held_back_removed": removed,
        "no_text": no_text,
        "rows_added": added,
        "already_embedded": index.count_indexed(engine) - len(todo),
        "embedded": 0,
        "input_tokens": 0,
        "cost_usd": 0.0,
    }
    for batch in batches(todo):
        result = embedder.embed([text for _, text in batch])
        bad = _unusable(batch, result.vectors)
        if bad:
            # The call was billed and left nothing to store: say what it cost, keep every
            # earlier batch (each was stored when it came back) and stop.
            get_run_logger().error(
                "embedding batch of %d texts (first %s) returned %s; billed %d tokens, $%.6f; "
                "summary so far: %s",
                len(batch),
                batch[0][0],
                bad,
                result.input_tokens,
                result.cost_usd,
                summary,
            )
            raise ValueError(f"the embedding API returned {bad}")
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
            served_model=result.model,
        )
        summary["embedded"] += len(batch)
        summary["input_tokens"] += result.input_tokens
        summary["cost_usd"] += result.cost_usd

    get_run_logger().info("index of %s: %s", pair_key, summary)
    return summary


def _unusable(batch: list[tuple[str, str]], vectors: list[list[float]]) -> str | None:
    """What is wrong with the API's answer to `batch`, or None when it can be stored."""
    if len(vectors) != len(batch):
        return f"{len(vectors)} vectors for {len(batch)} texts"
    for (doc_id, _), vector in zip(batch, vectors, strict=True):
        if len(vector) != EMBEDDING_DIM:
            return f"a vector of {len(vector)} numbers for {doc_id} (expected {EMBEDDING_DIM})"
    return None
