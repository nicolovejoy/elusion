"""Prefect flow `ask_eval`: run every gold question through every arm and grade the answers.

The idea in plain words. `ask` finds abstracts for a question (by words, by meaning, or both:
the three *arms*), shows the best five to a model, and gets back claims, each with a quote.
This flow asks the 30 gold questions of each arm, and measures three things per arm:

- recall@5: how often the one abstract a question was written from is among the five
  retrieved. The comparison is made here, in Python, between the question's `source_doc_id`
  and the retrieved ids. The source id never enters a prompt, never reaches `ask`, and never
  reaches the judge: it exists only for this comparison.
- groundedness: whether each claim's quote supports the claim. Python has already checked the
  quote is in the abstract (`ask`); a second model, the *judge*, now answers "does this quote
  support this claim", seeing the claim and the quote and nothing else, one call per claim.
  The judge is a different model from the answerer, so no model grades itself.
- correct refusals: how often the five questions the corpus cannot answer were refused.

Three rules from the project hold here. Every call carries a pinned model id and a prompt
version, stored with the result. A result is keyed by (question text, arm, answer model,
prompt version) and a judgement by (ask, claim, judge model, judge prompt version): a key that
is present is never paid for again, a call that gave no usable answer included, because it was
billed. And before the first call the flow projects the cost of only what is still to do and
refuses to start when it is above the limit (the review flow's `BudgetExceeded`).

A judge call that ends in a refusal or a cut-off is stored with `supported` null and reported
as "not judged": it is never counted as supported or unsupported.

Two guards on money. The projection before the first call refuses a run that is expected to
cost more than the limit. And while the run goes on it adds up what it has actually been billed
(answers, judge calls, embeddings) and stops before the next call once that sum plus the next
call's projected cost would pass the limit, raising `BudgetExceeded`; everything paid so far is
already stored, and a re-run with a higher limit continues from there.

Known gap. An ask that was paid for but not recorded as a result (a failure in the one insert
after it) is asked again on the next run, at a few cents.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from prefect import flow, get_run_logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from elusion.ask import index, results
from elusion.ask import log as ask_log
from elusion.ask.answer import PROMPT as ASK_PROMPT_NAME
from elusion.ask.answer import PROMPT_VERSION as ASK_PROMPT_VERSION
from elusion.ask.answer import ask
from elusion.ask.embed import Embedder
from elusion.ask.index import abstract_index
from elusion.ask.metrics import ArmReport, ClaimResult, EvalRow, arm_report
from elusion.ask.models import EMBEDDING_MODEL, THINKING_OUTPUT_TOKENS
from elusion.ask.questions import Question, questions_path, read_questions
from elusion.ask.report import Setup, render, report_path
from elusion.ask.retrieve import ARMS, FUSION_DEPTH, RRF_C, K
from elusion.ask.tracking import find_run, log_arm_run
from elusion.collect import db
from elusion.config import settings
from elusion.flows.evaluate import report_date
from elusion.flows.review import BudgetExceeded
from elusion.review.pricing import PRICES, cost_usd
from elusion.review.prompt import PROMPTS_DIR, load_prompt
from elusion.review.providers import Completer, ReviewError

ASK_PROMPT = load_prompt(ASK_PROMPT_NAME, ASK_PROMPT_VERSION).version  # "ask/v1"
JUDGE_PROMPT = load_prompt("ask", "judge-v1")
# 16,000, not a small limit: on claude-opus-5-5 and claude-sonnet-5-5 thinking cannot be turned
# off and its tokens count against `max_tokens` (Anthropic docs, read 2026-11-26).
JUDGE_MAX_TOKENS = 16000

# The spend guard's assumptions. Thinking cannot be switched off on either model and is paid as
# output, so a call may spend far more output than its answer needs: these are generous on
# purpose, and a real run costs less. The answer is a few hundred tokens and the verdict a few
# dozen; the rest is thinking.
ANSWER_OUTPUT_TOKENS = 3000
JUDGE_OUTPUT_TOKENS = THINKING_OUTPUT_TOKENS
JUDGE_INPUT_TOKENS = 300  # the prompt, one claim and one quote
CLAIMS_PER_ANSWER = 3  # assumed for each answerable question still to be asked
ANSWER_FRAME_TOKENS = 700  # the prompt and the question around the passages
PASSAGE_FRAME_TOKENS = 60  # the `[doc_id] title` header of each passage
QUESTION_EMBED_TOKENS = 100


class Support(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool
    rationale: str


@dataclass(frozen=True)
class EvalResult:
    report_path: Path
    run_ids: dict[str, str]  # arm -> MLflow run id; empty without MLflow
    asked: int  # asks made by this run
    judged: int  # judge calls made by this run
    cost_usd: float  # spent by this run
    reports: dict[str, ArmReport] = field(default_factory=dict)  # arm -> its figures


@dataclass(frozen=True)
class Owed:
    """A claim whose quote passed the check and that the judge has not seen yet."""

    ask_id: int
    claim_index: int
    claim: str
    quote: str


def judge_message(claim: str, quote: str) -> str:
    """The user turn of a judge call: the claim and its quote, and nothing else."""
    return f"CLAIM:\n{claim}\n\nQUOTE:\n{quote}"


def _checked(row: ask_log.AskRow) -> list[int]:
    """Indexes of the claims whose id was retrieved and whose quote is verbatim."""
    return sorted(c["index"] for c in row.checks if c["retrieved"] and c["verbatim"])


def _owed(engine: Engine, stored: list[results.Result], done: set[tuple[int, int]]) -> list[Owed]:
    out = []
    for result in stored:
        row = ask_log.get_ask(engine, result.ask_id)
        claims = (row.answer or {}).get("claims", [])
        for i in _checked(row):
            if (result.ask_id, i) not in done and i < len(claims):
                out.append(Owed(result.ask_id, i, claims[i]["claim"], claims[i]["quote"]))
    return out


def _mean_chars(engine: Engine) -> float:
    with engine.connect() as conn:
        mean = conn.execute(sa.select(sa.func.avg(sa.func.length(abstract_index.c.text)))).scalar()
    return float(mean or 0)


def ask_cost(answerer: Completer, embedder: Embedder, arm: str, mean_chars: float) -> float:
    """The projected cost of one ask: the five passages, the question and a long answer, and
    the question's embedding for an arm that searches by meaning."""
    passages = K * (int(mean_chars) // 3 + PASSAGE_FRAME_TOKENS) + ANSWER_FRAME_TOKENS
    embedding = cost_usd(embedder.model, QUESTION_EMBED_TOKENS, 0) if arm != "lexical" else 0.0
    return cost_usd(answerer.model, passages, ANSWER_OUTPUT_TOKENS) + embedding


def claim_cost(judge: Completer) -> float:
    """The projected cost of one judge call."""
    return cost_usd(judge.model, JUDGE_INPUT_TOKENS, JUDGE_OUTPUT_TOKENS)


def project(
    answerer: Completer,
    judge: Completer,
    embedder: Embedder,
    *,
    todo: list[tuple[Question, str]],
    owed: int,
    mean_chars: float,
) -> float:
    """The cost of what is still to do: each ask still to make (the five passages, the
    question, a long answer), its judge calls (three claims assumed for an answerable
    question) and the claims already stored but not yet judged."""
    per_claim = claim_cost(judge)
    projected = sum(ask_cost(answerer, embedder, arm, mean_chars) for _, arm in todo)
    assumed = sum(CLAIMS_PER_ANSWER for q, _ in todo if q.kind == "answerable")
    return projected + per_claim * (assumed + owed)


def _check_inputs(
    questions: list[Question], answerer: Completer, judge: Completer, embedder: Embedder
) -> None:
    if answerer.model == judge.model:
        raise ValueError(
            f"the answer model and the judge are both {answerer.model!r}: "
            "a model must not grade its own output"
        )
    if embedder.model != EMBEDDING_MODEL:
        raise ValueError(
            f"the embedder is {embedder.model!r} but the index holds {EMBEDDING_MODEL!r} vectors"
        )
    for model in (answerer.model, judge.model, embedder.model):
        if model not in PRICES:
            raise ValueError(f"no price for model {model!r}; add it to review/pricing.py")
    seen: dict[str, str] = {}
    for q in questions:
        if q.question in seen:
            raise ValueError(
                f"{q.question_id} and {seen[q.question]} have the same question text; "
                "a result is keyed by the text, so each question must be worded once"
            )
        seen[q.question] = q.question_id
    if not any(q.kind == "answerable" for q in questions):
        raise ValueError("the questions file holds no answerable question")


def _rows(
    engine: Engine,
    questions: list[Question],
    arm: str,
    stored: dict[tuple[str, str], results.Result],
    judgements: dict[tuple[int, int], results.Judgement],
) -> tuple[list[EvalRow], float]:
    """One `EvalRow` per question of the arm, and the arm's cost: its asks and its judgements.

    `source_hit` is recomputed here from the retrieved ids the `ask_log` row holds and the
    question's current `source_doc_id`, so it can never disagree with them."""
    rows, cost = [], 0.0
    for q in questions:
        result = stored.get((q.question, arm))
        if result is None:
            continue
        row = ask_log.get_ask(engine, result.ask_id)
        cost += row.cost_usd
        hit = None
        if q.kind == "answerable":
            hit = q.source_doc_id in {h["doc_id"] for h in row.retrieved}
        passed = set(_checked(row))
        claims = []
        for i in range(len((row.answer or {}).get("claims", []))):
            judgement = judgements.get((result.ask_id, i))
            if judgement is not None:
                cost += judgement.cost_usd
            checked = i in passed
            claims.append(
                ClaimResult(checked, judgement.supported if checked and judgement else None)
            )
        rows.append(
            EvalRow(
                q.question_id,
                q.kind,
                hit,
                row.outcome,
                tuple(claims),
                no_passages=bool(row.detail.get("no_passages")),
            )
        )
    return rows, cost


@flow(name="ask_eval")
def ask_eval(
    criterion: str,
    answerer: Completer,
    judge: Completer,
    embedder: Embedder,
    *,
    engine: Engine | None = None,
    questions_csv: Path | None = None,
    reports_dir: Path | None = None,
    max_usd: float | None = None,
    log_mlflow: bool = True,
) -> EvalResult:
    """Ask every gold question of every arm, judge each checked claim, write the report and
    log one MLflow run per arm. A second run finds every key present and pays nothing."""
    log = get_run_logger()
    max_usd = settings.max_run_usd if max_usd is None else max_usd
    path = questions_csv or questions_path(criterion, settings.data_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"no questions file at {path}: run `elusion ask-questions --criterion "
            f"criteria/{criterion}.md` first"
        )
    questions = read_questions(path)
    _check_inputs(questions, answerer, judge, embedder)

    engine = engine or db.make_engine(settings.database_url)
    index.ensure_schema(engine)
    ask_log.ensure_schema(engine)
    results.ensure_schema(engine)
    indexed = index.count_indexed(engine)
    if not indexed or index.count_vectors(engine, embedder.model) != indexed:
        raise ValueError("the index is empty or not fully embedded; run elusion index first")
    pair = index.indexed_pair(engine)
    # A source that has left the index (a re-index dropped a newly held-back id) would be an
    # unavoidable miss in all three arms, and nothing in the report would say why. The ids are
    # compared here, in Python, and go nowhere else.
    in_index = index.indexed_ids(engine)
    absent = sorted(
        q.question_id
        for q in questions
        if q.kind == "answerable" and q.source_doc_id not in in_index
    )
    if absent:
        raise ValueError(
            f"the source abstract of {', '.join(absent)} is not in the index (a re-index may "
            "have dropped it as newly held back for the gold set), so no arm could find it; "
            "nothing was asked and nothing was spent. Replace these questions: in "
            f"data/gold/{criterion}.questions-draft.csv set keep to no on their rows and keep "
            f"25 answerable ones, move data/gold/{criterion}.questions.csv aside (ask-questions "
            "never overwrites a file), run `elusion ask-questions` again, then run ask-eval"
        )

    wanted = {(q.question, arm) for q in questions for arm in ARMS}
    done = results.result_keys(engine, answerer.model, ASK_PROMPT)
    todo = [(q, arm) for q in questions for arm in ARMS if (q.question, arm) not in done]
    stored = [
        r
        for r in results.results_for(engine, answerer.model, ASK_PROMPT)
        if (r.question, r.arm) in wanted
    ]
    judged_keys = results.judged(engine, judge.model, JUDGE_PROMPT.version)
    owed = _owed(engine, stored, judged_keys)

    mean_chars = _mean_chars(engine)
    projected = project(
        answerer,
        judge,
        embedder,
        todo=todo,
        owed=len(owed),
        mean_chars=mean_chars,
    )
    if projected > max_usd:
        raise BudgetExceeded(
            f"projected cost ${projected:.6f} for {len(todo)} asks and "
            f"{len(owed)} stored claims is above the limit of ${max_usd:.6f}; refusing to start"
        )

    spent = 0.0

    def check_spend(next_call: float) -> None:
        """Stop before a call that would take this run's actual bill past the limit."""
        if spent + next_call > max_usd:
            raise BudgetExceeded(
                f"billed ${spent:.6f} so far in this run and the next call is projected at "
                f"${next_call:.6f}, above the limit of ${max_usd:.6f}; stopping. Everything "
                "paid for is stored: run again with a higher --max-usd to continue"
            )

    for q, arm in todo:
        check_spend(ask_cost(answerer, embedder, arm, mean_chars))
        # Only the question's own text goes in. `q.source_doc_id` is read once more, below,
        # to compare with what came back, and goes nowhere else.
        asked = ask(
            engine, q.question, answerer, embedder, arm=arm, prompt_version=ASK_PROMPT_VERSION
        )
        spent += asked.cost_usd
        results.add_result(
            engine,
            question=q.question,
            arm=arm,
            answer_model=answerer.model,
            prompt_version=ASK_PROMPT,
            question_id=q.question_id,
            kind=q.kind,
            ask_id=asked.ask_id,
            k=K,
            source_hit=(
                q.source_doc_id in {h.doc_id for h in asked.retrieved}
                if q.kind == "answerable"
                else None
            ),
        )

    stored = [
        r
        for r in results.results_for(engine, answerer.model, ASK_PROMPT)
        if (r.question, r.arm) in wanted
    ]
    n_judged = 0
    for item in _owed(engine, stored, judged_keys):
        check_spend(claim_cost(judge))
        try:
            verdict = judge.complete(
                JUDGE_PROMPT.text,
                judge_message(item.claim, item.quote),
                Support,
                max_tokens=JUDGE_MAX_TOKENS,
            )
        except ReviewError as exc:
            # Billed and unusable: the key is held with `supported` null, and never retried.
            cost = exc.cost_usd(judge.model)
            fields = dict(
                supported=None,
                rationale=f"[no judgement: stop_reason={exc.stop_reason}]",
                served_model=exc.model,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                cost_usd=cost,
            )
        else:
            if verdict.model != judge.model:
                log.warning(
                    "the pinned judge is %s but the API served %s; the row stores both",
                    judge.model,
                    verdict.model,
                )
            cost = verdict.cost_usd
            fields = dict(
                supported=verdict.parsed.supported,
                rationale=verdict.parsed.rationale,
                served_model=verdict.model,
                input_tokens=verdict.input_tokens,
                output_tokens=verdict.output_tokens,
                cost_usd=cost,
            )
        results.add_judgement(
            engine,
            ask_id=item.ask_id,
            claim_index=item.claim_index,
            judge_model=judge.model,
            judge_prompt_version=JUDGE_PROMPT.version,
            **fields,
        )
        n_judged += 1
        spent += cost

    judgements = results.judgements(engine, judge.model, JUDGE_PROMPT.version)
    by_key = {(r.question, r.arm): r for r in stored}
    arm_rows: dict[str, list[EvalRow]] = {}
    reports: dict[str, ArmReport] = {}
    for arm in ARMS:
        rows, arm_cost = _rows(engine, questions, arm, by_key, judgements)
        arm_rows[arm] = rows
        reports[arm] = arm_report(arm, K, rows, arm_cost)

    review_model, review_version, threshold = pair or ("", "", 0)
    setup = Setup(
        criterion=criterion,
        date=report_date(datetime.now(UTC)),
        index_size=indexed,
        review_model=review_model,
        review_prompt_version=review_version,
        threshold=threshold,
        k=K,
        embedding_model=embedder.model,
        answer_model=answerer.model,
        prompt_version=ASK_PROMPT,
        judge_model=judge.model,
        judge_prompt_version=JUDGE_PROMPT.version,
        rrf_c=RRF_C,
        fusion_depth=FUSION_DEPTH,
    )
    out_dir = reports_dir if reports_dir is not None else Path("docs/reports")
    out = report_path(out_dir, criterion, answerer.model, ASK_PROMPT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render([reports[a] for a in ARMS], arm_rows, setup), encoding="utf-8")
    log.info("report written to %s", out)

    run_ids: dict[str, str] = {}
    if log_mlflow:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts = [
            out,
            PROMPTS_DIR / ASK_PROMPT_NAME / f"{ASK_PROMPT_VERSION}.md",
            PROMPTS_DIR / "ask" / "judge-v1.md",
        ]
        try:
            for arm in ARMS:
                params = {
                    "arm": arm,
                    "k": str(K),
                    "embedding_model": embedder.model,
                    "answer_model": answerer.model,
                    "prompt_version": ASK_PROMPT,
                    "judge_model": judge.model,
                    "judge_prompt_version": JUDGE_PROMPT.version,
                    "rrf_c": str(RRF_C),
                    "fusion_depth": str(FUSION_DEPTH),
                    "ts_config": "english",
                    "review_model": review_model,
                    "review_prompt_version": review_version,
                    "threshold": str(threshold),
                    "index_size": str(indexed),
                    "questions_sha256": digest,
                    "n_answerable": str(reports[arm].n_answerable),
                    "n_unanswerable": str(reports[arm].n_unanswerable),
                    "report_path": str(out),
                }
                uri = settings.mlflow_tracking_uri
                # An arm already logged with these params and figures (a re-run after a failure
                # on a later arm) keeps its run: no second copy on the leaderboard.
                run_ids[arm] = find_run(reports[arm], params, uri) or log_arm_run(
                    reports[arm], params, artifacts, uri
                )
        except Exception as exc:
            # Everything paid for is stored: a re-run costs nothing and logs only the arms that
            # have no finished run with the same params and figures.
            raise RuntimeError(
                f"report written to {out}, but MLflow logging failed: {exc}"
            ) from exc
    return EvalResult(out, run_ids, len(todo), n_judged, spent, reports)
