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
    with_seeds,
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


# 50 flagged and 100 discard, for the flagged-limit tests.
SMALL = {f"f{i:03}": 4 for i in range(50)} | {f"d{i:03}": 1 for i in range(100)}


def test_flagged_limit_samples_the_flagged_pile():
    got = split_piles(SMALL, 3, 10, seed=7, flagged_n=20)
    flagged = [d for d, p in got if p == "flagged"]
    assert len(flagged) == 20 and len(set(flagged)) == 20
    assert all(d.startswith("f") for d in flagged)
    assert got == split_piles(dict(reversed(SMALL.items())), 3, 10, seed=7, flagged_n=20)
    assert flagged != sorted(SMALL)[:20]  # a draw, not the first twenty


def test_flagged_limit_does_not_move_the_discard_draw():
    def discards(**kw):
        return [d for d, p in split_piles(SMALL, 3, 10, seed=7, **kw) if p == "discard"]

    assert discards() == discards(flagged_n=20) == discards(flagged_n=5)


def test_no_flagged_limit_is_todays_result():
    got = split_piles(SMALL, 3, 10, seed=7)
    assert [d for d, p in got if p == "flagged"] == sorted(d for d in SMALL if d.startswith("f"))


def test_flagged_pile_smaller_than_the_limit_is_taken_whole():
    got = split_piles(SMALL, 3, 10, seed=7, flagged_n=500)
    assert sum(1 for _, p in got if p == "flagged") == 50


def test_with_seeds_adds_flagged_seeds_outside_the_draw():
    gold = [("f1", "flagged"), ("d1", "discard")]
    scores = {"f1": 4, "f2": 3, "f3": 4, "d1": 0, "d2": 1}
    got = with_seeds(gold, scores, 3, seed_doc_ids={"f1", "f3", "d2", "zz"})
    assert got == [("f1", "flagged"), ("d1", "discard"), ("f3", "seed")]


def test_with_seeds_appends_in_id_order():
    scores = {"s2": 3, "s1": 4, "f1": 4}
    got = with_seeds([("f1", "flagged")], scores, 3, seed_doc_ids={"s2", "s1"})
    assert got == [("f1", "flagged"), ("s1", "seed"), ("s2", "seed")]


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


@requires_service("localhost", 5433, "postgres")
def test_select_gold_passes_the_flagged_limit():
    from elusion.config import settings
    from elusion.review.db import ensure_schema, insert_scores, make_engine, scores

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    version = f"test/{uuid.uuid4().hex[:8]}"
    pre = uuid.uuid4().hex[:8]
    rows = [
        {
            "doc_id": f"{pre}{i}",
            "model": "m",
            "prompt_version": version,
            "score": i % 5,
            "rationale": "",
            "quote": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_usd": 0,
            "provider": "fake",
        }  # fmt: skip
        for i in range(20)
    ]
    insert_scores(engine, rows)
    try:
        gold = select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5, flagged_n=1)
        assert len([d for d, p in gold if p == "flagged"]) == 1
        assert len([d for d, p in gold if p == "discard"]) == 3
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


@requires_service("localhost", 5433, "postgres")
def test_select_gold_draws_only_from_the_given_filing_years():
    """Scores of another year must not move the seeded discard draw (they would cost a re-judge)."""
    from elusion.collect import db as collect_db
    from elusion.config import settings
    from elusion.review.db import ensure_schema, insert_scores, make_engine, scores

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    collect_db.ensure_schema(engine)
    version = f"test/{uuid.uuid4().hex[:8]}"
    pre = uuid.uuid4().hex[:8]

    def score(doc_id, s):
        return {"doc_id": doc_id, "model": "m", "prompt_version": version, "score": s,
                "rationale": "", "quote": "", "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost_usd": 0, "provider": "fake"}  # fmt: skip

    def filed(doc_ids, year):
        return [
            {"doc_id": d, "cik": "0", "year": year, "paragraph_index": 0, "word_count": 1}
            for d in doc_ids
        ]

    old = [f"{pre}a{i:02d}" for i in range(20)]
    new = [f"{pre}b{i:02d}" for i in range(20)]
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(collect_db.documents), filed(old, 2019) + filed(new, 2020))
        insert_scores(engine, [score(d, i % 5) for i, d in enumerate(old)])
        before = select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5, years=[2019])
        insert_scores(engine, [score(d, i % 5) for i, d in enumerate(new)])
        after = select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5, years=[2019])
        assert after == before
        assert {d for d, _ in after} <= set(old)
        only_new = select_gold(engine, "m", version, threshold=2, discard_n=3, seed=5, years=[2020])
        assert {d for d, _ in only_new} <= set(new)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.prompt_version == version))
            conn.execute(
                sa.delete(collect_db.documents).where(collect_db.documents.c.doc_id.in_(old + new))
            )
