"""The owner's blind audit: label a few paragraphs by hand, then compare with the judge.

The judge model's labels are only trustworthy if a human agrees with them on a sample.
This module picks that sample (half from the flagged pile, half from the discard pile,
chosen at random with a seed) and runs a plain terminal session: it shows one paragraph
at a time, with nothing that hints at what the judge or the review said, and appends the
owner's yes/no to the gold CSV the moment it is typed. It is a human session, so it is
not a Prefect flow and needs no server.

The sample comes from one of two places. By default it is picked among the paragraphs
the judge has already labeled. Given `candidates` (the drawn gold ids, see
flows.evaluate.draw_gold) it is picked from the draw itself and the judge's rows are
never read, so the owner can label before the judge has labeled anything and the two
sets of labels are independent.
"""

from __future__ import annotations

import random
import shutil
import textwrap
from collections.abc import Callable, Collection, Sequence
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

PROMPT = "y = yes, it is   n = no, it is not   s = skip for now   q = stop here (answers saved): "
QUESTION = "Is this text about that topic?"


def criterion_summary(path: Path) -> str:
    """The criterion's one-paragraph statement of the topic: its `>` lines, joined.

    It is what the owner is asked about in the session. The lists of what counts and what
    does not are left out on purpose: they are the review prompt's and the judge's reading
    of the topic, and the owner's labels are there to check that reading, not to repeat it.
    """
    lines = [ln.lstrip("> ").strip() for ln in path.read_text().splitlines() if ln.startswith(">")]
    return " ".join(ln for ln in lines if ln)


def pick_from_draw(
    candidates: Sequence[tuple[str, str]], n: int, seed: int, exclude: Collection[str] = ()
) -> list[tuple[str, str]]:
    """The owner's pick from the drawn gold ids: n//2 of each pile, shuffled together.

    It does not depend on what has been labeled, so a second sitting gets the same pick
    back. Each pile's ids are sorted, shuffled with the seeded generator and cut at
    n//2, so a larger `n` keeps every abstract a smaller `n` picked and adds to them.
    """
    rng = random.Random(seed)
    picked: list[tuple[str, str]] = []
    for pile in ("flagged", "discard"):
        ids = sorted({d for d, p in candidates if p == pile} - set(exclude))
        rng.shuffle(ids)
        picked += [(d, pile) for d in ids[: n // 2]]
    rng.shuffle(picked)
    return picked


def select_for_audit(
    path: Path,
    n: int,
    seed: int,
    candidates: Sequence[tuple[str, str]] | None = None,
    exclude: Collection[str] = (),
) -> list[tuple[str, str]]:
    """(doc_id, pile) pairs still to label: n//2 flagged and n//2 discard, shuffled together.

    The shuffle (same seeded rng) keeps the pile from being guessable from the position
    in the session. The ids in `exclude` are never selected.

    With `candidates` (the judge's rows are then never read) the pick is `pick_from_draw`
    less what the owner has already labeled: quitting part-way and running the same
    command again shows the ones not yet answered, not a fresh pick.

    Without, it is a fresh pick each time among the judge's rows the owner has not
    labeled, sorted before the seeded draw so it depends only on the CSV and the seed. A
    pile with fewer candidates than n//2 is taken whole.
    """
    done = labeled_ids(path, HUMAN)
    if candidates is not None:
        return [(d, p) for d, p in pick_from_draw(candidates, n, seed, exclude) if d not in done]
    rng = random.Random(seed)
    judged = judge_rows(path)
    picked: list[tuple[str, str]] = []
    for pile in ("flagged", "discard"):
        ids = sorted({r.doc_id for r in judged if r.pile == pile} - done - set(exclude))
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
    topic: str = "",
) -> dict[str, int]:
    """Show each paragraph, read y/n/s/q, append a human row per y/n. Returns the counts.

    With `topic` (the criterion's summary) the question is spelled out under each
    paragraph, so the owner never answers a bare yes/no.
    """
    width = shutil.get_terminal_size((80, 24)).columns
    asked = f"{textwrap.fill('Topic: ' + topic, width=width)}\n{QUESTION}" if topic else ""
    answered = skipped = 0
    total = len(selection)
    for i, (doc_id, pile) in enumerate(selection, 1):
        writer(f"\n--- {i}/{total} ---\n{textwrap.fill(texts[doc_id], width=width)}\n")
        if asked:
            writer(asked)
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


def label(
    criterion: str,
    n: int,
    seed: int = DEFAULT_SEED,
    *,
    candidates: Sequence[tuple[str, str]] | None = None,
    exclude: Collection[str] = (),
    topic: str = "",
) -> str:
    """Select, load paragraph text, run the session; returns the summary line.

    `candidates` and `exclude` are passed to `select_for_audit`, `topic` to `run_session`.

    `n` is split evenly between the piles, so it must be at least 2; an odd n is rounded
    down (with a note) rather than silently labeling one fewer than asked.
    """
    from elusion.config import settings
    from elusion.corpora import corpus_for
    from elusion.review.corpus import load_texts

    if n < 2:
        raise typer.BadParameter(
            f"n={n}: need at least 2 (n/2 flagged + n/2 discard)", param_hint="--n"
        )
    if n % 2:
        typer.echo(f"note: rounding n={n} down to {n - 1} (half flagged, half discard)")
        n -= 1

    path = gold_path(criterion, settings.data_dir)
    selection = select_for_audit(path, n, seed, candidates, exclude)
    if candidates is not None:
        picked = len(pick_from_draw(candidates, n, seed, exclude))
        if not picked:
            return "nothing to label: every drawn abstract is labelled or excluded"
        if not selection:
            return f"nothing to label: all {picked} picked are labelled; raise --n to label more"
        if len(selection) < picked:
            typer.echo(f"note: {len(selection)} of the {picked} picked are still to label")
    elif not selection:
        return "nothing to label: no unlabeled judge rows (run `elusion judge` first?)"
    else:
        for pile in ("flagged", "discard"):
            if sum(1 for _, p in selection if p == pile) < n // 2:
                typer.echo(f"note: fewer than {n // 2} unlabeled '{pile}' paragraphs; taking all")
    texts = load_texts(corpus_for(criterion).text_dir(settings.data_dir))
    s = run_session(path, texts, selection, topic=topic)
    return f"answered {s['answered']}, skipped {s['skipped']}, remaining {s['remaining']}"
