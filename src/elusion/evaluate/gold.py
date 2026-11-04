"""The gold set: paragraphs with a trusted yes/no label, kept apart from everything else.

A *gold set* is the answer key used to measure the review: for a sample of paragraphs we
record whether each truly meets the criterion, then compare that with what the review
flagged. Here a judge model writes most labels and the owner audits a few by hand, so
every row says who labeled it. It is a plain CSV per criterion under data/gold/ (outside
git, and never fed into any prompt), appended to as labels arrive; a (doc_id, labeler)
pair appears at most once, which is what makes re-running the judge free.

Which paragraphs get labeled is decided by `select_gold`: every paragraph the review
flagged (score >= threshold), plus a uniform random sample of the discard pile drawn
with a seeded random number generator, so the same seed always picks the same sample.

Next to the CSV, data/gold/<criterion>.judge-runs.jsonl keeps one JSON line per judge
run (labels written, failures, dollars spent), because the evaluate step runs later in
another process and reports what labeling the gold set cost.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import Engine

from elusion.review.db import scores_for

Pile = Literal["flagged", "discard"]

HUMAN = "human"  # the `labeler` value for the owner's own labels
DEFAULT_SEED = 20261104  # fixed default for the discard sample; written into the report


@dataclass(frozen=True)
class GoldRow:
    doc_id: str
    label: int  # 1 = meets the criterion, 0 = does not
    pile: str  # "flagged" | "discard": the pile when labeled (informational)
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


def human_rows(path: Path) -> list[GoldRow]:
    return [r for r in read_rows(path) if r.labeler == HUMAN]


def labeled_ids(path: Path, labeler: str) -> set[str]:
    return {r.doc_id for r in read_rows(path) if r.labeler == labeler}


def append_rows(path: Path, rows: Iterable[GoldRow]) -> int:
    """Append rows whose (doc_id, labeler) is not in the file yet. Returns rows written.

    Creates the directory and the header line on first use. Not safe for two writers at
    once; the judge flow and the labeling CLI each write from a single thread.
    """
    seen = {(r.doc_id, r.labeler) for r in read_rows(path)}
    new: list[GoldRow] = []
    for r in rows:
        if r.label not in (0, 1) or isinstance(r.label, bool):
            raise ValueError(f"label must be 0 or 1, got {r.label!r} for {r.doc_id}")
        if (r.doc_id, r.labeler) not in seen:
            seen.add((r.doc_id, r.labeler))
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


def judge_cost_usd(path: Path) -> float:
    """Total `cost_usd` over every judge run recorded in `path`; 0.0 if there is none."""
    if not path.is_file():
        return 0.0
    with path.open(encoding="utf-8") as f:
        return sum(json.loads(line)["cost_usd"] for line in f if line.strip())


def split_piles(
    scores: Mapping[str, int], threshold: int, discard_n: int, seed: int
) -> list[tuple[str, Pile]]:
    """Every doc with score >= threshold, then `discard_n` of the rest drawn at random.

    Ids are sorted before sampling so the result depends only on the scores and the seed,
    not on the order the database returned rows in. A discard pile smaller than
    `discard_n` is taken whole.
    """
    flagged = sorted(d for d, s in scores.items() if s >= threshold)
    discard = sorted(d for d, s in scores.items() if s < threshold)
    sample = random.Random(seed).sample(discard, min(discard_n, len(discard)))
    return [(d, "flagged") for d in flagged] + [(d, "discard") for d in sample]


def select_gold(
    engine: Engine,
    model: str,
    prompt_version: str,
    threshold: int = 2,
    discard_n: int = 200,
    seed: int = DEFAULT_SEED,
) -> list[tuple[str, Pile]]:
    """The gold ids for one review run, i.e. the (review model, prompt version) pair."""
    return split_piles(scores_for(engine, model, prompt_version), threshold, discard_n, seed)
