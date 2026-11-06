import csv

import pytest

from elusion.evaluate.grades import (
    COLUMNS,
    GradeRow,
    append_grade_run,
    append_rows,
    grade_cost_usd,
    grade_runs_path,
    graded_ids,
    grades_path,
    quote_is_verbatim,
    read_rows,
    rows_for,
)


def row(doc_id, grader="g", review_model="m", prompt_version="v3", sound=1) -> GradeRow:
    return GradeRow(
        doc_id=doc_id,
        review_model=review_model,
        prompt_version=prompt_version,
        score=3,
        grader=grader,
        grader_prompt_version="cloud-concentration/grade-v1",
        sound=sound,
        quote_verbatim=1,
        rationale="r, with a comma",
        graded_at="2026-11-05T00:00:00+00:00",
    )


def test_grades_path_is_under_grades_dir(tmp_path):
    assert grades_path("c", tmp_path) == tmp_path / "grades" / "c.csv"


def test_append_creates_header_and_dedupes_on_key(tmp_path):
    path = tmp_path / "grades" / "c.csv"
    assert append_rows(path, [row("a"), row("b")]) == 2
    assert append_rows(path, [row("a", sound=0), row("c")]) == 1  # a already graded
    assert append_rows(path, []) == 0
    with path.open(newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == COLUMNS
    assert [r.doc_id for r in read_rows(path)] == ["a", "b", "c"]
    assert read_rows(path)[0].rationale == "r, with a comma"
    assert read_rows(path)[0].sound == 1


def test_key_includes_review_model_prompt_version_and_grader(tmp_path):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a")])
    assert append_rows(path, [row("a", grader="g2")]) == 1
    assert append_rows(path, [row("a", review_model="m2")]) == 1
    assert append_rows(path, [row("a", prompt_version="v2")]) == 1
    assert len(read_rows(path)) == 4


def test_read_rows_missing_file_is_empty(tmp_path):
    assert read_rows(tmp_path / "none.csv") == []


def test_graded_ids_and_rows_for_filter_on_the_triple(tmp_path):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a"), row("b", grader="g2"), row("c", review_model="m2")])
    assert graded_ids(path, "m", "v3", "g") == {"a"}
    assert [r.doc_id for r in rows_for(path, "m", "v3", "g")] == ["a"]
    assert rows_for(path, "m", "v3", "nobody") == []


def test_append_after_partial_last_line_starts_a_new_line(tmp_path):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a")])
    path.write_bytes(path.read_bytes().rstrip(b"\r\n")[:-3])
    append_rows(path, [row("b")])
    assert [r.doc_id for r in read_rows(path)] == ["a", "b"]


def test_sound_must_be_zero_or_one(tmp_path):
    with pytest.raises(ValueError, match="sound"):
        append_rows(tmp_path / "c.csv", [row("a", sound=2)])


def test_malformed_row_raises_naming_the_line(tmp_path):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a")])
    with path.open("a", encoding="utf-8") as f:
        f.write("a,m,v3,3\r\n")
    with pytest.raises(ValueError, match=r"c\.csv:3"):
        read_rows(path)


def test_grade_runs_ledger_appends_and_sums(tmp_path):
    csv_path = tmp_path / "grades" / "c.csv"
    ledger = grade_runs_path(csv_path)
    assert ledger == tmp_path / "grades" / "c.grade-runs.jsonl"
    assert grade_cost_usd(ledger) == 0.0
    append_grade_run(ledger, {"graded": 2, "cost_usd": 0.25})
    append_grade_run(ledger, {"graded": 1, "cost_usd": 0.5})
    assert grade_cost_usd(ledger) == pytest.approx(0.75)


def test_quote_is_verbatim():
    para = "We host substantially all of our services on Amazon Web Services."
    assert quote_is_verbatim("host substantially all of our services", para) is True
    assert quote_is_verbatim("", para) is False  # no quote is not a verbatim quote
    assert quote_is_verbatim("we run mostly on AWS", para) is False  # paraphrase


def test_grade_cost_filters_by_pair(tmp_path):
    ledger = grade_runs_path(tmp_path / "grades" / "c.csv")
    a = {"review_model": "m1", "review_prompt_version": "c/v2"}
    append_grade_run(ledger, {**a, "cost_usd": 0.25})
    append_grade_run(ledger, {**a, "cost_usd": 0.5})
    append_grade_run(ledger, {**a, "review_prompt_version": "c/v3", "cost_usd": 1.0})
    append_grade_run(ledger, {**a, "review_model": "m2", "cost_usd": 2.0})
    assert grade_cost_usd(ledger) == pytest.approx(3.75)
    assert grade_cost_usd(ledger, "m1", "c/v2") == pytest.approx(0.75)
    assert grade_cost_usd(ledger, "m2", "c/v2") == pytest.approx(2.0)
    assert grade_cost_usd(ledger, "m3", "c/v2") == 0.0
