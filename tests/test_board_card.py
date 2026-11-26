"""The concept card's pure helpers: Pacific time, the month of a paper, marking quotes."""

from datetime import UTC, datetime, timedelta, timezone

from markupsafe import Markup

from elusion.board.batch import Paper
from elusion.board.card import display_time, field_history, field_month, mark_quotes


def paper(date: str) -> Paper:
    return Paper(doc_id=date, work_id="W", title="t", publication_date=date, text="x")


def test_display_time_is_pacific_and_crosses_the_date_line() -> None:
    assert display_time(datetime(2026, 11, 26, 1, 0, tzinfo=UTC)) == "2026-11-25 17:00 PST"
    assert display_time(datetime(2026, 11, 26, 22, 5, tzinfo=UTC)) == "2026-11-26 14:05 PST"


def test_display_time_follows_daylight_saving_and_any_input_zone() -> None:
    assert display_time(datetime(2026, 7, 1, 1, 0, tzinfo=UTC)) == "2026-06-30 18:00 PDT"
    plus_two = timezone(timedelta(hours=2))
    assert display_time(datetime(2026, 11, 26, 3, 0, tzinfo=plus_two)) == "2026-11-25 17:00 PST"


def test_display_time_takes_a_time_without_a_zone_as_utc() -> None:
    assert display_time(datetime(2026, 11, 26, 1, 0)) == "2026-11-25 17:00 PST"


def test_field_month_is_the_month_and_a_january_first_gives_the_year_alone() -> None:
    assert field_month("2026-07-14") == "2026-07"
    assert field_month("2026-01-02") == "2026-01"
    assert field_month("2026-01-01") == "2026"
    assert field_month("2026-02-01") == "2026-02"


def test_field_history_counts_papers_per_month_sorted_by_the_key() -> None:
    papers = [paper(d) for d in ("2025-03-09", "2024-01-01", "2025-03-30", "2024-12-31")]
    assert field_history(papers) == [("2024", 1), ("2024-12", 1), ("2025-03", 2)]
    assert field_history([]) == []


def test_mark_quotes_escapes_everything_and_marks_the_quote() -> None:
    text = "<b>bold</b> & it sets a stop <i>rule</i> here."
    html = mark_quotes(text, ["stop <i>rule</i>"])
    assert isinstance(html, Markup)
    assert (
        html
        == "&lt;b&gt;bold&lt;/b&gt; &amp; it sets a <mark>stop &lt;i&gt;rule&lt;/i&gt;</mark> here."
    )


def test_mark_quotes_marks_each_quote_and_merges_overlapping_ones() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    assert mark_quotes(text, ["the quick brown", "brown fox jumps"]) == (
        "<mark>the quick brown fox jumps</mark> over the lazy dog"
    )
    assert mark_quotes(text, ["lazy dog", "quick"]) == (
        "the <mark>quick</mark> brown fox jumps over the <mark>lazy dog</mark>"
    )
    assert mark_quotes(text, ["quick brown", "brown"]) == (
        "the <mark>quick brown</mark> fox jumps over the lazy dog"
    )


def test_mark_quotes_ignores_a_quote_that_is_not_there_and_empty_ones() -> None:
    assert mark_quotes("a & b", ["nowhere", "", "  "]) == "a &amp; b"
    assert mark_quotes("a & b", []) == "a &amp; b"


def test_mark_quotes_matches_through_whitespace_like_find_quote() -> None:
    assert mark_quotes("one  two\nthree", ["one two three"]) == "<mark>one  two\nthree</mark>"
