"""The gold set: paragraphs with a trusted yes/no label, kept apart from everything else.

A *gold set* is the answer key used to measure the review: for a sample of paragraphs we
record whether each truly meets the criterion, then compare that with what the review
flagged. Here a judge model writes most labels and the owner audits a few by hand, so
every row says who labeled it. It is a plain CSV per criterion under data/gold/ (outside
git, and never fed into any prompt), appended to as labels arrive; a (doc_id, labeler,
prompt version) triple appears at most once, which is what makes re-running the judge
free. The prompt version is in the key so that a rewritten judge prompt (judge-v2) can
label abstracts the first one already labeled, and both sets of labels are kept.

Which paragraphs get labeled is decided by `select_gold`: every paragraph the review
flagged (score >= threshold), or a seeded random sample of them when a limit is given,
plus a uniform random sample of the discard pile drawn with a seeded random number
generator, so the same seed always picks the same sample.

Next to the CSV, data/gold/<criterion>.judge-runs.jsonl keeps one JSON line per judge
run (labels written, failures, dollars spent), because the evaluate step runs later in
another process and reports what labeling the gold set cost.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import Engine

from elusion.review.db import scores_for

# "seed": a flagged document found on the citation route (phase 7) that the flagged draw
# left out. It is judged for harvest recall and never counts in precision.
Pile = Literal["flagged", "discard", "seed"]

HUMAN = "human"  # the `labeler` value for the owner's own labels
# The `prompt_version` of a human row that replaces the owner's first, blind label of the
# same document. The blind row is kept; the revision is appended after it with the reason.
REVISED = "revised"
DEFAULT_SEED = 20261104  # fixed default for the discard sample; written into the report


@dataclass(frozen=True)
class GoldRow:
    doc_id: str
    label: int  # 1 = meets the criterion, 0 = does not
    pile: str  # "flagged" | "discard" | "seed": the pile when labeled (informational)
    labeler: str  # the judge's pinned model id, or HUMAN
    prompt_version: str  # the judge prompt version; empty for HUMAN rows
    rationale: str
    labeled_at: str  # ISO 8601, UTC


COLUMNS = [f.name for f in fields(GoldRow)]


def gold_path(criterion: str, data_dir: Path) -> Path:
    return data_dir / "gold" / f"{criterion}.csv"


def judge_runs_path(gold_csv: Path) -> Path:
    """data/gold/<criterion>.csv -> data/gold/<criterion>.judge-runs.jsonl"""
    return gold_csv.with_name(f"{gold_csv.stem}.judge-runs.jsonl")


def read_rows(path: Path) -> list[GoldRow]:
    """Every row in the CSV, in file order; [] if the file does not exist yet.

    Raises ValueError naming the line for a row with missing or extra fields, a bad
    header, or a label that is not 0/1 (e.g. a hand edit gone wrong).
    """
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is not None and reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: header {reader.fieldnames} is not {COLUMNS}")
        rows = []
        for r in reader:
            if None in r or None in r.values() or r["label"] not in ("0", "1"):
                raise ValueError(f"{path}:{reader.line_num}: malformed gold row {r!r}")
            rows.append(GoldRow(**{**r, "label": int(r["label"])}))
        return rows


def judge_rows(path: Path) -> list[GoldRow]:
    return [r for r in read_rows(path) if r.labeler != HUMAN]


def human_rows(path: Path, blind: bool = False) -> list[GoldRow]:
    """The owner's label of each document he labeled, one row per document.

    A label he changed after reading the judge's disagreements is a second row marked
    REVISED; it replaces the first unless `blind`, which returns the labels as first
    given, before he had seen anything the judge said.
    """
    rows: dict[str, GoldRow] = {}
    for r in read_rows(path):
        if r.labeler == HUMAN and not (blind and r.prompt_version == REVISED):
            rows[r.doc_id] = r
    return list(rows.values())


def judge_rows_for(path: Path, model: str, prompt_version: str) -> list[GoldRow]:
    """The rows one judge wrote: a pinned model under one judge prompt version."""
    return [r for r in read_rows(path) if r.labeler == model and r.prompt_version == prompt_version]


def labeled_ids(path: Path, labeler: str, prompt_version: str | None = None) -> set[str]:
    """Ids labeled by `labeler`; with `prompt_version`, only under that judge prompt."""
    return {
        r.doc_id
        for r in read_rows(path)
        if r.labeler == labeler and prompt_version in (None, r.prompt_version)
    }


def append_rows(path: Path, rows: Iterable[GoldRow]) -> int:
    """Append rows whose (doc_id, labeler, prompt_version) is not in the file yet.

    Returns the number of rows written.

    Creates the directory and the header line on first use. Not safe for two writers at
    once; the judge flow and the labeling CLI each write from a single thread.
    """
    seen = {(r.doc_id, r.labeler, r.prompt_version) for r in read_rows(path)}
    new: list[GoldRow] = []
    for r in rows:
        if r.label not in (0, 1) or isinstance(r.label, bool):
            raise ValueError(f"label must be 0 or 1, got {r.label!r} for {r.doc_id}")
        if (r.doc_id, r.labeler, r.prompt_version) not in seen:
            seen.add((r.doc_id, r.labeler, r.prompt_version))
            new.append(r)
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file() or path.stat().st_size == 0
    torn = not fresh and not path.read_bytes().endswith(b"\n")  # e.g. a killed writer
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(COLUMNS)
        elif torn:
            f.write("\r\n")  # never glue a new row onto a partial last line
        w.writerows(astuple(r) for r in new)
    return len(new)


def append_judge_run(path: Path, record: dict) -> None:
    """Append one judge run's record as a JSON line (creating the directory if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def refusals_path(gold_csv: Path) -> Path:
    """The refusals ledger next to the gold CSV: <criterion>.judge-refusals.jsonl."""
    return gold_csv.with_name(f"{gold_csv.stem}.judge-refusals.jsonl")


def append_refusals(path: Path, doc_ids: Iterable[str], model: str, prompt_version: str) -> None:
    """Record that `model`, under `prompt_version`, refused to answer about each id.

    A *refusal* is the model declining to answer at all (the API's stop_reason
    "refusal"), usually because of the document's subject. It is not a label, so it has
    no row in the gold CSV; this file is the record that the id was asked about.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for doc_id in doc_ids:
        append_judge_run(
            path,
            {"doc_id": doc_id, "model": model, "prompt_version": prompt_version, "at": now},
        )


def refused_ids(path: Path, model: str, prompt_version: str) -> set[str]:
    """The ids `model` has refused under `prompt_version`, whether or not labeled since."""
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    return {
        r["doc_id"]
        for r in records
        if r["model"] == model and r["prompt_version"] == prompt_version
    }


def judge_cost_usd(path: Path) -> float:
    """Total `cost_usd` over every judge run recorded in `path`; 0.0 if there is none."""
    if not path.is_file():
        return 0.0
    with path.open(encoding="utf-8") as f:
        return sum(json.loads(line)["cost_usd"] for line in f if line.strip())


def split_piles(
    scores: Mapping[str, int],
    threshold: int,
    discard_n: int,
    seed: int,
    flagged_n: int | None = None,
) -> list[tuple[str, Pile]]:
    """Every doc with score >= threshold, then `discard_n` of the rest drawn at random.

    Ids are sorted before sampling so the result depends only on the scores and the seed,
    not on the order the database returned rows in. A discard pile smaller than
    `discard_n` is taken whole.

    With `flagged_n`, the flagged pile is sampled too (taken whole if it is smaller) and
    the drawn ids are returned sorted. Each pile has its own generator started from the
    same seed, so a limit on one pile never changes which ids are drawn from the other.
    """
    flagged = sorted(d for d, s in scores.items() if s >= threshold)
    if flagged_n is not None:
        flagged = sorted(random.Random(seed).sample(flagged, min(flagged_n, len(flagged))))
    discard = sorted(d for d, s in scores.items() if s < threshold)
    sample = random.Random(seed).sample(discard, min(discard_n, len(discard)))
    return [(d, "flagged") for d in flagged] + [(d, "discard") for d in sample]


def with_seeds(
    gold: list[tuple[str, Pile]],
    scores: Mapping[str, int],
    threshold: int,
    seed_doc_ids: Collection[str],
) -> list[tuple[str, Pile]]:
    """`gold`, then (doc_id, "seed") for each flagged seed document the draw left out.

    A *seed* here is a paper found by following citations from an anchor paper, not by
    a search term; whether the searches also returned it is how harvest recall is
    estimated, and only for seeds the judge calls relevant, so each flagged seed needs a
    label even when the random sample of the flagged pile did not draw it. A seed
    already in `gold` keeps its pile, a discarded seed is not judged, and an id with no
    score is ignored. Added ids are sorted.
    """
    have = {d for d, _ in gold}
    extra = sorted(d for d in seed_doc_ids if scores.get(d, -1) >= threshold and d not in have)
    return gold + [(d, "seed") for d in extra]


def select_gold(
    engine: Engine,
    model: str,
    prompt_version: str,
    threshold: int = 3,
    discard_n: int = 200,
    seed: int = DEFAULT_SEED,
    years: Sequence[int] | None = None,
    flagged_n: int | None = None,
) -> list[tuple[str, Pile]]:
    """The gold ids for one review run, i.e. the (review model, prompt version) pair.

    With `years`, the piles hold only paragraphs filed in those years. The discard draw
    depends on the whole pile it is drawn from, so scores of another filing year would
    otherwise change the sample and the judge would be paid to label a new one.
    """
    scores = scores_for(engine, model, prompt_version, years)
    return split_piles(scores, threshold, discard_n, seed, flagged_n)
