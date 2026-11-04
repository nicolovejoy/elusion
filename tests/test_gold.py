import csv
import random
import uuid

import pytest
import sqlalchemy as sa

from elusion.evaluate.gold import (
    COLUMNS,
    DEFAULT_SEED,
    GoldRow,
    append_judge_run,
    append_rows,
    gold_path,
    human_rows,
    judge_cost_usd,
    judge_rows,
    judge_runs_path,
    labeled_ids,
    read_rows,
    select_gold,
    split_piles,
)
from tests.conftest import requires_service

# A seeded in-memory score set: 60 flagged (score 2-4), 940 discard (score 0-1).
_rng = random.Random(7)
SCORES = {f"d{i:05d}": (_rng.randint(2, 4) if i < 60 else _rng.randint(0, 1)) for i in range(1000)}


def test_split_piles_counts_and_no_overlap():
    gold = split_piles(SCORES, threshold=2, discard_n=200, seed=1)
    flagged = [d for d, p in gold if p == "flagged"]
    discard = [d for d, p in gold if p == "discard"]
    assert len(flagged) == 60 and len(discard) == 200
    assert len({d for d, _ in gold}) == 260  # no doc in both piles, none twice
    assert all(SCORES[d] >= 2 for d in flagged)
    assert all(SCORES[d] < 2 for d in discard)


def test_split_piles_threshold_moves_the_boundary():
    gold = split_piles(SCORES, threshold=3, discard_n=10, seed=1)
    flagged = {d for d, p in gold if p == "flagged"}
    assert flagged == {d for d, s in SCORES.items() if s >= 3}
    assert all(SCORES[d] < 3 for d, p in gold if p == "discard")


def test_split_piles_is_deterministic_and_order_independent():
    a = split_piles(SCORES, 2, 200, seed=1)
    b = split_piles(dict(reversed(list(SCORES.items()))), 2, 200, seed=1)
    assert a == b  # same seed, same ids, whatever order the database returned them in
    c = split_piles(SCORES, 2, 200, seed=2)
    assert {d for d, p in a if p == "discard"} != {d for d, p in c if p == "discard"}


def test_split_piles_takes_whole_discard_pile_when_small():
    scores = {"a": 3, "b": 0, "c": 1}
    assert sorted(split_piles(scores, 2, 200, seed=DEFAULT_SEED)) == [
        ("a", "flagged"),
        ("b", "discard"),
        ("c", "discard"),
    ]


def row(doc_id, labeler="claude-opus-5-5", label=1, pile="flagged"):
    return GoldRow(
        doc_id=doc_id,
        label=label,
        pile=pile,
        labeler=labeler,
        prompt_version="cloud-concentration/judge-v1" if labeler != "human" else "",
        rationale='because, "quoted"',
        labeled_at="2026-11-04T12:00:00+00:00",
    )


def test_gold_path(tmp_path):
    assert (
        gold_path("cloud-concentration", tmp_path) == tmp_path / "gold" / "cloud-concentration.csv"
    )


def test_append_creates_file_with_header(tmp_path):
    path = tmp_path / "gold" / "c.csv"
    assert append_rows(path, [row("a"), row("b", label=0, pile="discard")]) == 2
    with path.open(newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == COLUMNS
    assert lines[1] == [
        "a",
        "1",
        "flagged",
        "claude-opus-5-5",
        "cloud-concentration/judge-v1",
        'because, "quoted"',
        "2026-11-04T12:00:00+00:00",
    ]
    assert len(lines) == 3


def test_append_dedupes_on_doc_id_and_labeler(tmp_path):
    path = tmp_path / "gold" / "c.csv"
    append_rows(path, [row("a")])
    # Same (doc_id, labeler) again, inside one call and across calls: written once.
    assert append_rows(path, [row("a"), row("b"), row("b")]) == 1
    # Same doc, different labeler: a separate row.
    assert append_rows(path, [row("a", labeler="human")]) == 1
    with path.open(newline="") as f:
        assert len(list(csv.reader(f))) == 1 + 3
    assert append_rows(path, []) == 0


def test_read_helpers_split_judge_and_human(tmp_path):
    path = tmp_path / "c.csv"
    assert judge_rows(path) == [] and human_rows(path) == []  # no file yet
    append_rows(path, [row("a"), row("b", label=0, pile="discard"), row("a", labeler="human")])
    judged = judge_rows(path)
    assert [r.doc_id for r in judged] == ["a", "b"]
    assert judged[1] == row("b", label=0, pile="discard")  # round-trips, label back to int
    assert [r.doc_id for r in human_rows(path)] == ["a"]
    assert human_rows(path)[0].prompt_version == ""
    assert labeled_ids(path, "claude-opus-5-5") == {"a", "b"}
    assert labeled_ids(path, "human") == {"a"}


@requires_service("localhost", 5433, "postgres")
def test_select_gold_reads_scores_for_the_pair():
    from elusion.config import settings
    from elusion.review.db import ensure_schema, insert_scores, make_engine, scores

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    version = f"test/{uuid.uuid4().hex[:8]}"
    pre = uuid.uuid4().hex[:8]

    def score(doc_id, s, model="m"):
        return {"doc_id": doc_id, "model": model, "prompt_version": version, "score": s,
                "rationale": "", "quote": "", "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost_usd": 0, "provider": "fake"}  # fmt: skip

    rows = [score(f"{pre}{i}", i % 5) for i in range(20)] + [score(f"{pre}x", 4, model="other")]
    insert_scores(engine, rows)
    try:
        gold = select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5)
        assert len([d for d, p in gold if p == "flagged"]) == 12
        assert len([d for d, p in gold if p == "discard"]) == 3
        assert f"{pre}x" not in {d for d, _ in gold}  # other model's scores ignored
        assert gold == select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.prompt_version == version))


def test_label_is_zero_or_one(tmp_path):
    with pytest.raises(ValueError):
        append_rows(tmp_path / "c.csv", [row("a", label=2)])


def test_judge_runs_ledger_appends_and_sums(tmp_path):
    path = judge_runs_path(tmp_path / "gold" / "c.csv")
    assert path == tmp_path / "gold" / "c.judge-runs.jsonl"
    assert judge_cost_usd(path) == 0.0  # no file yet
    rec = {"labeled": 5, "failed": 1, "model": "m", "prompt_version": "c/judge-v1",
           "finished_at": "2026-11-04T12:00:00+00:00"}  # fmt: skip
    append_judge_run(path, {**rec, "cost_usd": 1.25})
    append_judge_run(path, {**rec, "cost_usd": 0.5})
    assert len(path.read_text().splitlines()) == 2
    assert judge_cost_usd(path) == pytest.approx(1.75)


def test_append_after_partial_last_line_starts_a_new_line(tmp_path):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a")])
    # A writer killed mid-way through the last field: the row parses but has no newline.
    path.write_bytes(path.read_bytes().rstrip(b"\r\n")[:-3])
    append_rows(path, [row("b")])
    assert [r.doc_id for r in read_rows(path)] == ["a", "b"]


@pytest.mark.parametrize(
    "line",
    [
        "a,1,flagged\r\n",  # short row
        "a,1,flagged,m,v,r,t,extra\r\n",  # too many fields
        "a,yes,flagged,m,v,r,t\r\n",  # label not 0/1
    ],
)
def test_malformed_row_raises_naming_the_line(tmp_path, line):
    path = tmp_path / "c.csv"
    append_rows(path, [row("a")])
    with path.open("a", newline="") as f:
        f.write(line)
    with pytest.raises(ValueError, match=r"c\.csv:3: malformed gold row"):
        read_rows(path)
