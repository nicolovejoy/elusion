"""The gold questions: the seeded draw, the one-call-per-abstract draft, and the final file.

The draw and the draft read the `abstract_index` of the `index_engine` fixture (a throwaway
database); finalizing reads and writes CSV files under `tmp_path` and needs no database.
No test calls a model: the drafter is a `FakeCompleter`.
"""

import ast
import csv
from pathlib import Path

import pytest

from elusion.ask import index, questions
from elusion.ask.questions import (
    ANSWERABLE_N,
    DRAFT_COLUMNS,
    DRAFT_MODEL,
    FINAL_COLUMNS,
    UNANSWERABLE_N,
    Draft,
    Question,
    draft_message,
    draft_path,
    draft_questions,
    draw_sources,
    finalize_questions,
    questions_path,
    read_questions,
)
from elusion.board.batch import Paper
from elusion.flows.review import BudgetExceeded
from elusion.review.providers import ReviewError
from tests.fakes import FakeCompleter


def doc_id(i: int) -> str:
    return f"d{i:015x}"


def title(i: int) -> str:
    return f"Title zq{i} of paper"


def text(i: int) -> str:
    return f"Abstract body {i} about stopping rules in reviews."


def fill_index(engine, count: int) -> list[str]:
    papers = [Paper(doc_id(i), f"W{i}", title(i), "2024-01-01", text(i)) for i in range(count)]
    index.add_abstracts(engine, papers, review_model="m", prompt_version="c/v1", threshold=3)
    return [p.doc_id for p in papers]


def drafter(model: str = "fake-completer") -> FakeCompleter:
    return FakeCompleter(lambda system, message, out: out(question="What is asked?"), model=model)


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows({c: row.get(c, "") for c in columns} for row in rows)


def draft_row(i: int, **overrides) -> dict:
    row = {
        "question_id": f"q{i:02d}",
        "kind": "answerable",
        "source_doc_id": doc_id(i),
        "drafted_question": f"Drafted {i}?",
        "question": f"Drafted {i}?",
        "keep": "yes",
        "draft_model": "claude-opus-5-5",
        "draft_prompt_version": "ask/draft-v1",
        "drafted_at": "2026-11-26T20:00:00+00:00",
    }
    return {**row, **overrides}


def unanswerable_row(i: int, **overrides) -> dict:
    row = {
        "question_id": f"u{i}",
        "kind": "unanswerable",
        "source_doc_id": "",
        "drafted_question": "",
        "question": f"Owner question {i}?",
        "keep": "yes",
    }
    return {**row, **overrides}


def write_draft(path: Path, answerable: int = 40, struck: int = 15) -> None:
    """A draft with `answerable` drafted rows, the last `struck` of them struck, and u1 to u5."""
    rows = [
        draft_row(i, keep="no" if i > answerable - struck else "yes")
        for i in range(1, answerable + 1)
    ]
    rows += [unanswerable_row(i) for i in range(1, UNANSWERABLE_N + 1)]
    write_csv(path, DRAFT_COLUMNS, rows)


# ---------------------------------------------------------------------------- the draw


def test_the_draw_is_seeded_and_repeatable(index_engine) -> None:
    fill_index(index_engine, 60)
    first = draw_sources(index_engine, n=10, seed=5)
    assert draw_sources(index_engine, n=10, seed=5) == first
    assert len(set(first)) == 10


def test_a_different_seed_draws_other_ids(index_engine) -> None:
    fill_index(index_engine, 60)
    assert draw_sources(index_engine, n=10, seed=1) != draw_sources(index_engine, n=10, seed=2)


def test_the_draw_comes_only_from_the_index(index_engine) -> None:
    indexed = set(fill_index(index_engine, 45))
    held_back = {doc_id(i) for i in range(100, 120)}  # gold ids: never indexed
    drawn = draw_sources(index_engine, n=40)
    assert set(drawn) <= indexed
    assert not set(drawn) & held_back


def test_the_draw_refuses_an_index_smaller_than_n(index_engine) -> None:
    fill_index(index_engine, 39)
    with pytest.raises(ValueError, match="39"):
        draw_sources(index_engine)


def test_the_constants_are_the_briefs() -> None:
    assert (questions.DRAFT_N, ANSWERABLE_N, UNANSWERABLE_N) == (40, 25, 5)
    assert questions.QUESTION_SEED == 20261126
    assert DRAFT_MODEL == "claude-opus-5-5"


# -------------------------------------------------------------------------- the draft


def test_the_drafter_gets_the_prompt_file_and_only_the_abstract_text(
    index_engine, tmp_path
) -> None:
    fill_index(index_engine, 12)
    fake = drafter()
    path = tmp_path / "draft.csv"
    draft_questions(index_engine, fake, path, n=6)
    system_prompt = (
        Path(questions.__file__).parents[1] / "review" / "prompts" / "ask" / "draft-v1.md"
    ).read_text(encoding="utf-8")
    assert len(fake.calls) == 6
    drawn = {r["source_doc_id"]: r for r in read_rows(path)}
    for system, message in fake.calls:
        assert system == system_prompt
        found = [i for i in range(12) if text(i) in message]
        assert len(found) == 1  # one abstract per call, never another
        assert message == draft_message(text(found[0]))
        assert message.startswith("ABSTRACT:\n")
        assert doc_id(found[0]) in drawn
        for i in range(12):
            assert doc_id(i) not in system and doc_id(i) not in message
            assert title(i) not in system and title(i) not in message
    assert set(fake.max_tokens) == {16000}


def test_the_draft_file_has_a_row_per_source_with_model_and_prompt_version(
    index_engine, tmp_path
) -> None:
    fill_index(index_engine, 12)
    fake = FakeCompleter(
        lambda system, message, out: out(question=f"Q about {message[-3:]}?"),
        model="pinned-drafter",
    )
    path = tmp_path / "gold" / "c.questions-draft.csv"
    summary = draft_questions(index_engine, fake, path, n=6)
    assert summary == {
        "drawn": 6,
        "already_drafted": 0,
        "drafted": 6,
        "failed": 0,
        "cost_usd": 0.0,
    }
    with path.open(encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == DRAFT_COLUMNS
    rows = read_rows(path)
    sources = draw_sources(index_engine, n=6)
    assert [r["source_doc_id"] for r in rows] == sources
    assert [r["question_id"] for r in rows] == [f"q{i:02d}" for i in range(1, 7)]
    for row in rows:
        assert row["kind"] == "answerable"
        assert row["keep"] == "yes"
        assert row["question"] == row["drafted_question"] != ""
        assert row["draft_model"] == "pinned-drafter"
        assert row["draft_prompt_version"] == "ask/draft-v1"
        assert row["drafted_at"].endswith("+00:00")


def test_a_rerun_sends_nothing(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft_questions(index_engine, drafter(), path, n=6)
    before = path.read_bytes()
    again = drafter()
    summary = draft_questions(index_engine, again, path, n=6)
    assert again.calls == []
    assert path.read_bytes() == before
    assert summary["drafted"] == 0 and summary["already_drafted"] == 6


def test_a_rerun_with_the_owners_unanswerable_rows_still_sends_nothing(
    index_engine, tmp_path
) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft_questions(index_engine, drafter(), path, n=6)
    rows = read_rows(path) + [unanswerable_row(i) for i in range(1, 6)]
    write_csv(path, DRAFT_COLUMNS, rows)
    again = drafter()
    summary = draft_questions(index_engine, again, path, n=6)
    assert again.calls == []
    assert summary == {
        "drawn": 0,
        "already_drafted": 6,
        "drafted": 0,
        "failed": 0,
        "cost_usd": 0.0,
    }


def test_a_rerun_after_a_crash_drafts_only_the_missing(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    count = 0

    def crash_on_fourth(system, message, out):
        nonlocal count
        count += 1
        if count == 4:
            raise RuntimeError("the network dropped")
        return out(question="What is asked?")

    with pytest.raises(RuntimeError):
        draft_questions(index_engine, FakeCompleter(crash_on_fourth), path, n=6)
    assert len(read_rows(path)) == 3  # the three paid drafts are kept

    again = drafter()
    summary = draft_questions(index_engine, again, path, n=6)
    assert len(again.calls) == 3
    assert summary["already_drafted"] == 3 and summary["drafted"] == 3
    rows = read_rows(path)
    assert [r["source_doc_id"] for r in rows] == draw_sources(index_engine, n=6)
    assert [r["question_id"] for r in rows] == [f"q{i:02d}" for i in range(1, 7)]


def test_a_review_error_holds_its_key_and_is_recorded_not_read_as_a_question(
    index_engine, tmp_path
) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    count = 0

    def refuse_second(system, message, out):
        nonlocal count
        count += 1
        if count == 2:
            return ReviewError("refusal", "claude-opus-5-5", 1000, 200)
        return out(question="What is asked?")

    fake = FakeCompleter(refuse_second, model="claude-opus-5-5")
    summary = draft_questions(index_engine, fake, path, n=4)
    assert summary["failed"] == 1 and summary["drafted"] == 3
    assert summary["cost_usd"] == pytest.approx(1000 * 4.0e-6 + 200 * 20.0e-6)
    rows = read_rows(path)
    assert len(rows) == 4  # the billed call holds its key: a row, not a gap
    bad = rows[1]
    assert bad["question"] == "" and bad["keep"] == "no"
    assert "refusal" in bad["drafted_question"]
    assert bad["source_doc_id"] == draw_sources(index_engine, n=4)[1]

    again = drafter("claude-opus-5-5")
    draft_questions(index_engine, again, path, n=4)
    assert again.calls == []  # not tried again: it was billed


def test_an_empty_question_is_recorded_as_a_failure(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    fake = FakeCompleter(lambda system, message, out: out(question="   "))
    summary = draft_questions(index_engine, fake, path, n=2)
    assert summary["failed"] == 2 and summary["drafted"] == 0
    assert {r["keep"] for r in read_rows(path)} == {"no"}
    assert {r["question"] for r in read_rows(path)} == {""}


def test_the_spend_guard_raises_before_any_call(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    fake = drafter("claude-opus-5-5")  # a priced model
    with pytest.raises(BudgetExceeded, match="refusing to start"):
        draft_questions(index_engine, fake, path, n=6, max_usd=0.0001)
    assert fake.calls == []
    assert not path.exists()


def test_the_spend_guard_counts_only_what_is_still_to_draft(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft_questions(index_engine, drafter("claude-opus-5-5"), path, n=6)
    again = drafter("claude-opus-5-5")
    draft_questions(index_engine, again, path, n=6, max_usd=0.0)  # nothing left: no raise
    assert again.calls == []


def test_the_draft_never_imports_the_retrieval_or_the_answer() -> None:
    """The gold rule: a question's source id lives in the questions files only. The module
    that holds it must not be able to hand it to `retrieve` or `ask`."""
    tree = ast.parse(Path(questions.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not {"elusion.ask.retrieve", "elusion.ask.answer", "elusion.ask.ask"} & imported


def test_draft_message_is_the_text_and_nothing_else() -> None:
    assert draft_message("some text") == "ABSTRACT:\nsome text"
    assert set(Draft.model_fields) == {"question"}


# ------------------------------------------------------------------------- the final file


def test_the_paths_sit_beside_the_other_gold_files(tmp_path) -> None:
    assert draft_path("c", tmp_path) == tmp_path / "gold" / "c.questions-draft.csv"
    assert questions_path("c", tmp_path) == tmp_path / "gold" / "c.questions.csv"


def test_finalize_writes_25_and_5_with_origins(tmp_path) -> None:
    from datetime import UTC, datetime

    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    rows[2]["question"] = "A rewritten third question?"
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    summary = finalize_questions(draft, final, now=datetime(2026, 11, 27, 1, 2, 3, tzinfo=UTC))
    assert summary == {"answerable": 25, "unanswerable": 5, "rewritten": 1, "struck": 15}
    with final.open(encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == FINAL_COLUMNS
    out = read_rows(final)
    assert [r["question_id"] for r in out] == [f"q{i:02d}" for i in range(1, 26)] + [
        f"u{i}" for i in range(1, 6)
    ]
    assert out[2]["origin"] == "rewritten" and out[2]["question"] == "A rewritten third question?"
    assert out[0]["origin"] == "drafted" and out[0]["source_doc_id"] == doc_id(1)
    assert out[0]["draft_model"] == "claude-opus-5-5"
    assert out[0]["draft_prompt_version"] == "ask/draft-v1"
    assert out[25]["origin"] == "owner" and out[25]["source_doc_id"] == ""
    assert {r["finalized_at"] for r in out} == {"2026-11-27T01:02:03+00:00"}


def test_finalize_ignores_struck_rows(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    # a struck row may be empty or half-edited: it is not validated beyond its keep value
    rows[30]["question"] = ""
    rows[31]["source_doc_id"] = ""
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    finalize_questions(draft, final)
    assert [r["question_id"] for r in read_rows(final)][:25] == [f"q{i:02d}" for i in range(1, 26)]


def test_finalize_refuses_wrong_counts_with_the_exact_message(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft, answerable=40, struck=14)  # keeps 26
    with pytest.raises(
        ValueError,
        match="^the draft keeps 26 answerable and 5 unanswerable questions; it must keep 25 and 5$",
    ):
        finalize_questions(draft, final)
    write_draft(draft)
    write_csv(draft, DRAFT_COLUMNS, [r for r in read_rows(draft) if r["question_id"] != "u5"])
    with pytest.raises(ValueError, match="keeps 25 answerable and 4 unanswerable"):
        finalize_questions(draft, final)
    assert not final.exists()


@pytest.mark.parametrize("keep", ["maybe", "", "1", "true"])
def test_finalize_refuses_a_keep_value_that_is_not_yes_or_no(tmp_path, keep) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[3]["keep"] = keep
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 5: keep must be yes or no"):
        finalize_questions(draft, final)
    assert not final.exists()


def test_finalize_accepts_yes_and_no_in_any_case(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["keep"] = " Yes "
    rows[39]["keep"] = "NO"
    write_csv(draft, DRAFT_COLUMNS, rows)
    assert finalize_questions(draft, final)["answerable"] == 25


def test_finalize_refuses_a_kept_row_without_a_question(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["question"] = "  "
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 2: .*question is empty"):
        finalize_questions(draft, final)


def test_finalize_refuses_an_unanswerable_row_with_a_source(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[-1]["source_doc_id"] = doc_id(1)
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 46: .*unanswerable .*no source_doc_id"):
        finalize_questions(draft, final)


def test_finalize_refuses_an_answerable_row_without_a_source(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[1]["source_doc_id"] = ""
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 3: .*answerable .*needs a source_doc_id"):
        finalize_questions(draft, final)


def test_finalize_refuses_repeated_ids(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[5]["question_id"] = "q01"
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 7: question_id q01 repeats line 2"):
        finalize_questions(draft, final)


def test_finalize_refuses_an_unknown_kind(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["kind"] = "easy"
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 2: kind must be"):
        finalize_questions(draft, final)


def test_finalize_never_overwrites(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    finalize_questions(draft, final)
    before = final.read_bytes()
    with pytest.raises(FileExistsError):
        finalize_questions(draft, final)
    assert final.read_bytes() == before


def test_finalize_refuses_a_file_that_is_not_a_draft(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_csv(draft, ["a", "b"], [{"a": "1", "b": "2"}])
    with pytest.raises(ValueError, match="columns"):
        finalize_questions(draft, final)


def test_read_questions_round_trips(tmp_path) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    rows[0]["question"] = 'Quoted, "tricky"\nmultiline? é'
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    finalize_questions(draft, final)
    found = read_questions(final)
    assert len(found) == 30
    assert found[0] == Question(
        question_id="q01",
        kind="answerable",
        source_doc_id=doc_id(1),
        question='Quoted, "tricky"\nmultiline? é',
        origin="rewritten",
        draft_model="claude-opus-5-5",
        draft_prompt_version="ask/draft-v1",
    )
    assert [q.kind for q in found] == ["answerable"] * 25 + ["unanswerable"] * 5
    assert found[-1].source_doc_id == "" and found[-1].origin == "owner"
