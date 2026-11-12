"""The grades store: a stronger model's verdict on whether a reviewer's rationale is sound.

A reviewer model scores a paragraph and explains itself (rationale plus a verbatim quote).
*Grading* asks a stronger model, the grader, to read the paragraph and that one answer and
say whether the rationale is a faithful reading under the criterion. It is a separate
question from "is the score right": a reviewer can land on the right score for a made-up
reason. The grader never sees a gold label, and this file never feeds back into any prompt.

One CSV per criterion under data/grades/ (outside git), appended to as grades arrive. A
(doc_id, review_model, prompt_version, grader) combination appears at most once, which is
what makes re-running the grade flow free. `quote_verbatim` is computed here, not asked of
the model: whether the reviewer's quote really occurs in the paragraph.

Next to the CSV, data/grades/<criterion>.grade-runs.jsonl keeps one JSON line per grade run
(grades written, failures, dollars spent), the same ledger shape as the gold judge runs.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class GradeRow:
    doc_id: str
    review_model: str  # the reviewer whose answer was graded
    prompt_version: str  # the reviewer's prompt version
    score: int  # the reviewer's 0-4 score
    grader: str  # the grader's pinned model id
    grader_prompt_version: str
    sound: int  # 1 = faithful reading, 0 = not
    quote_verbatim: int  # 1 = the reviewer's quote occurs in the paragraph, 0 = not
    rationale: str  # the grader's one sentence
    graded_at: str  # ISO 8601, UTC


COLUMNS = [f.name for f in fields(GradeRow)]
_INT_COLUMNS = ("score", "sound", "quote_verbatim")


def grades_path(criterion: str, data_dir: Path) -> Path:
    return data_dir / "grades" / f"{criterion}.csv"


def grade_runs_path(grades_csv: Path) -> Path:
    """data/grades/<criterion>.csv -> data/grades/<criterion>.grade-runs.jsonl"""
    return grades_csv.with_name(f"{grades_csv.stem}.grade-runs.jsonl")


def read_rows(path: Path) -> list[GradeRow]:
    """Every row in the CSV, in file order; [] if the file does not exist yet.

    Raises ValueError naming the line for a row with missing or extra fields, a bad
    header, or a non-integer score/sound/quote_verbatim.
    """
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is not None and reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: header {reader.fieldnames} is not {COLUMNS}")
        rows = []
        for r in reader:
            try:
                if None in r or None in r.values():
                    raise ValueError("wrong field count")
                row = GradeRow(**{**r, **{c: int(r[c]) for c in _INT_COLUMNS}})
                if row.sound not in (0, 1) or row.quote_verbatim not in (0, 1):
                    raise ValueError("sound and quote_verbatim must be 0 or 1")
            except ValueError as exc:
                raise ValueError(f"{path}:{reader.line_num}: malformed grade row {r!r}") from exc
            rows.append(row)
        return rows


def rows_for(path: Path, review_model: str, prompt_version: str, grader: str) -> list[GradeRow]:
    return [
        r
        for r in read_rows(path)
        if (r.review_model, r.prompt_version, r.grader) == (review_model, prompt_version, grader)
    ]


def graded_ids(path: Path, review_model: str, prompt_version: str, grader: str) -> set[str]:
    return {r.doc_id for r in rows_for(path, review_model, prompt_version, grader)}


def append_rows(path: Path, rows: Iterable[GradeRow]) -> int:
    """Append rows whose (doc_id, review_model, prompt_version, grader) is not in the file.

    Returns rows written. Creates the directory and the header line on first use. Not safe
    for two writers at once; the grade flow writes from a single thread.
    """
    seen = {(r.doc_id, r.review_model, r.prompt_version, r.grader) for r in read_rows(path)}
    new: list[GradeRow] = []
    for r in rows:
        for name in ("sound", "quote_verbatim"):
            v = getattr(r, name)
            if v not in (0, 1) or isinstance(v, bool):
                raise ValueError(f"{name} must be 0 or 1, got {v!r} for {r.doc_id}")
        key = (r.doc_id, r.review_model, r.prompt_version, r.grader)
        if key not in seen:
            seen.add(key)
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


def append_grade_run(path: Path, record: dict) -> None:
    """Append one grade run's record as a JSON line (creating the directory if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def grade_cost_usd(
    path: Path, review_model: str | None = None, review_prompt_version: str | None = None
) -> float:
    """Total `cost_usd` over the grade runs recorded in `path`; 0.0 if there is none.

    With `review_model` and/or `review_prompt_version`, only runs whose ledger record
    carries those exact values count, so a pair sees its own grading spend.
    """
    if not path.is_file():
        return 0.0
    want = {"review_model": review_model, "review_prompt_version": review_prompt_version}
    with path.open(encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    return sum(
        r["cost_usd"] for r in records if all(v is None or r.get(k) == v for k, v in want.items())
    )


def quote_is_verbatim(quote: str, paragraph: str) -> bool:
    """True when the reviewer's quote occurs exactly in the paragraph; an empty quote is not."""
    return quote != "" and quote in paragraph
