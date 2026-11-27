"""The two tables of `ask-eval`: what each (question, arm) asked, and what the judge said.

`ask_eval_results` holds one row for every (question, arm, answer model, prompt version)
that has been asked: the key is the question's own text, so an edited question is a new
question and is paid for again. The row points at the `ask_log` row that holds the answer,
and says whether the question's source abstract was among the retrieved ids. That
comparison is made in Python by the flow; the source id itself is never stored here.

`ask_judgements` holds one row for every (ask, claim, judge model, judge prompt version): a
second model's answer to "does this quote support this claim". A judge call that gave no
usable answer (a refusal, a cut-off, bad JSON) was billed, so it holds its key with
`supported` null, and is reported as "not judged", never as supported or unsupported.

A re-run finds the keys present and makes no call. Both inserts are `ON CONFLICT DO NOTHING`
with no WHERE clause, so nothing here depends on how the driver prepares a statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

metadata = sa.MetaData()

ask_eval_results = sa.Table(
    "ask_eval_results",
    metadata,
    sa.Column("question", sa.Text, primary_key=True),
    sa.Column("arm", sa.Text, primary_key=True),
    sa.Column("answer_model", sa.Text, primary_key=True),
    sa.Column("prompt_version", sa.Text, primary_key=True),
    sa.Column("question_id", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("ask_id", sa.BigInteger, nullable=False),
    sa.Column("k", sa.SmallInteger, nullable=False),
    # Null for an unanswerable question; else: the source id is among the retrieved ids.
    sa.Column("source_hit", sa.Boolean),
    sa.Column(
        "evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

ask_judgements = sa.Table(
    "ask_judgements",
    metadata,
    sa.Column("ask_id", sa.BigInteger, primary_key=True),
    sa.Column("claim_index", sa.SmallInteger, primary_key=True),
    sa.Column("judge_model", sa.Text, primary_key=True),
    sa.Column("judge_prompt_version", sa.Text, primary_key=True),
    sa.Column("supported", sa.Boolean),  # null: the judge gave no usable answer
    sa.Column("rationale", sa.Text),
    sa.Column("served_model", sa.Text),  # the id the API answered with
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "cost_usd", sa.Numeric(10, 6, asdecimal=False), nullable=False, server_default=sa.text("0")
    ),
    sa.Column(
        "judged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)


@dataclass(frozen=True)
class Result:
    question: str
    arm: str
    question_id: str
    kind: str
    ask_id: int
    k: int
    source_hit: bool | None
    evaluated_at: datetime


@dataclass(frozen=True)
class Judgement:
    supported: bool | None
    rationale: str | None
    cost_usd: float


def ensure_schema(engine: Engine) -> None:
    """Create the two tables. Safe to repeat."""
    metadata.create_all(engine)


def result_keys(engine: Engine, answer_model: str, prompt_version: str) -> set[tuple[str, str]]:
    """(question, arm) of every result of this answer model and prompt version."""
    c = ask_eval_results.c
    stmt = sa.select(c.question, c.arm).where(
        c.answer_model == answer_model, c.prompt_version == prompt_version
    )
    with engine.connect() as conn:
        return {(row.question, row.arm) for row in conn.execute(stmt)}


def add_result(
    engine: Engine,
    *,
    question: str,
    arm: str,
    answer_model: str,
    prompt_version: str,
    question_id: str,
    kind: str,
    ask_id: int,
    k: int,
    source_hit: bool | None,
) -> bool:
    """Store one result. Returns False, and changes nothing, when the key is already there."""
    stmt = (
        insert(ask_eval_results)
        .values(
            question=question,
            arm=arm,
            answer_model=answer_model,
            prompt_version=prompt_version,
            question_id=question_id,
            kind=kind,
            ask_id=ask_id,
            k=k,
            source_hit=source_hit,
        )
        .on_conflict_do_nothing(
            index_elements=["question", "arm", "answer_model", "prompt_version"]
        )
        .returning(ask_eval_results.c.question)
    )
    with engine.begin() as conn:
        return conn.execute(stmt).first() is not None


def results_for(engine: Engine, answer_model: str, prompt_version: str) -> list[Result]:
    """Every result of this answer model and prompt version, by question id then arm."""
    c = ask_eval_results.c
    stmt = (
        sa.select(
            c.question, c.arm, c.question_id, c.kind, c.ask_id, c.k, c.source_hit, c.evaluated_at
        )
        .where(c.answer_model == answer_model, c.prompt_version == prompt_version)
        .order_by(c.question_id, c.arm)
    )
    with engine.connect() as conn:
        return [Result(*row) for row in conn.execute(stmt)]


def judged(engine: Engine, judge_model: str, judge_prompt_version: str) -> set[tuple[int, int]]:
    """(ask_id, claim_index) of every claim this judge model and prompt version has judged,
    an unusable judgement included."""
    return set(judgements(engine, judge_model, judge_prompt_version))


def judgements(
    engine: Engine, judge_model: str, judge_prompt_version: str
) -> dict[tuple[int, int], Judgement]:
    """The stored judgement of each (ask_id, claim_index)."""
    c = ask_judgements.c
    stmt = sa.select(c.ask_id, c.claim_index, c.supported, c.rationale, c.cost_usd).where(
        c.judge_model == judge_model, c.judge_prompt_version == judge_prompt_version
    )
    with engine.connect() as conn:
        return {
            (row.ask_id, row.claim_index): Judgement(row.supported, row.rationale, row.cost_usd)
            for row in conn.execute(stmt)
        }


def add_judgement(
    engine: Engine,
    *,
    ask_id: int,
    claim_index: int,
    judge_model: str,
    judge_prompt_version: str,
    supported: bool | None,
    rationale: str | None,
    served_model: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> bool:
    """Store one judgement. Returns False, and changes nothing, when the key is already there."""
    stmt = (
        insert(ask_judgements)
        .values(
            ask_id=ask_id,
            claim_index=claim_index,
            judge_model=judge_model,
            judge_prompt_version=judge_prompt_version,
            supported=supported,
            rationale=rationale,
            served_model=served_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        .on_conflict_do_nothing(
            index_elements=["ask_id", "claim_index", "judge_model", "judge_prompt_version"]
        )
        .returning(ask_judgements.c.ask_id)
    )
    with engine.begin() as conn:
        return conn.execute(stmt).first() is not None
