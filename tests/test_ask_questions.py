"""The gold questions: the seeded draw, the one-call-per-abstract draft, and the final file.

The draw and the draft read the `abstract_index` of the `index_engine` fixture (a throwaway
database); finalizing reads and writes CSV files under `tmp_path` and needs no database.
No test calls a model: the drafter is a `FakeCompleter`.
"""

import ast
import csv
import random
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


def fill_index(engine, count: int, start: int = 0) -> list[str]:
    """Index abstracts `start` to `count - 1`, inserted in a shuffled order."""
    papers = [
        Paper(doc_id(i), f"W{i}", title(i), "2024-01-01", text(i)) for i in range(start, count)
    ]
    random.Random(7).shuffle(papers)
    index.add_abstracts(engine, papers, review_model="m", prompt_version="c/v1", threshold=3)
    return [p.doc_id for p in papers]


def draft(engine, fake, path, **kw):
    """`draft_questions` with no gold label held back, unless the test says so."""
    return draft_questions(engine, fake, path, **{"held_back": frozenset(), **kw})


def finalize(draft_csv, final_csv, engine, **kw):
    return finalize_questions(draft_csv, final_csv, engine, **{"held_back": frozenset(), **kw})


@pytest.fixture
def indexed(index_engine):
    """An index holding the abstracts that `write_draft`'s rows name."""
    fill_index(index_engine, 41)
    return index_engine


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
        "served_model": "claude-opus-5-5",
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
    ids = fill_index(index_engine, 60)  # inserted in a shuffled order
    first = draw_sources(index_engine, n=10, seed=5)
    assert draw_sources(index_engine, n=10, seed=5) == first
    assert first == random.Random(5).sample(sorted(ids), 10)


def test_a_different_seed_draws_other_ids(index_engine) -> None:
    fill_index(index_engine, 60)
    assert draw_sources(index_engine, n=10, seed=1) != draw_sources(index_engine, n=10, seed=2)


def test_the_draw_comes_only_from_the_index(index_engine) -> None:
    indexed = set(fill_index(index_engine, 45))
    held_back = {doc_id(i) for i in range(100, 120)}  # gold ids: never indexed
    drawn = draw_sources(index_engine, n=40)
    assert set(drawn) <= indexed
    assert not set(drawn) & held_back


def test_the_draw_leaves_out_held_back_ids_that_are_in_the_index(index_engine) -> None:
    """A gold label added after the index was built: the id is indexed and held back."""
    ids = fill_index(index_engine, 60)
    held = set(sorted(ids)[:40])
    drawn = draw_sources(index_engine, n=15, seed=3, held_back=held)
    assert not set(drawn) & held
    assert drawn == random.Random(3).sample(sorted(set(ids) - held), 15)
    with pytest.raises(ValueError, match="20 abstracts to draw from"):
        draw_sources(index_engine, n=21, held_back=held)


def test_drafting_never_sends_a_held_back_abstract(index_engine, tmp_path) -> None:
    ids = fill_index(index_engine, 20)
    held = set(sorted(ids)[:10])
    fake = drafter()
    path = tmp_path / "draft.csv"
    draft(index_engine, fake, path, n=10, held_back=held)
    assert len(fake.calls) == 10
    assert not {r["source_doc_id"] for r in read_rows(path)} & held
    for _, message in fake.calls:
        assert not any(text(int(d[1:], 16)) in message for d in held)


def test_the_draw_refuses_an_index_smaller_than_n(index_engine) -> None:
    fill_index(index_engine, 39)
    with pytest.raises(ValueError, match="39"):
        draw_sources(index_engine)


def test_the_column_lists() -> None:
    assert DRAFT_COLUMNS[-1] == "served_model" and FINAL_COLUMNS[-1] == "served_model"
    assert Question("q01", "answerable", "d", "q?", "drafted", "m", "p").served_model == ""


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
    draft(index_engine, fake, path, n=6)
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
    summary = draft(index_engine, fake, path, n=6)
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
        assert row["served_model"] == "pinned-drafter"
        assert row["drafted_at"].endswith("+00:00")


def test_a_rerun_sends_nothing(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft(index_engine, drafter(), path, n=6)
    before = path.read_bytes()
    again = drafter()
    summary = draft(index_engine, again, path, n=6)
    assert again.calls == []
    assert path.read_bytes() == before
    assert summary["drafted"] == 0 and summary["already_drafted"] == 6


def test_a_rerun_with_the_owners_unanswerable_rows_still_sends_nothing(
    index_engine, tmp_path
) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft(index_engine, drafter(), path, n=6)
    rows = read_rows(path) + [unanswerable_row(i) for i in range(1, 6)]
    write_csv(path, DRAFT_COLUMNS, rows)
    again = drafter()
    summary = draft(index_engine, again, path, n=6)
    assert again.calls == []
    assert summary == {
        "drawn": 6,
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
        draft(index_engine, FakeCompleter(crash_on_fourth), path, n=6)
    # the three paid drafts are kept; the other three are pending, not missing
    assert [r["keep"] for r in read_rows(path)] == ["yes"] * 3 + ["pending"] * 3

    again = drafter()
    summary = draft(index_engine, again, path, n=6)
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
    summary = draft(index_engine, fake, path, n=4)
    assert summary["failed"] == 1 and summary["drafted"] == 3
    assert summary["cost_usd"] == pytest.approx(1000 * 4.0e-6 + 200 * 20.0e-6)
    rows = read_rows(path)
    assert len(rows) == 4  # the billed call holds its key: a row, not a gap
    bad = rows[1]
    assert bad["question"] == "" and bad["keep"] == "no"
    assert bad["served_model"] == "claude-opus-5-5"
    assert "refusal" in bad["drafted_question"]
    assert bad["source_doc_id"] == draw_sources(index_engine, n=4)[1]

    again = drafter("claude-opus-5-5")
    draft(index_engine, again, path, n=4)
    assert again.calls == []  # not tried again: it was billed


def test_an_empty_question_is_recorded_as_a_failure(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    fake = FakeCompleter(lambda system, message, out: out(question="   "))
    summary = draft(index_engine, fake, path, n=2)
    assert summary["failed"] == 2 and summary["drafted"] == 0
    assert {r["keep"] for r in read_rows(path)} == {"no"}
    assert {r["question"] for r in read_rows(path)} == {""}


def test_the_spend_guard_raises_before_any_call(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    fake = drafter("claude-opus-5-5")  # a priced model
    with pytest.raises(BudgetExceeded, match="refusing to start"):
        draft(index_engine, fake, path, n=6, max_usd=0.0001)
    assert fake.calls == []
    assert not path.exists()


def test_the_spend_guard_counts_only_what_is_still_to_draft(index_engine, tmp_path) -> None:
    from elusion.review.pricing import cost_usd

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
        draft(index_engine, FakeCompleter(crash_on_fourth, model="claude-opus-5-5"), path, n=6)
    # every abstract is about the same length, so each call projects to about this much
    one = cost_usd("claude-opus-5-5", len(text(0)) // 3 + 400, 150)
    again = drafter("claude-opus-5-5")
    # a limit above the three calls left and below all six: a guard that counted the
    # drafted rows too would refuse
    summary = draft(index_engine, again, path, n=6, max_usd=3.5 * one)
    assert len(again.calls) == 3 and summary["drafted"] == 3

    done = drafter("claude-opus-5-5")
    draft(index_engine, done, path, n=6, max_usd=0.0)  # nothing left: no raise
    assert done.calls == []


def test_the_draw_never_moves_when_the_index_grows(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    seen = []
    count = 0

    def crash_on_fourth(system, message, out):
        nonlocal count
        count += 1
        if count == 1:
            seen.append(read_rows(path))  # the draw is on disk before the first call
        if count == 4:
            raise RuntimeError("the network dropped")
        return out(question="What is asked?")

    with pytest.raises(RuntimeError):
        draft(index_engine, FakeCompleter(crash_on_fourth), path, n=6)
    assert [r["keep"] for r in seen[0]] == ["pending"] * 6
    assert {r["question"] for r in seen[0]} == {""}
    crashed = read_rows(path)
    assert [r["keep"] for r in crashed] == ["yes"] * 3 + ["pending"] * 3

    fill_index(index_engine, 60, start=12)  # the index grows: a fresh draw would differ
    assert draw_sources(index_engine, n=6) != [r["source_doc_id"] for r in crashed]
    again = drafter()
    draft(index_engine, again, path, n=6)
    assert len(again.calls) == 3
    rows = read_rows(path)
    assert [r["source_doc_id"] for r in rows] == [r["source_doc_id"] for r in crashed]
    assert [r["question_id"] for r in rows] == [r["question_id"] for r in crashed]
    assert len({r["question_id"] for r in rows}) == 6
    assert {r["keep"] for r in rows} == {"yes"}
    assert rows[:3] == crashed[:3]  # the first three paid drafts are untouched


def test_a_failed_row_set_back_to_pending_is_drafted_again(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    refuse = FakeCompleter(lambda s, m, out: ReviewError("refusal", "fake-completer", 5, 5))
    assert draft(index_engine, refuse, path, n=3)["failed"] == 3
    rows = read_rows(path)
    rows[1]["keep"] = "pending"
    write_csv(path, DRAFT_COLUMNS, rows)
    again = drafter()
    draft(index_engine, again, path, n=3)
    assert len(again.calls) == 1
    assert [r["keep"] for r in read_rows(path)] == ["no", "yes", "no"]


def test_deleting_a_draft_row_does_not_redraw_it(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft(index_engine, drafter(), path, n=4)
    rows = read_rows(path)
    write_csv(path, DRAFT_COLUMNS, rows[:3])
    again = drafter()
    draft(index_engine, again, path, n=4)
    assert again.calls == [] and len(read_rows(path)) == 3


def test_the_draft_is_read_as_a_spreadsheet_saves_it(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    draft(index_engine, drafter(), path, n=4)
    rows = read_rows(path)
    write_csv(path, DRAFT_COLUMNS, [*rows, {}, {}])  # two rows of empty cells
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())  # a byte-order mark
    again = drafter()
    summary = draft(index_engine, again, path, n=4)
    assert again.calls == [] and summary["already_drafted"] == 4


def test_a_semicolon_delimited_draft_is_refused_by_name(index_engine, tmp_path) -> None:
    fill_index(index_engine, 12)
    path = tmp_path / "draft.csv"
    path.write_text(";".join(DRAFT_COLUMNS) + "\nq01;answerable;x;;;yes;;;;\n", encoding="utf-8")
    fake = drafter()
    with pytest.raises(ValueError, match="semicolon-delimited"):
        draft(index_engine, fake, path, n=4)
    assert fake.calls == []
    with pytest.raises(ValueError, match="semicolon-delimited"):
        finalize(path, tmp_path / "final.csv", index_engine)
    assert not (tmp_path / "final.csv").exists()


def test_a_question_with_a_comma_a_quote_and_a_line_break_survives_a_write_and_read(
    tmp_path,
) -> None:
    tricky = 'Why, "exactly",\nis it so?'
    path = tmp_path / "draft.csv"
    questions._write_draft(path, [draft_row(1, question=tricky, drafted_question=tricky)])
    assert questions._read_draft_rows(path)[0]["question"] == tricky


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


def test_finalize_writes_25_and_5_with_origins(tmp_path, indexed) -> None:
    from datetime import UTC, datetime

    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    rows[2]["question"] = "A rewritten third question?"
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    summary = finalize(draft, final, indexed, now=datetime(2026, 11, 27, 1, 2, 3, tzinfo=UTC))
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
    assert out[0]["served_model"] == "claude-opus-5-5"
    assert out[25]["origin"] == "owner" and out[25]["source_doc_id"] == ""
    assert out[25]["served_model"] == ""
    assert {r["finalized_at"] for r in out} == {"2026-11-27T01:02:03+00:00"}


def test_finalize_ignores_struck_rows(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    # a struck row may be empty or half-edited: it is not validated beyond its keep value
    rows[30]["question"] = ""
    rows[31]["source_doc_id"] = ""
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    finalize(draft, final, indexed)
    assert [r["question_id"] for r in read_rows(final)][:25] == [f"q{i:02d}" for i in range(1, 26)]


def test_finalize_refuses_wrong_counts_with_the_exact_message(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft, answerable=40, struck=14)  # keeps 26
    with pytest.raises(
        ValueError,
        match="^the draft keeps 26 answerable and 5 unanswerable questions; it must keep 25 and 5$",
    ):
        finalize(draft, final, indexed)
    write_draft(draft)
    write_csv(draft, DRAFT_COLUMNS, [r for r in read_rows(draft) if r["question_id"] != "u5"])
    with pytest.raises(ValueError, match="keeps 25 answerable and 4 unanswerable"):
        finalize(draft, final, indexed)
    assert not final.exists()


@pytest.mark.parametrize("keep", ["maybe", "", "1", "true"])
def test_finalize_refuses_a_keep_value_that_is_not_yes_or_no(tmp_path, indexed, keep) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[3]["keep"] = keep
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 5: keep must be yes or no"):
        finalize(draft, final, indexed)
    assert not final.exists()


def test_finalize_accepts_yes_and_no_in_any_case(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["keep"] = " Yes "
    rows[39]["keep"] = "NO"
    write_csv(draft, DRAFT_COLUMNS, rows)
    assert finalize(draft, final, indexed)["answerable"] == 25


def test_finalize_refuses_a_kept_row_without_a_question(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["question"] = "  "
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 2: .*question is empty"):
        finalize(draft, final, indexed)


def test_finalize_refuses_an_unanswerable_row_with_a_source(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[-1]["source_doc_id"] = doc_id(1)
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 46: .*unanswerable .*no source_doc_id"):
        finalize(draft, final, indexed)


def test_finalize_refuses_an_answerable_row_without_a_source(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[1]["source_doc_id"] = ""
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 3: .*answerable .*needs a source_doc_id"):
        finalize(draft, final, indexed)


def test_finalize_refuses_repeated_ids(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[5]["question_id"] = "q01"
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 7: question_id q01 repeats line 2"):
        finalize(draft, final, indexed)


def test_finalize_refuses_an_unknown_kind(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[0]["kind"] = "easy"
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 2: kind must be"):
        finalize(draft, final, indexed)


def test_finalize_never_overwrites(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    finalize(draft, final, indexed)
    before = final.read_bytes()
    with pytest.raises(FileExistsError):
        finalize(draft, final, indexed)
    assert final.read_bytes() == before


def test_finalize_refuses_a_row_still_pending(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[2].update(keep="pending", question="", drafted_question="")
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 4: q03 is still pending"):
        finalize(draft, final, indexed)
    assert not final.exists()


def test_finalize_refuses_a_source_that_is_not_in_the_index(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    rows = read_rows(draft)
    rows[3]["source_doc_id"] = "1.23E+15"  # what a spreadsheet makes of 1230000000000000
    write_csv(draft, DRAFT_COLUMNS, rows)
    with pytest.raises(ValueError, match=r"line 5: q04 .*'1.23E\+15'.*not in the index"):
        finalize(draft, final, indexed)
    assert not final.exists()


def test_finalize_refuses_a_source_that_is_held_back(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    with pytest.raises(ValueError, match=rf"line 4: q03 .*{doc_id(3)}.*held back"):
        finalize(draft, final, indexed, held_back={doc_id(3)})
    # a struck row's source is not checked
    finalize(draft, final, indexed, held_back={doc_id(30)})


def test_finalize_reads_a_spreadsheets_file(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)
    write_csv(draft, DRAFT_COLUMNS, [*read_rows(draft), {}, {}])
    draft.write_bytes(b"\xef\xbb\xbf" + draft.read_bytes())
    assert finalize(draft, final, indexed)["answerable"] == 25


def test_a_crash_while_writing_leaves_no_final_file(tmp_path, indexed, monkeypatch) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_draft(draft)

    def crash(self, rows):
        raise RuntimeError("disk full")

    monkeypatch.setattr(csv.DictWriter, "writerows", crash)
    with pytest.raises(RuntimeError):
        finalize(draft, final, indexed)
    assert not final.exists() and not list(tmp_path.glob("final.csv*"))
    monkeypatch.undo()
    finalize(draft, final, indexed)  # and the rerun works
    assert final.exists() and not list(tmp_path.glob("*.tmp"))


def test_finalize_refuses_a_file_that_is_not_a_draft(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    write_csv(draft, ["a", "b"], [{"a": "1", "b": "2"}])
    with pytest.raises(ValueError, match="columns"):
        finalize(draft, final, indexed)


def test_read_questions_round_trips(tmp_path, indexed) -> None:
    draft, final = tmp_path / "draft.csv", tmp_path / "final.csv"
    rows = [draft_row(i, keep="no" if i > 25 else "yes") for i in range(1, 41)]
    rows[0]["question"] = 'Quoted, "tricky"\nmultiline? é'
    rows += [unanswerable_row(i) for i in range(1, 6)]
    write_csv(draft, DRAFT_COLUMNS, rows)
    finalize(draft, final, indexed)
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
        served_model="claude-opus-5-5",
    )
    assert [q.kind for q in found] == ["answerable"] * 25 + ["unanswerable"] * 5
    assert found[-1].source_doc_id == "" and found[-1].origin == "owner"
