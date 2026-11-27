"""The `ask_eval` flow on a throwaway database with fake models: no network, no paid call.

Three made-up questions (two answerable, one not) over three made-up abstracts, so every
count can be worked out by hand.
"""

import csv
import hashlib
import re

import pytest
import sqlalchemy as sa
from prefect.testing.utilities import prefect_test_harness

from elusion.ask import index, log, results
from elusion.ask.answer import Answer, Claim
from elusion.ask.models import ANSWER_MODEL, EMBEDDING_MODEL, JUDGE_MODEL
from elusion.ask.questions import FINAL_COLUMNS
from elusion.ask.retrieve import ARMS
from elusion.board.batch import Paper
from elusion.flows.ask_eval import ASK_PROMPT, JUDGE_PROMPT, Support, ask_eval, judge_message
from elusion.flows.review import BudgetExceeded
from elusion.review.providers import ReviewError
from tests.fakes import FakeCompleter, FakeEmbedder

CRITERION = "auditable-decisions"
PAIR = {"review_model": "gpt-6-luna", "prompt_version": "auditable-decisions/v1", "threshold": 3}
SRC1, SRC2, OTHER = "src0000000000001", "src0000000000002", "dis0000000000003"
SOURCES = (SRC1, SRC2)
DOCS = {
    SRC1: (
        "Stopping rules",
        "A stopping rule says when a review ends. It stops at a fixed recall.",
    ),
    SRC2: ("Audit trails", "Audit trails record every decision a reviewer makes during review."),
    OTHER: ("Soil", "Soil chemistry of wetlands and peat bogs."),
}
Q1 = "When does a stopping rule end a review?"
Q2 = "What do audit trails record about every decision?"
Q3 = "How tall is the zebra in the savanna?"
PEAT = "Why does peat chemistry matter?"  # words that match OTHER only


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


@pytest.fixture
def world(index_engine, tmp_path):
    papers = [Paper(d, f"W{d}", title, "2024-03-01", text) for d, (title, text) in DOCS.items()]
    index.add_abstracts(index_engine, papers, **PAIR)
    fake = FakeEmbedder(model=EMBEDDING_MODEL)
    index.add_vectors(
        index_engine,
        EMBEDDING_MODEL,
        {d: fake.vector(index.document_text(t, x)) for d, (t, x) in DOCS.items()},
    )
    write_questions(
        tmp_path,
        [
            ("q01", "answerable", SRC1, Q1),
            ("q02", "answerable", SRC2, Q2),
            ("u1", "unanswerable", "", Q3),
        ],
    )
    return index_engine


def write_questions(tmp_path, rows):
    path = tmp_path / "questions.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS)
        writer.writeheader()
        for qid, kind, source, question in rows:
            writer.writerow(
                dict.fromkeys(FINAL_COLUMNS, "")
                | {
                    "question_id": qid,
                    "kind": kind,
                    "source_doc_id": source,
                    "question": question,
                    "origin": "drafted",
                }
            )
    return path


def first_passage(message):
    """(doc_id, first line of its text) of the first passage the answerer was shown."""
    body = message.split("PASSAGES:\n", 1)[1]
    doc_id, text = re.match(r"\[(\w+)\] [^\n]*\n([^\n]*)", body).groups()
    return doc_id, text


def good_answer(system, message, out):
    if "zebra" in message.split("PASSAGES:")[0]:
        return Answer(answerable=False, claims=[])
    doc_id, text = first_passage(message)
    quote = " ".join(text.split()[:6])
    return Answer(answerable=True, claims=[Claim(claim="It says so.", doc_id=doc_id, quote=quote)])


def yes(system, message, out):
    return Support(supported=True, rationale="the quote says it")


def answerer(fn=good_answer, model=ANSWER_MODEL, **kw):
    return FakeCompleter(fn, model=model, **kw)


def judge(fn=yes, model=JUDGE_MODEL, **kw):
    return FakeCompleter(fn, model=model, **kw)


def run(engine, tmp_path, a=None, j=None, e=None, **kw):
    kw.setdefault("log_mlflow", False)
    kw.setdefault("questions_csv", tmp_path / "questions.csv")
    kw.setdefault("reports_dir", tmp_path / "reports")
    return ask_eval(
        CRITERION,
        a or answerer(),
        j or judge(),
        e or FakeEmbedder(model=EMBEDDING_MODEL),
        engine=engine,
        **kw,
    )


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def stored_results(engine, model=ANSWER_MODEL):
    return {(r.question, r.arm): r for r in results.results_for(engine, model, ASK_PROMPT)}


def test_the_two_models_differ() -> None:
    assert ANSWER_MODEL != JUDGE_MODEL


def test_every_question_arm_pair_is_asked_once(world, tmp_path) -> None:
    a, j, e = answerer(), judge(), FakeEmbedder(model=EMBEDDING_MODEL)
    first = run(world, tmp_path, a, j, e)
    assert first.asked == 9 and first.judged > 0
    assert count(world, log.ask_log) == 9 and count(world, results.ask_eval_results) == 9
    assert set(stored_results(world)) == {(q, arm) for q in (Q1, Q2, Q3) for arm in ARMS}
    before = (len(a.calls), len(j.calls), len(e.calls))
    second = run(world, tmp_path, a, j, e)
    assert (len(a.calls), len(j.calls), len(e.calls)) == before  # no call of any kind
    assert (second.asked, second.judged, second.cost_usd) == (0, 0, 0.0)
    assert count(world, log.ask_log) == 9 and count(world, results.ask_eval_results) == 9
    assert second.reports == first.reports


def test_the_judge_is_called_only_for_claims_that_passed_the_check(world, tmp_path) -> None:
    def two_claims(system, message, out):
        answer = good_answer(system, message, out)
        if answer.claims:
            invented = Claim(claim="Invented.", doc_id=answer.claims[0].doc_id, quote="never said")
            answer = Answer(answerable=True, claims=[*answer.claims, invented])
        return answer

    j = judge()
    result = run(world, tmp_path, answerer(two_claims), j)
    assert j.calls and not any("never said" in m or "Invented." in m for _, m in j.calls)
    assert len(j.calls) == result.judged == count(world, results.ask_judgements)
    assert all(s == JUDGE_PROMPT.text for s, _ in j.calls)
    assert j.max_tokens == [16000] * len(j.calls)
    # q01 and q02 answered with one good and one invented claim: `failed`, one claim unsupported.
    hybrid = result.reports["hybrid"]
    assert (hybrid.claims_total, hybrid.claims_supported) == (4, 2)
    assert (hybrid.answered_questions, hybrid.fully_grounded) == (2, 0)


def test_the_judge_sees_the_claim_and_the_quote_and_nothing_else(world, tmp_path) -> None:
    j = judge()
    run(world, tmp_path, answerer(), j)
    assert j.calls
    for _, message in j.calls:
        assert message == judge_message("It says so.", message.split("QUOTE:\n")[1])
    assert judge_message("c", "q") == "CLAIM:\nc\n\nQUOTE:\nq"


def test_a_judged_claim_is_not_judged_again(world, tmp_path) -> None:
    seen = []

    def breaks_on_the_third(system, message, out):
        seen.append(message)
        if len(seen) == 3:
            raise RuntimeError("connection dropped")
        return Support(supported=True, rationale="r")

    a = answerer()
    with pytest.raises(RuntimeError, match="connection dropped"):
        run(world, tmp_path, a, judge(breaks_on_the_third))
    assert count(world, results.ask_judgements) == 2  # the paid ones are kept
    asked_calls = len(a.calls)

    j2 = judge()
    second = run(world, tmp_path, a, j2)
    assert len(a.calls) == asked_calls and second.asked == 0  # no ask again
    owed = count(world, results.ask_judgements) - 2
    assert len(j2.calls) == second.judged == owed
    # Every (ask, claim) holds one judgement and none was written twice.
    assert count(world, results.ask_judgements) == len(
        results.judged(world, JUDGE_MODEL, JUDGE_PROMPT.version)
    )


def test_an_unusable_judgement_is_stored_and_not_retried(world, tmp_path) -> None:
    def refuses(system, message, out):
        return ReviewError("refusal", JUDGE_MODEL, 120, 2500)

    first = run(world, tmp_path, answerer(), judge(refuses))
    stored = results.judgements(world, JUDGE_MODEL, JUDGE_PROMPT.version)
    assert len(stored) == first.judged > 0
    assert all(
        v.supported is None and "refusal" in v.rationale and v.cost_usd > 0 for v in stored.values()
    )
    assert first.cost_usd > 0
    r = first.reports["hybrid"]
    assert r.judge_unusable == r.claims_total > 0
    assert (
        r.groundedness_claims is None and r.claims_supported == 0
    )  # neither supported nor unsupported

    j2 = judge()
    second = run(world, tmp_path, answerer(), j2)
    assert j2.calls == [] and second.judged == 0
    assert "not judged" in second.report_path.read_text()


def test_a_new_answer_model_is_a_new_key(world, tmp_path) -> None:
    run(world, tmp_path, answerer())
    other = answerer(model="claude-haiku-5-5")
    second = run(world, tmp_path, other)
    assert second.asked == 9 and other.calls
    assert count(world, results.ask_eval_results) == 18 and count(world, log.ask_log) == 18
    assert len(stored_results(world, "claude-haiku-5-5")) == 9


def test_an_edited_question_is_a_new_question(world, tmp_path) -> None:
    run(world, tmp_path)
    write_questions(
        tmp_path,
        [
            ("q01", "answerable", SRC1, Q1 + " Please."),
            ("q02", "answerable", SRC2, Q2),
            ("u1", "unanswerable", "", Q3),
        ],
    )
    a2 = answerer()
    second = run(world, tmp_path, a2)
    assert second.asked == 3  # the three arms of the edited text only
    assert all(Q1 + " Please." in m for _, m in a2.calls)  # and only the edited text was sent
    assert count(world, results.ask_eval_results) == 12  # the old rows stay, unread
    assert (
        second.reports["hybrid"].n_answerable == 2 and second.reports["hybrid"].n_unanswerable == 1
    )


def test_no_source_doc_id_appears_in_any_message_to_any_model(world, tmp_path, monkeypatch) -> None:
    import elusion.ask.answer as answer_mod
    import elusion.flows.ask_eval as mod

    seen_args, retrieve_args = [], []
    real_ask, real_retrieve = mod.ask, answer_mod.retrieve

    def spy(*args, **kwargs):
        seen_args.append(repr((args[1:], kwargs)))
        return real_ask(*args, **kwargs)

    def retrieve_spy(*args, **kwargs):
        retrieve_args.append(repr((args[1:], kwargs)))
        return real_retrieve(*args, **kwargs)

    monkeypatch.setattr(mod, "ask", spy)
    monkeypatch.setattr(answer_mod, "retrieve", retrieve_spy)
    a, j, e = answerer(), judge(), FakeEmbedder(model=EMBEDDING_MODEL)
    run(world, tmp_path, a, j, e)
    assert a.calls and j.calls and e.calls and len(seen_args) == len(retrieve_args) == 9
    # The judge is sent the claim and the quote and nothing else: every message is exactly the
    # template filled with the fake answerer's claim and a quote cut from an indexed abstract.
    allowed = {
        judge_message("It says so.", " ".join(text.split()[:6])) for _, text in DOCS.values()
    }
    assert {message for _, message in j.calls} <= allowed
    for source in SOURCES:
        assert not any(source in text for text in seen_args)  # never passed to ask
        assert not any(source in text for text in retrieve_args)  # nor to retrieve
        assert not any(source in text for texts in e.calls for text in texts)
        assert not any(source in system or source in message for system, message in j.calls)
        for system, message in a.calls:
            assert source not in system
            question, passages = message.split("PASSAGES:\n", 1)
            assert source not in question
            # A retrieved abstract is shown under its own id, like any retrieved abstract; the
            # id appears nowhere else in what the answerer was sent.
            assert source not in passages.replace(f"[{source}] ", "")


def test_a_judge_message_that_carried_an_id_would_fail_the_leak_check(world, tmp_path) -> None:
    """The check above is not vacuous: a judge message with a document id fails it."""
    j = judge()
    run(world, tmp_path, answerer(), j)
    allowed = {
        judge_message("It says so.", " ".join(text.split()[:6])) for _, text in DOCS.values()
    }
    leaked = j.calls[0][1] + f"\nDOC: {SRC1}"
    assert leaked not in allowed and SRC1 in leaked


def test_source_hit_comes_from_the_retrieved_ids(world, tmp_path) -> None:
    """PEAT shares no word with its source, so the lexical arm misses it; the vector arm
    returns every indexed abstract and so hits."""
    write_questions(tmp_path, [("q01", "answerable", SRC1, PEAT), ("u1", "unanswerable", "", Q3)])
    run(world, tmp_path)
    stored = stored_results(world)
    assert [stored[(PEAT, arm)].source_hit for arm in ARMS] == [False, True, True]
    assert all(stored[(Q3, arm)].source_hit is None for arm in ARMS)
    hits = {arm: log.get_ask(world, stored[(PEAT, arm)].ask_id) for arm in ARMS}
    assert [h["doc_id"] for h in hits["lexical"].retrieved] == [OTHER]
    assert SRC1 in [h["doc_id"] for h in hits["vector"].retrieved]
    assert (stored[(PEAT, "lexical")].question_id, stored[(PEAT, "lexical")].k) == ("q01", 5)


def test_an_error_ask_still_holds_its_key(world, tmp_path) -> None:
    def cut_off(system, message, out):
        return ReviewError("max_tokens", ANSWER_MODEL, 900, 16000)

    a = answerer(cut_off)
    first = run(world, tmp_path, a)
    errors = [
        r for r in stored_results(world).values() if log.get_ask(world, r.ask_id).outcome == "error"
    ]
    assert (
        len(errors) == len(stored_results(world)) - 1 > 0
    )  # all but the lexical zebra, which retrieved nothing
    assert first.reports["hybrid"].errors == 3 and first.cost_usd > 0
    calls = len(a.calls)
    second = run(world, tmp_path, a)
    assert len(a.calls) == calls and second.asked == 0  # the billed key is held


def test_the_spend_guard_raises_before_any_call(world, tmp_path) -> None:
    a, j, e = answerer(), judge(), FakeEmbedder(model=EMBEDDING_MODEL)
    with pytest.raises(BudgetExceeded, match=r"projected cost \$\d.*limit of \$0\.000100"):
        run(world, tmp_path, a, j, e, max_usd=0.0001)
    assert (a.calls, j.calls, e.calls) == ([], [], [])
    assert count(world, log.ask_log) == 0 and count(world, results.ask_eval_results) == 0


def test_the_spend_guard_projects_only_what_is_still_to_do(world, tmp_path) -> None:
    from elusion.review.pricing import cost_usd

    # The judge goes down on its first call: every ask is stored, every claim is still owed.
    def down(system, message, out):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError, match="down"):
        run(world, tmp_path, answerer(), judge(down))
    owed = sum(
        1
        for row in log.asks(world)
        for check in row.checks
        if check["retrieved"] and check["verbatim"]
    )
    assert owed > 0
    per_claim = cost_usd(JUDGE_MODEL, 300, 1000)
    a, j = answerer(), judge()
    # No ask is left, so the projection is the owed claims alone: just under it refuses,
    # before a single call, and just over it goes through.
    with pytest.raises(BudgetExceeded):
        run(world, tmp_path, a, j, max_usd=per_claim * owed * 0.99)
    assert j.calls == [] and a.calls == []
    run(world, tmp_path, a, j, max_usd=per_claim * owed * 1.01)
    assert len(j.calls) == owed and a.calls == []
    # Nothing left at all: nothing is projected, so even a limit of zero lets it through.
    again = run(world, tmp_path, a, j, max_usd=0.0)
    assert (again.asked, again.judged) == (0, 0)


def test_a_missing_questions_file_names_the_command(world, tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="elusion ask-questions"):
        run(world, tmp_path, questions_csv=tmp_path / "none.csv")


def test_the_same_model_may_not_answer_and_judge(world, tmp_path) -> None:
    with pytest.raises(ValueError, match="grade its own"):
        run(world, tmp_path, answerer(), judge(model=ANSWER_MODEL))


def test_a_repeated_question_text_is_refused(world, tmp_path) -> None:
    write_questions(tmp_path, [("q01", "answerable", SRC1, Q1), ("q02", "answerable", SRC2, Q1)])
    a = answerer()
    with pytest.raises(ValueError, match="same question text"):
        run(world, tmp_path, a)
    assert a.calls == []


def test_the_report_has_no_question_text_no_source_id_and_the_five_lines(world, tmp_path) -> None:
    result = run(world, tmp_path)
    text = result.report_path.read_text()
    assert result.report_path.name == "ask-eval-auditable-decisions-claude-sonnet-5-5-ask-v1.md"
    for forbidden in (
        Q1,
        Q2,
        Q3,
        *SOURCES,
        OTHER,
        *(t for _, t in DOCS.values()),
        *(t for t, _ in DOCS.values()),
    ):
        assert forbidden not in text
    assert "|" not in text
    for needle in (
        "q01",
        "q02",
        "u1",
        "recall@5",
        "claude-opus-5-5",
        "ask/judge-v1",
        "hybrid",
        "lexical",
        "vector",
    ):
        assert needle in text
    assert text.endswith(
        "- recall@5 is a lower bound: another abstract may also answer the question.\n"
        "- The questions were drafted from abstracts, so they share vocabulary with their source"
        " even after rewording; this flatters lexical search.\n"
        "- Groundedness by claim treats the claims of one answer as independent, which they are"
        " not; the by-question figure is the one to trust.\n"
        "- The answer model and the judge are different models, so no model grades itself.\n"
        "- Only abstracts the screen flagged are searched; one the screen discarded cannot be"
        " found.\n"
    )


def test_one_mlflow_run_per_arm(world, tmp_path, monkeypatch) -> None:
    import mlflow

    import elusion.flows.ask_eval as mod
    from elusion.config import Settings

    uri = f"file://{tmp_path}/mlruns"
    monkeypatch.setattr(mod, "settings", Settings(mlflow_tracking_uri=uri))
    result = run(world, tmp_path, log_mlflow=True)
    assert set(result.run_ids) == set(ARMS) and len(set(result.run_ids.values())) == 3
    client = mlflow.MlflowClient(tracking_uri=uri)
    digest = hashlib.sha256((tmp_path / "questions.csv").read_bytes()).hexdigest()
    for arm, run_id in result.run_ids.items():
        run_ = client.get_run(run_id)
        p = run_.data.params
        assert (p["arm"], p["k"], p["answer_model"], p["prompt_version"]) == (
            arm,
            "5",
            ANSWER_MODEL,
            "ask/v1",
        )
        assert (p["judge_model"], p["judge_prompt_version"]) == (JUDGE_MODEL, "ask/judge-v1")
        assert (p["review_model"], p["review_prompt_version"], p["threshold"]) == (
            "gpt-6-luna",
            "auditable-decisions/v1",
            "3",
        )
        assert (p["index_size"], p["n_answerable"], p["n_unanswerable"]) == ("3", "2", "1")
        assert (p["rrf_c"], p["fusion_depth"], p["ts_config"]) == ("60", "50", "english")
        assert p["questions_sha256"] == digest and p["report_path"] == str(result.report_path)
        names = {a.path for a in client.list_artifacts(run_id)}
        assert names == {result.report_path.name, "v1.md", "judge-v1.md"}
        assert run_.data.metrics["recall_at_k_n"] == 2
    experiment = client.get_experiment_by_name("elusion/ask")
    assert len(client.search_runs([experiment.experiment_id])) == 3
    # A second invocation pays nothing and logs three more runs.
    again = run(world, tmp_path, log_mlflow=True)
    assert again.asked == 0 and set(again.run_ids.values()).isdisjoint(result.run_ids.values())
    assert len(client.search_runs([experiment.experiment_id])) == 6


def test_an_mlflow_failure_names_the_report_and_keeps_the_paid_work(
    world, tmp_path, monkeypatch
) -> None:
    import elusion.flows.ask_eval as mod

    def broken(*args, **kwargs):
        raise OSError("no server")

    monkeypatch.setattr(mod, "log_arm_run", broken)
    with pytest.raises(RuntimeError, match=r"report written to .*MLflow logging failed"):
        run(world, tmp_path, log_mlflow=True)
    assert count(world, results.ask_eval_results) == 9


def test_the_report_and_figures_split_refusals_nobody_decided(world, tmp_path) -> None:
    """The lexical arm finds no passage for the zebra question, so no model is asked and the
    ask is refused; the other arms retrieve passages and the model refuses."""
    a = answerer()
    result = run(world, tmp_path, a)
    lexical, vector, hybrid = (result.reports[arm] for arm in ARMS)
    assert (lexical.refusals_correct, lexical.no_passage_unanswerable) == (0, 1)
    assert (vector.refusals_correct, vector.no_passage_unanswerable) == (1, 0)
    assert (hybrid.refusals_correct, hybrid.no_passage_unanswerable) == (1, 0)
    assert lexical.refusals_wrong == lexical.no_passage_answerable == 0
    text = result.report_path.read_text()
    assert (
        "correct refusals (the model refused an unanswerable question): 0 of 1; no passage" in text
    )
    assert "u1: lexical refused, no passage; vector refused; hybrid refused" in text
    assert "Nobody decided that refusal" in text
    assert "asked again on the next run, at a few cents" in text


def test_a_run_stops_before_the_call_that_would_pass_the_limit_on_what_was_billed(
    world, tmp_path
) -> None:
    a = answerer(usage={"cost_usd": 5.0})
    j, e = judge(), FakeEmbedder(model=EMBEDDING_MODEL)
    with pytest.raises(BudgetExceeded, match=r"billed \$5\.000000.*limit of \$1\.000000"):
        run(world, tmp_path, a, j, e, max_usd=1.0)
    assert len(a.calls) == 1 and j.calls == []  # the second ask was never made
    assert count(world, results.ask_eval_results) == 1 and count(world, log.ask_log) == 1

    # Everything paid for is stored: a higher limit continues, and pays for the rest only.
    a2 = answerer()
    second = run(world, tmp_path, a2, j, e, max_usd=10.0)
    assert second.asked == 8 and count(world, results.ask_eval_results) == 9
    assert count(world, log.ask_log) == 9


def test_the_judge_phase_is_stopped_by_the_bill_too(world, tmp_path) -> None:
    j = judge(usage={"cost_usd": 5.0})
    with pytest.raises(BudgetExceeded, match=r"billed \$5\.000000"):
        run(world, tmp_path, answerer(), j, max_usd=2.0)
    assert len(j.calls) == 1 and count(world, results.ask_judgements) == 1
    assert count(world, results.ask_eval_results) == 9  # every ask was already stored
    j2 = judge()
    second = run(world, tmp_path, answerer(), j2, max_usd=2.0)
    assert second.asked == 0 and len(j2.calls) == second.judged > 0


def test_an_answerer_failure_partway_keeps_every_earlier_result(world, tmp_path) -> None:
    calls = []

    def drops_on_the_third(system, message, out):
        calls.append(message)
        if len(calls) == 3:
            raise RuntimeError("connection dropped")
        return good_answer(system, message, out)

    j = judge()
    with pytest.raises(RuntimeError, match="connection dropped"):
        run(world, tmp_path, answerer(drops_on_the_third), j)
    kept = stored_results(world)
    assert len(kept) == 2  # the two asks before the failure
    assert j.calls == []  # the flow stopped before judging
    before = {(r.question, r.arm): r.ask_id for r in kept.values()}

    a2 = answerer()
    second = run(world, tmp_path, a2, j)
    assert second.asked == 7  # the failed ask and every one after it, and no earlier one
    after = stored_results(world)
    assert len(after) == 9 and all(after[key].ask_id == i for key, i in before.items())
    assert len(a2.calls) == 6  # the lexical zebra ask retrieves nothing and calls no model
