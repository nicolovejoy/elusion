"""The gold questions: 30 questions with a known answer (or none) to test `ask` against.

The idea in plain words. To measure whether retrieval finds the right abstract, we need
questions whose right abstract is known in advance. 25 of the 30 are answerable: each was
written from one screened-in abstract, its *source*, and the test is whether that abstract
comes back when the question is searched. The other 5 cannot be answered from the corpus at
all, and the test is whether `ask` refuses them.

How the file is made, in three steps with the owner's hands in the middle:

1. `draft_questions` draws 40 abstracts from the index with a fixed seed and asks one model
   call each to write one question that the abstract answers. Each question is appended to a
   draft CSV the moment it arrives, so a crash keeps the paid ones.
2. The owner edits the draft CSV by hand: sets `keep` to `no` on the questions he strikes
   (down to 25), rewrites the `question` of any he wants to word himself, and adds five rows
   `u1` to `u5` of `kind` `unanswerable` that he writes himself.
3. `finalize_questions` validates the edited draft and writes the final questions file, once
   (it never overwrites). `read_questions` reads that file back for the evaluation.

The gold rule. A question's source document id lives only in these two files under
`data/gold/`. It never enters a prompt (the model sees the abstract text and nothing else:
no id, no title), the index, or anything passed to `retrieve` or `ask`; this module does not
import either, and a test keeps it that way. The draw comes from the index, which already
leaves out every abstract held back for the gold labels, so no source is a labelled one.

A draft call that ends without a usable question (the provider's refusal, a cut-off, an
empty string) was billed, so it still holds its key, the source id: its row is written with
`keep` `no`, an empty `question`, and the reason in `drafted_question`. It is never read as a
question and is not tried again; delete its row from the draft file to draw that abstract
afresh.
"""

from __future__ import annotations

import csv
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from elusion.ask.index import abstract_index
from elusion.ask.models import DRAFT_MODEL
from elusion.config import settings
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import Completer, ReviewError

logger = logging.getLogger(__name__)

__all__ = ["DRAFT_MODEL"]  # re-exported: the evaluation names the drafting model from here

DRAFT_N = 40
ANSWERABLE_N = 25
UNANSWERABLE_N = 5
QUESTION_SEED = 20261126  # the day it was chosen, as a number, like the other seeds here

PROMPT = "ask"
PROMPT_VERSION = "draft-v1"
# 16,000: thinking cannot be turned off on claude-opus-5-5 and its tokens count against
# `max_tokens` (Anthropic docs, read 2026-11-26), so a small limit can be spent on thinking.
DRAFT_MAX_TOKENS = 16000
# For the spend guard: the framing around an abstract and the answer, in tokens.
_FRAME_TOKENS = 400
_ANSWER_TOKENS = 150

DRAFT_COLUMNS = [
    "question_id",
    "kind",
    "source_doc_id",
    "drafted_question",
    "question",
    "keep",
    "draft_model",
    "draft_prompt_version",
    "drafted_at",
]
FINAL_COLUMNS = [
    "question_id",
    "kind",
    "source_doc_id",
    "question",
    "origin",
    "draft_model",
    "draft_prompt_version",
    "finalized_at",
]
KINDS = ("answerable", "unanswerable")


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


@dataclass(frozen=True)
class Question:
    question_id: str
    kind: str  # "answerable" or "unanswerable"
    source_doc_id: str  # "" for unanswerable
    question: str
    origin: str  # "drafted", "rewritten" or "owner"
    draft_model: str
    draft_prompt_version: str


def draft_path(criterion: str, data_dir: Path) -> Path:
    """data/gold/<criterion>.questions-draft.csv, beside the other gold files."""
    return data_dir / "gold" / f"{criterion}.questions-draft.csv"


def questions_path(criterion: str, data_dir: Path) -> Path:
    """data/gold/<criterion>.questions.csv: the final file."""
    return data_dir / "gold" / f"{criterion}.questions.csv"


def draft_message(text: str) -> str:
    """The user turn of a draft call: the abstract's text, and nothing else."""
    return f"ABSTRACT:\n{text}"


def draw_sources(engine: Engine, n: int = DRAFT_N, seed: int = QUESTION_SEED) -> list[str]:
    """`n` doc ids drawn from the index: its sorted ids, then `random.Random(seed).sample`.

    The index already leaves out the papers held back as the gold set. Raises `ValueError`
    when the index holds fewer than `n` abstracts."""
    stmt = sa.select(abstract_index.c.doc_id).order_by(abstract_index.c.doc_id)
    with engine.connect() as conn:
        ids = [row.doc_id for row in conn.execute(stmt)]
    if len(ids) < n:
        raise ValueError(f"the index holds {len(ids)} abstracts and the draw needs {n}")
    return random.Random(seed).sample(ids, n)


def _texts(engine: Engine, doc_ids: list[str]) -> dict[str, str]:
    """The abstract text of each id: the text column only, never the title."""
    if not doc_ids:
        return {}
    stmt = sa.select(abstract_index.c.doc_id, abstract_index.c.text).where(
        abstract_index.c.doc_id.in_(doc_ids)
    )
    with engine.connect() as conn:
        return {row.doc_id: row.text for row in conn.execute(stmt)}


def _check_header(header: list[str] | None, columns: list[str], path: Path) -> None:
    if header != columns:
        raise ValueError(f"{path} has the columns {header}; expected {columns}")


def _read_draft_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        _check_header(reader.fieldnames, DRAFT_COLUMNS, path)
        return list(reader)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _append_row(path: Path, row: dict[str, str]) -> None:
    """Append one row and flush it. The header is written first for a new file, and a final
    newline is added first when a hand edit left the file without one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    missing_newline = False
    if not fresh:
        with path.open("rb") as f:
            f.seek(-1, 2)
            missing_newline = f.read(1) != b"\n"
    with path.open("a", encoding="utf-8", newline="") as f:
        if missing_newline:
            f.write("\n")
        writer = csv.DictWriter(f, fieldnames=DRAFT_COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerow(row)
        f.flush()


def draft_questions(
    engine: Engine,
    drafter: Completer,
    path: Path,
    *,
    n: int = DRAFT_N,
    seed: int = QUESTION_SEED,
    max_usd: float | None = None,
) -> dict:
    """Draft one question from each of `n` drawn abstracts into the draft CSV at `path`.

    A source already in the file is not drafted again (its row, even a failed one, holds
    its key). Raises `BudgetExceeded` before the first call when the abstracts still to
    draft are projected to cost more than `max_usd` (default `ELUSION_MAX_RUN_USD`).
    Returns `{"drawn", "already_drafted", "drafted", "failed", "cost_usd"}`.
    """
    max_usd = settings.max_run_usd if max_usd is None else max_usd
    prompt = load_prompt(PROMPT, PROMPT_VERSION)
    rows = _read_draft_rows(path)
    have = {r["source_doc_id"] for r in rows if r["kind"] == "answerable"}
    summary = {"drawn": 0, "already_drafted": len(have), "drafted": 0, "failed": 0, "cost_usd": 0.0}
    if len(have) >= n:
        return summary  # the file is full: nothing is drawn and nothing is called

    sources = draw_sources(engine, n, seed)
    summary["drawn"] = len(sources)
    todo = [(position, d) for position, d in enumerate(sources, start=1) if d not in have]
    texts = _texts(engine, [d for _, d in todo])

    projected = sum(
        cost_usd(drafter.model, len(texts[d]) // 3 + _FRAME_TOKENS, _ANSWER_TOKENS)
        for _, d in todo
        if d in texts
    )
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(todo)} abstracts is above "
            f"the limit of ${max_usd:.6f}; refusing to start"
        )

    for position, source in todo:
        if source not in texts:
            continue  # indexed when drawn, gone now: nothing to ask about
        row = {
            "question_id": f"q{position:02d}",
            "kind": "answerable",
            "source_doc_id": source,
            "draft_model": drafter.model,
            "draft_prompt_version": prompt.version,
            "drafted_at": _now(),
        }
        try:
            done = drafter.complete(
                prompt.text, draft_message(texts[source]), Draft, max_tokens=DRAFT_MAX_TOKENS
            )
        except ReviewError as exc:
            # No usable answer, but the call was billed: it holds its key as a struck row.
            summary["cost_usd"] += cost_usd(
                drafter.model,
                exc.input_tokens,
                exc.output_tokens,
                exc.cache_creation_input_tokens,
                exc.cache_read_input_tokens,
            )
            summary["failed"] += 1
            _append_row(
                path,
                {
                    **row,
                    "drafted_question": f"[no question: stop_reason={exc.stop_reason}]",
                    "question": "",
                    "keep": "no",
                },
            )
            continue
        summary["cost_usd"] += done.cost_usd
        if done.model != drafter.model:
            logger.warning(
                "the pinned model is %s but the API served %s", drafter.model, done.model
            )
        question = " ".join(done.parsed.question.split())
        if not question:
            summary["failed"] += 1
            _append_row(
                path,
                {
                    **row,
                    "drafted_question": "[no question: the model's question was empty]",
                    "question": "",
                    "keep": "no",
                },
            )
            continue
        summary["drafted"] += 1
        _append_row(
            path, {**row, "drafted_question": question, "question": question, "keep": "yes"}
        )
    return summary


def finalize_questions(draft_csv: Path, out_csv: Path, *, now: datetime | None = None) -> dict:
    """Validate the owner's edited draft and write the final questions file, once.

    Raises `ValueError` (naming the line, or both counts) for a `keep` that is not yes or
    no, a kept row with no question, a kept answerable row with no source or a kept
    unanswerable row with one, a repeated `question_id`, or kept counts other than 25
    answerable and 5 unanswerable. Raises `FileExistsError` when `out_csv` exists.
    Returns `{"answerable", "unanswerable", "rewritten", "struck"}`.
    """
    if out_csv.exists():
        raise FileExistsError(f"{out_csv} exists; the final questions file is never overwritten")
    kept: list[dict[str, str]] = []
    struck = 0
    first_line: dict[str, int] = {}
    with draft_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        _check_header(reader.fieldnames, DRAFT_COLUMNS, draft_csv)
        rows = iter(reader)
        while True:
            start = reader.line_num + 1
            row = next(rows, None)
            if row is None:
                break
            question_id = (row["question_id"] or "").strip()
            if not question_id:
                raise ValueError(f"line {start}: question_id is empty")
            if question_id in first_line:
                earlier = first_line[question_id]
                raise ValueError(f"line {start}: question_id {question_id} repeats line {earlier}")
            first_line[question_id] = start
            keep = (row["keep"] or "").strip().lower()
            if keep not in ("yes", "no"):
                raise ValueError(f"line {start}: keep must be yes or no, not {row['keep']!r}")
            kind = (row["kind"] or "").strip()
            if kind not in KINDS:
                raise ValueError(f"line {start}: kind must be answerable or unanswerable")
            if keep == "no":
                struck += 1
                continue
            question = (row["question"] or "").strip()
            source = (row["source_doc_id"] or "").strip()
            if not question:
                raise ValueError(f"line {start}: {question_id} is kept but its question is empty")
            if kind == "answerable" and not source:
                raise ValueError(
                    f"line {start}: {question_id} is answerable and needs a source_doc_id"
                )
            if kind == "unanswerable" and source:
                raise ValueError(
                    f"line {start}: {question_id} is unanswerable and has no source_doc_id"
                )
            kept.append(
                {**row, "question_id": question_id, "kind": kind, "question": question}
                | {"source_doc_id": source}
            )

    answerable = sorted((r for r in kept if r["kind"] == "answerable"), key=_id_of)
    unanswerable = sorted((r for r in kept if r["kind"] == "unanswerable"), key=_id_of)
    if (len(answerable), len(unanswerable)) != (ANSWERABLE_N, UNANSWERABLE_N):
        raise ValueError(
            f"the draft keeps {len(answerable)} answerable and {len(unanswerable)} unanswerable "
            f"questions; it must keep {ANSWERABLE_N} and {UNANSWERABLE_N}"
        )

    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    out = []
    for row in answerable + unanswerable:
        if row["kind"] == "unanswerable":
            origin = "owner"
        elif row["question"] == (row["drafted_question"] or "").strip():
            origin = "drafted"
        else:
            origin = "rewritten"
        out.append(
            {
                "question_id": row["question_id"],
                "kind": row["kind"],
                "source_doc_id": row["source_doc_id"],
                "question": row["question"],
                "origin": origin,
                "draft_model": row["draft_model"],
                "draft_prompt_version": row["draft_prompt_version"],
                "finalized_at": stamp,
            }
        )
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    return {
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "rewritten": sum(1 for r in out if r["origin"] == "rewritten"),
        "struck": struck,
    }


def _id_of(row: dict[str, str]) -> str:
    return row["question_id"]


def read_questions(path: Path) -> list[Question]:
    """The final questions file, in file order (answerable by id, then unanswerable)."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        _check_header(reader.fieldnames, FINAL_COLUMNS, path)
        return [
            Question(
                question_id=r["question_id"],
                kind=r["kind"],
                source_doc_id=r["source_doc_id"],
                question=r["question"],
                origin=r["origin"],
                draft_model=r["draft_model"],
                draft_prompt_version=r["draft_prompt_version"],
            )
            for r in reader
        ]
