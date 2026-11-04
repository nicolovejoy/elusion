"""The owner's blind audit: label a few paragraphs by hand, then compare with the judge.

The judge model's labels are only trustworthy if a human agrees with them on a sample.
This module picks that sample (half from the flagged pile, half from the discard pile,
chosen at random with a seed) and runs a plain terminal session: it shows one paragraph
at a time, with nothing that hints at what the judge or the review said, and appends the
owner's yes/no to the gold CSV the moment it is typed. It is a human session, so it is
not a Prefect flow and needs no server.
"""

from __future__ import annotations

import random
import shutil
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer

from elusion.evaluate.gold import (
    DEFAULT_SEED,
    HUMAN,
    GoldRow,
    append_rows,
    gold_path,
    judge_rows,
    labeled_ids,
)

PROMPT = "[y]es / [n]o / [s]kip / [q]uit: "


def select_for_audit(path: Path, n: int, seed: int) -> list[tuple[str, str]]:
    """(doc_id, pile) pairs: n//2 flagged and n//2 discard, shuffled together.

    Already-audited docs are skipped. The shuffle (same seeded rng) keeps the pile from
    being guessable from the position in the session.

    Candidates are sorted before the seeded draw so the pick depends only on the CSV and
    the seed. A pile with fewer candidates than n//2 is taken whole.
    """
    done = labeled_ids(path, HUMAN)
    rng = random.Random(seed)
    judged = judge_rows(path)
    picked: list[tuple[str, str]] = []
    for pile in ("flagged", "discard"):
        ids = sorted({r.doc_id for r in judged if r.pile == pile} - done)
        take = min(n // 2, len(ids))
        picked += [(d, pile) for d in rng.sample(ids, take)]
    rng.shuffle(picked)
    return picked


def run_session(
    path: Path,
    texts: dict[str, str],
    selection: list[tuple[str, str]],
    reader: Callable[[], str] = lambda: input(PROMPT),
    writer: Callable[[str], None] = typer.echo,
) -> dict[str, int]:
    """Show each paragraph, read y/n/s/q, append a human row per y/n. Returns the counts."""
    width = shutil.get_terminal_size((80, 24)).columns
    answered = skipped = 0
    total = len(selection)
    for i, (doc_id, pile) in enumerate(selection, 1):
        writer(f"\n--- {i}/{total} ---\n{textwrap.fill(texts[doc_id], width=width)}\n")
        while True:
            try:
                key = reader().strip().lower()
            except (EOFError, KeyboardInterrupt):
                key = "q"
            if key in ("y", "n", "s", "q"):
                break
            writer("Please type y, n, s or q.")
        if key == "q":
            return {"answered": answered, "skipped": skipped, "remaining": total - i + 1}
        if key == "s":
            skipped += 1
            continue
        now = datetime.now(UTC).isoformat()
        append_rows(path, [GoldRow(doc_id, int(key == "y"), pile, HUMAN, "", "", now)])
        answered += 1
    return {"answered": answered, "skipped": skipped, "remaining": 0}


def label(criterion: str, n: int, seed: int = DEFAULT_SEED) -> str:
    """Select, load paragraph text, run the session; returns the summary line."""
    from elusion.config import settings
    from elusion.review.corpus import load_texts

    path = gold_path(criterion, settings.data_dir)
    selection = select_for_audit(path, n, seed)
    if not selection:
        return "nothing to label: no unlabeled judge rows (run `elusion judge` first?)"
    for pile in ("flagged", "discard"):
        if sum(1 for _, p in selection if p == pile) < n // 2:
            typer.echo(f"note: fewer than {n // 2} unlabeled '{pile}' paragraphs; taking all")
    texts = load_texts(settings.corpus_dir)
    s = run_session(path, texts, selection)
    return f"answered {s['answered']}, skipped {s['skipped']}, remaining {s['remaining']}"
