"""Finding a quoted passage in an abstract.

A model that says a paper "holds" a concept must point at the words that show it. Models
paraphrase, and a paraphrase is not evidence, so the quote is checked against the stored
abstract: it must appear there character for character. The one tolerance is whitespace,
because an abstract's line breaks and double spaces are an accident of how it was stored,
not part of what the authors wrote.
"""

from __future__ import annotations

import re


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """The (start, end) span in `text` of the first place `quote` appears, or None.

    The match is exact and case-sensitive, except that any run of whitespace in the quote
    matches any run of whitespace in the text, and the quote's own leading and trailing
    whitespace is ignored. An empty or all-whitespace quote matches nothing.
    """
    tokens = quote.split()
    if not tokens:
        return None
    match = re.search(r"\s+".join(re.escape(token) for token in tokens), text)
    return (match.start(), match.end()) if match else None
