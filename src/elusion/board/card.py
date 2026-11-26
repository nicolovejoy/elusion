"""The small pieces the concept card and the paper note are built from.

Nothing here touches the log or a web request: each function takes plain values and
returns plain values, so each can be tested on its own. The card shows a time as the owner's
clock (Pacific) although the log stores UTC, groups papers by the month they were published
to show how the field's interest rose and fell, and marks the passages a reader quoted in
an abstract. A *quote* here is a passage the reader copied from the abstract as its evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from elusion.board.batch import Paper
from elusion.board.quotes import find_quote

PACIFIC = ZoneInfo("America/Los_Angeles")


def display_time(ts: datetime) -> str:
    """`ts` on the owner's clock, such as "2026-11-26 14:05 PST". A time with no zone is
    taken to be UTC, which is how the log stores them."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(PACIFIC).strftime("%Y-%m-%d %H:%M %Z")


def field_month(publication_date: str) -> str:
    """The month a paper was published, "2026-07" for "2026-07-14". A date on January 1
    gives the year alone, because that date usually means the month is not known."""
    if publication_date[5:] == "01-01":
        return publication_date[:4]
    return publication_date[:7]


def field_history(papers: Iterable[Paper]) -> list[tuple[str, int]]:
    """How many of `papers` were published in each `field_month`, in order of the month."""
    return sorted(Counter(field_month(p.publication_date) for p in papers).items())


def mark_quotes(text: str, quotes: Sequence[str]) -> Markup:
    """`text` as safe HTML with each quote found in it wrapped in <mark>. Everything is
    escaped, text and quotes alike. Quotes that overlap become one mark, and a quote that
    is not in the text is ignored."""
    spans = sorted(span for q in quotes if (span := find_quote(text, q)) is not None)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts: list[Markup] = []
    at = 0
    for start, end in merged:
        parts.append(escape(text[at:start]))
        parts.append(Markup("<mark>{}</mark>").format(text[start:end]))
        at = end
    parts.append(escape(text[at:]))
    return Markup("").join(parts)
