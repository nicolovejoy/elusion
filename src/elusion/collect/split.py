"""Turn one filing's Item 1A text into review-sized documents.

EDGAR-CORPUS stores each section as plain text with one newline per source paragraph,
so a paragraph is simply a non-empty line. Headings, bullets and table fragments are
too short to be reviewable on their own and are dropped by the word threshold.
"""

from __future__ import annotations

import hashlib


def split_paragraphs(text: str, min_words: int = 30) -> list[str]:
    """Return the paragraphs of `text` with at least `min_words` words, in order."""
    out: list[str] = []
    for line in text.splitlines():
        para = line.strip()
        if para and len(para.split()) >= min_words:
            out.append(para)
    return out


def doc_id(cik: str, year: int, text: str) -> str:
    """Stable 16-hex-char id for a paragraph.

    Hashing the text rather than the paragraph index means a change to the splitter
    does not renumber existing documents, which is what keeps re-runs idempotent.
    """
    h = hashlib.sha256(f"{cik}\x1f{year}\x1f{text}".encode()).hexdigest()
    return h[:16]
