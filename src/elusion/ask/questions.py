"""The gold questions: 30 questions with a known answer (or none) to test `ask` against.

The idea in plain words. To measure whether retrieval finds the right abstract, we need
questions whose right abstract is known in advance. 25 of the 30 are answerable: each was
written from one screened-in abstract, its *source*, and the test is whether that abstract
comes back when the question is searched. The other 5 cannot be answered from the corpus at
all, and the test is whether `ask` refuses them.

How the file is made, in three steps with the owner's hands in the middle:

1. `draft_questions` draws 40 abstracts from the index with a fixed seed, once. Before the
   first model call it writes all 40 to the draft CSV as *pending* rows (`keep` is
   `pending`, the question empty), then makes one model call each and fills each row the
   moment its call returns, so a crash keeps the paid ones. A re-run takes its sources from
   the file and calls only for the rows still pending: the draw never moves, even when the
   index has grown since.
2. The owner edits the draft CSV by hand: sets `keep` to `no` on the questions he strikes
   (down to 25), rewrites the `question` of any he wants to word himself, and adds five rows
   `u1` to `u5` of `kind` `unanswerable` that he writes himself.
3. `finalize_questions` validates the edited draft and writes the final questions file, once
   (it never overwrites). `read_questions` reads that file back for the evaluation.

The gold rule. A question's source document id lives only in these two files under
`data/gold/`. It never enters a prompt (the model sees the abstract text and nothing else:
no id, no title), the index, or anything passed to `retrieve` or `ask`; this module does not
import either, and a test keeps it that way. The draw leaves out the ids held back as gold
labels at the moment of drawing (the index already leaves out those it knew of), and
`finalize_questions` checks again that no kept source is held back.

A draft call that ends without a usable question (the provider's refusal, a cut-off, an
empty string) was billed, so it still holds its key, the source id: its row is written with
`keep` `no`, an empty `question`, and the reason in `drafted_question`. It is never read as a
question. To have such a row drafted (and paid) again, set its `keep` back to `pending`.
Deleting a row does not redraw it: the draw is made once.

The files are read as a spreadsheet may have saved them: with a byte-order mark, with rows
of empty cells at the end, and with a clear error when the columns are separated by `;`.
"""

from __future__ import annotations

import csv
import logging
import os
import random
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from elusion.ask.index import abstract_index
from elusion.ask.models import DRAFT_MODEL as DRAFT_MODEL  # the evaluation names it from here
from elusion.ask.models import THINKING_OUTPUT_TOKENS
from elusion.config import settings
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import Completer, ReviewError

logger = logging.getLogger(__name__)

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
_ANSWER_TOKENS = THINKING_OUTPUT_TOKENS  # as `ask-eval` assumes: thinking is billed as output

PENDING = "pending"  # the `keep` of a drawn row whose call has not returned yet

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
    "served_model",
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
    "served_model",
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
    served_model: str = ""  # the id the API answered the draft with; "" for an owner question


def draft_path(criterion: str, data_dir: Path) -> Path:
    """data/gold/<criterion>.questions-draft.csv, beside the other gold files."""
    return data_dir / "gold" / f"{criterion}.questions-draft.csv"


def questions_path(criterion: str, data_dir: Path) -> Path:
    """data/gold/<criterion>.questions.csv: the final file."""
    return data_dir / "gold" / f"{criterion}.questions.csv"


def draft_message(text: str) -> str:
    """The user turn of a draft call: the abstract's text, and nothing else."""
    return f"ABSTRACT:\n{text}"


def draw_sources(
    engine: Engine,
    n: int = DRAFT_N,
    seed: int = QUESTION_SEED,
    *,
    held_back: Collection[str] = (),
) -> list[str]:
    """`n` doc ids drawn from the index: its sorted ids less `held_back`, then
    `random.Random(seed).sample`.

    `held_back` is the gold set as it stands now (`held_back_ids`), so a label added after
    the index was built still keeps its paper out. Raises `ValueError` when fewer than `n`
    abstracts remain."""
    stmt = sa.select(abstract_index.c.doc_id).order_by(abstract_index.c.doc_id)
    with engine.connect() as conn:
        ids = [row.doc_id for row in conn.execute(stmt) if row.doc_id not in held_back]
    if len(ids) < n:
        raise ValueError(
            f"the index holds {len(ids)} abstracts to draw from and the draw needs {n}"
        )
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


def _indexed(engine: Engine, doc_ids: Collection[str]) -> set[str]:
    """The ids among `doc_ids` that the index holds."""
    if not doc_ids:
        return set()
    stmt = sa.select(abstract_index.c.doc_id).where(abstract_index.c.doc_id.in_(list(doc_ids)))
    with engine.connect() as conn:
        return {row.doc_id for row in conn.execute(stmt)}


def _read_table(path: Path, columns: list[str]) -> list[tuple[int, dict[str, str]]]:
    """The rows of a CSV as (line number, row), the way a spreadsheet may have left it.

    The file is read as UTF-8 with an optional byte-order mark; a row whose every cell is
    empty is skipped; a short row is padded. Raises `ValueError` for a header that is not
    `columns` (saying so when the cells are separated by `;`) and for a row with more
    cells than columns."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader, [])]
        if header != columns:
            if len(header) == 1 and header[0].split(";") == columns:
                raise ValueError(
                    f"{path} looks semicolon-delimited: save it as a comma-separated CSV (UTF-8)"
                )
            raise ValueError(f"{path} has the columns {header}; expected {columns}")
        out = []
        while True:
            line = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if not any(c.strip() for c in cells):
                continue
            if len(cells) > len(columns):
                raise ValueError(f"line {line}: {len(cells)} cells, expected {len(columns)}")
            cells = cells + [""] * (len(columns) - len(cells))
            out.append((line, dict(zip(columns, cells, strict=True))))
        return out


def _read_draft_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    return [row for _, row in _read_table(path, DRAFT_COLUMNS)]


def _write_draft(path: Path, rows: list[dict[str, str]]) -> None:
    """Write the whole draft to a temporary name and rename it over `path`, so a crash
    leaves the old file or the new one, never half of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_pending(row: dict[str, str]) -> bool:
    return row["kind"] == "answerable" and row["keep"].strip().lower() == PENDING


def draft_questions(
    engine: Engine,
    drafter: Completer,
    path: Path,
    *,
    held_back: Collection[str],
    n: int = DRAFT_N,
    seed: int = QUESTION_SEED,
    max_usd: float | None = None,
) -> dict:
    """Draft one question from each of `n` drawn abstracts into the draft CSV at `path`.

    The draw is made once: when the file is missing or holds no answerable row, `n`
    sources are drawn (leaving out `held_back`) and written as pending rows before the
    first call. Otherwise the sources are the file's, and only its pending rows are
    drafted; `n`, `seed` and the index are not consulted again. A row that holds a question,
    or a billed failure, is never drafted again. Raises `BudgetExceeded` before the first
    call when the rows still pending are projected to cost more than `max_usd` (default
    `ELUSION_MAX_RUN_USD`). Returns `{"drawn", "already_drafted", "drafted", "failed",
    "cost_usd"}`: `drawn` is the answerable rows in the file.
    """
    max_usd = settings.max_run_usd if max_usd is None else max_usd
    prompt = load_prompt(PROMPT, PROMPT_VERSION)
    rows = _read_draft_rows(path)
    fresh = not any(r["kind"] == "answerable" for r in rows)
    if fresh:
        for position, source in enumerate(
            draw_sources(engine, n, seed, held_back=held_back), start=1
        ):
            rows.append(
                {
                    **dict.fromkeys(DRAFT_COLUMNS, ""),
                    "question_id": f"q{position:02d}",
                    "kind": "answerable",
                    "source_doc_id": source,
                    "keep": PENDING,
                    "draft_model": drafter.model,
                    "draft_prompt_version": prompt.version,
                }
            )
    answerable = [r for r in rows if r["kind"] == "answerable"]
    todo = [r for r in answerable if _is_pending(r)]
    summary = {
        "drawn": len(answerable),
        "already_drafted": len(answerable) - len(todo),
        "drafted": 0,
        "failed": 0,
        "cost_usd": 0.0,
    }
    texts = _texts(engine, [r["source_doc_id"] for r in todo])

    projected = sum(
        cost_usd(drafter.model, len(texts[r["source_doc_id"]]) // 3 + _FRAME_TOKENS, _ANSWER_TOKENS)
        for r in todo
        if r["source_doc_id"] in texts
    )
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(todo)} abstracts is above "
            f"the limit of ${max_usd:.6f}; refusing to start"
        )
    if fresh:
        _write_draft(path, rows)  # the draw is fixed from here on, before any call

    for row in todo:
        source = row["source_doc_id"]
        if source not in texts:
            logger.warning("%s is no longer in the index; its row stays pending", source)
            continue
        row["draft_model"] = drafter.model
        row["draft_prompt_version"] = prompt.version
        try:
            done = drafter.complete(
                prompt.text, draft_message(texts[source]), Draft, max_tokens=DRAFT_MAX_TOKENS
            )
        except ReviewError as exc:
            # No usable answer, but the call was billed: it holds its key as a struck row.
            summary["cost_usd"] += exc.cost_usd(drafter.model)
            summary["failed"] += 1
            row.update(
                drafted_at=_now(),
                served_model=exc.model,
                drafted_question=f"[no question: stop_reason={exc.stop_reason}]",
                question="",
                keep="no",
            )
            _write_draft(path, rows)
            continue
        summary["cost_usd"] += done.cost_usd
        if done.model != drafter.model:
            logger.warning(
                "the pinned model is %s but the API served %s; the row stores both",
                drafter.model,
                done.model,
            )
        question = " ".join(done.parsed.question.split())
        row.update(drafted_at=_now(), served_model=done.model)
        if question:
            summary["drafted"] += 1
            row.update(drafted_question=question, question=question, keep="yes")
        else:
            summary["failed"] += 1
            row.update(
                drafted_question="[no question: the model's question was empty]",
                question="",
                keep="no",
            )
        _write_draft(path, rows)
    return summary


def _pending_advice(engine: Engine, row: dict[str, str]) -> str:
    """What to do about a pending row. A row whose source has left the index can never be
    drafted (`draft_questions` skips it), so running `ask-draft` again cannot help."""
    source = row["source_doc_id"].strip()
    if source and source not in _indexed(engine, [source]):
        return (
            f"its source {source} is no longer in the index, so ask-draft cannot draft it: "
            "set its keep to no"
        )
    return "run ask-draft again to draft it"


def finalize_questions(
    draft_csv: Path,
    out_csv: Path,
    engine: Engine,
    *,
    held_back: Collection[str],
    now: datetime | None = None,
) -> dict:
    """Validate the owner's edited draft and write the final questions file, once.

    Raises `ValueError` (naming the line, or both counts) for a row still `pending`, a
    `keep` that is not yes or no, a kept row with no question, a kept answerable row with no
    source or a kept unanswerable row with one, a repeated `question_id`, kept counts other
    than 25 answerable and 5 unanswerable, and a kept source that is not in the index or is
    in `held_back` (a spreadsheet can turn an all-digit id into `1.23E+15`). Raises
    `FileExistsError` when `out_csv` exists. The file is written under a temporary name and
    linked into place, so a crash never leaves a partial final file.
    Returns `{"answerable", "unanswerable", "rewritten", "struck"}`.
    """
    if out_csv.exists():
        raise FileExistsError(f"{out_csv} exists; the final questions file is never overwritten")
    kept: list[tuple[int, dict[str, str]]] = []
    struck = 0
    first_line: dict[str, int] = {}
    for line, row in _read_table(draft_csv, DRAFT_COLUMNS):
        question_id = row["question_id"].strip()
        if not question_id:
            raise ValueError(f"line {line}: question_id is empty")
        if question_id in first_line:
            earlier = first_line[question_id]
            raise ValueError(f"line {line}: question_id {question_id} repeats line {earlier}")
        first_line[question_id] = line
        keep = row["keep"].strip().lower()
        if keep == PENDING:
            raise ValueError(
                f"line {line}: {question_id} is still pending: {_pending_advice(engine, row)}"
            )
        if keep not in ("yes", "no"):
            raise ValueError(f"line {line}: keep must be yes or no, not {row['keep']!r}")
        kind = row["kind"].strip()
        if kind not in KINDS:
            raise ValueError(f"line {line}: kind must be answerable or unanswerable")
        if keep == "no":
            struck += 1
            continue
        question = row["question"].strip()
        source = row["source_doc_id"].strip()
        if not question:
            raise ValueError(f"line {line}: {question_id} is kept but its question is empty")
        if kind == "answerable" and not source:
            raise ValueError(f"line {line}: {question_id} is answerable and needs a source_doc_id")
        if kind == "unanswerable" and source:
            raise ValueError(
                f"line {line}: {question_id} is unanswerable and must have no source_doc_id"
            )
        kept.append(
            (
                line,
                {
                    **row,
                    "question_id": question_id,
                    "kind": kind,
                    "question": question,
                    "source_doc_id": source,
                },
            )
        )

    answerable = sorted((k for k in kept if k[1]["kind"] == "answerable"), key=_id_of)
    unanswerable = sorted((k for k in kept if k[1]["kind"] == "unanswerable"), key=_id_of)
    if (len(answerable), len(unanswerable)) != (ANSWERABLE_N, UNANSWERABLE_N):
        raise ValueError(
            f"the draft keeps {len(answerable)} answerable and {len(unanswerable)} unanswerable "
            f"questions; it must keep {ANSWERABLE_N} and {UNANSWERABLE_N}"
        )
    indexed = _indexed(engine, [row["source_doc_id"] for _, row in answerable])
    for line, row in answerable:
        source = row["source_doc_id"]
        if source in held_back:
            raise ValueError(
                f"line {line}: {row['question_id']} has source_doc_id {source!r}, "
                "which is held back as a gold label"
            )
        if source not in indexed:
            raise ValueError(
                f"line {line}: {row['question_id']} has source_doc_id {source!r}, "
                "which is not in the index (a spreadsheet may have mangled the id)"
            )

    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    out = []
    for _, row in answerable + unanswerable:
        if row["kind"] == "unanswerable":
            origin = "owner"
        elif row["question"] == row["drafted_question"].strip():
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
                "served_model": row["served_model"],
            }
        )
    _write_once(out_csv, out)
    return {
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "rewritten": sum(1 for r in out if r["origin"] == "rewritten"),
        "struck": struck,
    }


def _write_once(path: Path, rows: list[dict[str, str]]) -> None:
    """Write `rows` to `path` through a temporary name. `os.link` refuses an existing
    target, so this never overwrites, and the target appears whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _id_of(item: tuple[int, dict[str, str]]) -> str:
    return item[1]["question_id"]


def read_questions(path: Path) -> list[Question]:
    """The final questions file, in file order (answerable by id, then unanswerable)."""
    return [
        Question(
            question_id=r["question_id"],
            kind=r["kind"],
            source_doc_id=r["source_doc_id"],
            question=r["question"],
            origin=r["origin"],
            draft_model=r["draft_model"],
            draft_prompt_version=r["draft_prompt_version"],
            served_model=r["served_model"],
        )
        for _, r in _read_table(path, FINAL_COLUMNS)
    ]
