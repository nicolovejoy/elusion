"""`find_quote`: where a quoted passage sits in an abstract, tolerant only of whitespace."""

from elusion.board.quotes import find_quote

TEXT = "Stopping rules decide when a review ends.\nThe team stops at a fixed recall."


def test_an_exact_quote_is_found() -> None:
    assert find_quote(TEXT, "fixed recall") is not None


def test_a_quote_across_a_line_break_matches_a_space() -> None:
    assert find_quote(TEXT, "review ends. The team") is not None


def test_a_quote_whose_case_differs_is_not_found() -> None:
    assert find_quote(TEXT, "stopping rules") is None


def test_an_empty_or_blank_quote_is_not_found() -> None:
    assert find_quote(TEXT, "") is None
    assert find_quote(TEXT, "  \n\t ") is None


def test_the_span_slices_the_quote_out_of_the_text() -> None:
    start, end = find_quote(TEXT, "  review ends.   The team  ")
    assert TEXT[start:end] == "review ends.\nThe team"
