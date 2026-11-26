"""Prefect flow `first_read`: a pinned model reads each matching abstract against a concept.

A concept is a wording plus search terms, stored in the append-only `concept_log`. This
flow takes the papers whose abstract contains a search term (a plain word search, no
model), and for each one the reader model has not yet read it asks: does this paper hold
the concept, and which passage shows it? The answer is a *reading*, one row in the log.

Three rules from the project hold here. Every call carries the pinned model id and the
prompt version `first-read/v1`, and both are stored on the row. A (concept version, paper,
model, prompt version) with a reading row is never sent to a model again, whatever that
reading was, a failed one included: a paper is not paid for twice. And before the first
call the flow projects the whole run's cost and refuses to start when it is above the
limit (the review flow's `BudgetExceeded`).

A reading's quote must be copied from the abstract (`log.add_reading` checks). A model
that invents or edits its quote is not trusted: the row is stored as `failed`, with what
the model said kept in `detail` so the owner can see it.
"""

from __future__ import annotations

from pathlib import Path

from prefect import flow, get_run_logger
from sqlalchemy.engine import Engine

from elusion.board import log
from elusion.board.batch import load_papers, word_search
from elusion.collect import db
from elusion.config import settings
from elusion.corpora import OPENALEX
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import Reader, ReviewError, reader_message

PROMPT = "first-read"
# Projection per paper: about three characters a token for the abstract, a prompt and
# message frame of 600 tokens, and a 300-token answer.
FRAME_TOKENS = 600
ANSWER_TOKENS = 300


@flow(name="first_read")
def first_read(
    concept: str,
    reader: Reader,
    *,
    engine: Engine | None = None,
    abstracts_dir: Path | None = None,
    prompt_version: str = "v1",
    max_usd: float | None = None,
) -> dict:
    """Read every unread paper matching `concept`'s search terms. Returns the counts."""
    engine = engine or db.make_engine(settings.database_url)
    log.ensure_schema(engine)
    abstracts_dir = abstracts_dir or OPENALEX.text_dir(settings.data_dir)
    max_usd = settings.max_run_usd if max_usd is None else max_usd

    entry = log.current_concept(engine, concept)
    terms = entry.detail.get("search_terms")
    if not terms:
        raise ValueError(f"concept {concept!r} has no search_terms in its detail")
    prompt = load_prompt(PROMPT, prompt_version)

    matched = word_search(load_papers(abstracts_dir), terms)
    done = log.read_doc_ids(engine, concept, entry.concept_version, reader.model, prompt.version)
    unread = [p for p in matched if p.doc_id not in done]

    projected = sum(
        cost_usd(reader.model, len(p.text) // 3 + FRAME_TOKENS, ANSWER_TOKENS) for p in unread
    )
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(unread)} papers is above "
            f"the limit of ${max_usd:.6f}; refusing to start"
        )

    summary = {
        "matched": len(matched),
        "already_read": len(matched) - len(unread),
        "read": 0,
        "holds": 0,
        "does_not_hold": 0,
        "failed": 0,
        "cost_usd": 0.0,
    }
    for paper in unread:
        try:
            verdict, cost = _read_one(engine, reader, prompt, entry, paper)
        except Exception:
            # Any other error (a dropped connection, a bad request) is not a reading: no row is
            # written, so the next run tries this paper again. The spend so far is reported.
            get_run_logger().error(
                "first read of %s stopped on %s; summary so far: %s",
                concept,
                paper.doc_id,
                summary,
            )
            raise
        summary["read"] += 1
        summary[verdict] += 1
        summary["cost_usd"] += cost

    get_run_logger().info("first read of %s: %s", concept, summary)
    return summary


def _store(engine, stored, verdict, quote, rationale, detail) -> str:
    log.add_reading(
        engine, verdict=verdict, quote=quote, rationale=rationale, detail=detail, **stored
    )
    return verdict


def _read_one(engine, reader, prompt, entry, paper) -> tuple[str, float]:
    """Send one paper to the reader and store the reading. Returns (verdict, cost in USD)."""
    stored = {
        "concept": entry.concept,
        "version": entry.concept_version,
        "doc_id": paper.doc_id,
        "model": reader.model,
        "prompt_version": prompt.version,
        "text": paper.text,
    }
    try:
        first = reader.read(prompt.text, reader_message(entry.body, paper.text))
    except ReviewError as exc:
        cost = cost_usd(
            reader.model,
            exc.input_tokens,
            exc.output_tokens,
            exc.cache_creation_input_tokens,
            exc.cache_read_input_tokens,
        )
        detail = {
            "stop_reason": exc.stop_reason,
            "input_tokens": exc.input_tokens,
            "output_tokens": exc.output_tokens,
            "cost_usd": cost,
        }
        return _store(engine, stored, "failed", None, "", detail), cost
    reading = first.reading
    detail = {
        "answer": reading.answer,
        "served_model": first.model,
        "input_tokens": first.input_tokens,
        "output_tokens": first.output_tokens,
        "cost_usd": first.cost_usd,
    }
    verdict = "holds" if reading.holds else "does_not_hold"
    try:
        return _store(engine, stored, verdict, reading.quote, reading.rationale, detail), (
            first.cost_usd
        )
    except log.QuoteNotVerbatim:
        detail.update(
            stop_reason="quote_not_verbatim",
            model_holds=reading.holds,
            model_quote=reading.quote,
        )
        return _store(engine, stored, "failed", None, reading.rationale, detail), first.cost_usd
