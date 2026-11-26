"""The first_read flow with a fake reader, against a throwaway database and temp Parquet.

Each test files its papers under a concept slug of its own.
"""

import uuid

import pytest
from prefect.testing.utilities import prefect_test_harness

from elusion.board import log
from elusion.flows.review import BudgetExceeded
from elusion.review.prompt import load_prompt
from elusion.review.providers import Reading, ReviewError, reader_message
from tests.fakes import FakeReader
from tests.test_board_batch import write_year

MODEL = "claude-haiku-5-5"
STOPS = "A team stops reviewing when recall reaches a fixed target. Costs also matter."
OTHER = "The bus stops at the station. A stopping rule is not used."
TERMS = ["stopping rule", "stops reviewing"]


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


@pytest.fixture
def concept(board_engine) -> str:
    slug = f"c{uuid.uuid4().hex[:10]}"
    log.add_concept(
        board_engine, slug, "When should a review stop?", "owner", {"search_terms": TERMS}
    )
    return slug


@pytest.fixture
def abstracts(tmp_path):
    write_year(
        tmp_path,
        2024,
        [
            {"doc_id": "d1", "text": STOPS},
            {"doc_id": "d2", "text": OTHER},
            {"doc_id": "d3", "text": "Nothing relevant at all."},
        ],
    )
    return tmp_path


def holds_if_stops(message: str) -> Reading:
    abstract = message.split("ABSTRACT:\n", 1)[1]
    if abstract == STOPS:
        return Reading(
            holds=True,
            quote="stops reviewing when recall reaches a fixed target",
            answer="Stop at a recall target.",
            rationale="It gives a stopping criterion.",
        )
    return Reading(holds=False, quote="", answer="", rationale="Other sense.")


def run(board_engine, abstracts, concept, reader, **kw):
    from elusion.flows.first_read import first_read

    return first_read(concept, reader, engine=board_engine, abstracts_dir=abstracts, **kw)


def readings(board_engine, concept):
    return {e.doc_id: e for e in log.entries(board_engine, concept, kind="reading")}


def test_the_reader_gets_the_prompt_file_and_the_exact_message(
    board_engine, abstracts, concept
) -> None:
    reader = FakeReader(holds_if_stops)
    run(board_engine, abstracts, concept, reader)
    system = load_prompt("first-read", "v1").text
    assert reader.calls == [
        (system, reader_message("When should a review stop?", STOPS)),
        (system, reader_message("When should a review stop?", OTHER)),
    ]


def test_a_holds_reading_is_stored_with_model_prompt_version_quote_and_answer(
    board_engine, abstracts, concept
) -> None:
    summary = run(board_engine, abstracts, concept, FakeReader(holds_if_stops))
    row = readings(board_engine, concept)["d1"]
    assert (row.verdict, row.model, row.prompt_version) == ("holds", MODEL, "first-read/v1")
    assert row.quote == "stops reviewing when recall reaches a fixed target"
    assert row.body == "It gives a stopping criterion."
    assert row.detail["answer"] == "Stop at a recall target."
    assert row.detail["served_model"] == MODEL
    assert row.detail["input_tokens"] == 100
    assert readings(board_engine, concept)["d2"].verdict == "does_not_hold"
    assert "d3" not in readings(board_engine, concept)
    assert summary == {
        "matched": 2,
        "already_read": 0,
        "read": 2,
        "holds": 1,
        "does_not_hold": 1,
        "failed": 0,
        "cost_usd": 0.0,
    }


def test_a_quote_that_is_not_verbatim_becomes_a_failed_row_keeping_the_models_quote(
    board_engine, abstracts, concept
) -> None:
    invented = Reading(holds=True, quote="stops when recall is reached", answer="a", rationale="r")
    summary = run(board_engine, abstracts, concept, FakeReader(lambda m: invented))
    row = readings(board_engine, concept)["d1"]
    assert row.verdict == "failed"
    assert row.quote is None
    assert row.detail["stop_reason"] == "quote_not_verbatim"
    assert row.detail["model_holds"] is True
    assert row.detail["model_quote"] == "stops when recall is reached"
    assert summary["failed"] == 2
    assert summary["read"] == 2


def test_a_review_error_becomes_a_failed_row_with_its_cost(
    board_engine, abstracts, concept
) -> None:
    def refuse(message: str):
        return ReviewError("refusal", MODEL, 1000, 100)

    summary = run(board_engine, abstracts, concept, FakeReader(refuse))
    row = readings(board_engine, concept)["d1"]
    assert (row.verdict, row.model, row.prompt_version) == ("failed", MODEL, "first-read/v1")
    assert row.detail["stop_reason"] == "refusal"
    assert (row.detail["input_tokens"], row.detail["output_tokens"]) == (1000, 100)
    assert row.detail["cost_usd"] > 0
    assert summary["failed"] == 2
    assert summary["cost_usd"] == pytest.approx(2 * row.detail["cost_usd"])


def test_a_second_run_makes_no_call_and_reports_already_read(
    board_engine, abstracts, concept
) -> None:
    run(board_engine, abstracts, concept, FakeReader(holds_if_stops))
    reader = FakeReader(holds_if_stops)
    summary = run(board_engine, abstracts, concept, reader)
    assert reader.calls == []
    assert summary["already_read"] == 2
    assert summary["read"] == 0


def test_a_failed_paper_is_not_called_again(board_engine, abstracts, concept) -> None:
    def fail_d1(message: str):
        if STOPS in message:
            return ReviewError("max_tokens", MODEL, 10, 1024)
        return holds_if_stops(message)

    run(board_engine, abstracts, concept, FakeReader(fail_d1))
    reader = FakeReader(holds_if_stops)
    summary = run(board_engine, abstracts, concept, reader)
    assert reader.calls == []
    assert summary["already_read"] == 2
    assert readings(board_engine, concept)["d1"].verdict == "failed"


def test_the_spend_guard_raises_before_any_call(board_engine, abstracts, concept) -> None:
    reader = FakeReader(holds_if_stops)
    with pytest.raises(BudgetExceeded, match=r"projected cost \$0\.\d+ .*\$0\.000001"):
        run(board_engine, abstracts, concept, reader, max_usd=0.000001)
    assert reader.calls == []
    assert readings(board_engine, concept) == {}


def test_a_concept_without_search_terms_is_refused(board_engine, abstracts) -> None:
    slug = f"c{uuid.uuid4().hex[:10]}"
    log.add_concept(board_engine, slug, "no terms", "owner")
    with pytest.raises(ValueError, match="search_terms"):
        run(board_engine, abstracts, slug, FakeReader(holds_if_stops))


def test_a_paper_in_two_year_files_is_called_once(board_engine, abstracts, concept) -> None:
    write_year(abstracts, 2023, [{"doc_id": "d1", "text": STOPS}])
    reader = FakeReader(holds_if_stops)
    summary = run(board_engine, abstracts, concept, reader)
    assert len(reader.calls) == 2
    assert summary["matched"] == 2
    assert summary["read"] == 2


def test_an_unexpected_error_propagates_after_logging_the_partial_summary(
    board_engine, abstracts, concept, caplog
) -> None:
    def outage(message: str):
        if OTHER in message:
            return RuntimeError("connection reset")
        return holds_if_stops(message)

    with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="connection reset"):
        run(board_engine, abstracts, concept, FakeReader(outage))

    rows = readings(board_engine, concept)
    assert set(rows) == {"d1"}  # no row for the paper the outage stopped on
    stopped = [r.getMessage() for r in caplog.records if "d2" in r.getMessage()]
    assert stopped and "'read': 1" in stopped[0] and "'holds': 1" in stopped[0]

    reader = FakeReader(holds_if_stops)
    summary = run(board_engine, abstracts, concept, reader)
    assert [m for _, m in reader.calls] == [reader_message("When should a review stop?", OTHER)]
    assert summary["already_read"] == 1
    assert summary["read"] == 1
