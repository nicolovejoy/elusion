"""The build_index flow with a fake embedder, a throwaway database and temp Parquet."""

import pytest
import sqlalchemy as sa
from prefect.testing.utilities import prefect_test_harness

from elusion.ask import index
from elusion.evaluate.gold import GoldRow, append_refusals, append_rows, refusals_path
from elusion.flows.review import BudgetExceeded
from elusion.review import db as review_db
from elusion.review.pricing import PRICES
from tests.fakes import FakeEmbedder
from tests.test_board_batch import write_year

CRITERION = "auditable-decisions"
MODEL = "gpt-6-luna"
KEY = f"{CRITERION}/v1"


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


def score(doc_id: str, value: int, model: str = MODEL, version: str = KEY) -> dict:
    return {
        "doc_id": doc_id,
        "model": model,
        "prompt_version": version,
        "score": value,
        "rationale": "r",
        "quote": "q",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost_usd": 0.0,
        "provider": "fake",
    }


def seed(engine, tmp_path, scores: dict[str, int], with_text: bool = True) -> None:
    review_db.insert_scores(engine, [score(d, s) for d, s in scores.items()])
    if with_text:
        write_year(
            tmp_path / "abstracts",
            2024,
            [{"doc_id": d, "text": f"Abstract of {d} about review."} for d in scores],
        )


def gold(tmp_path, *rows: tuple[str, int]):
    csv = tmp_path / "gold" / f"{CRITERION}.csv"
    append_rows(
        csv,
        [
            GoldRow(d, label, "flagged", "human", "", "r", "2026-11-01T00:00:00Z")
            for d, label in rows
        ],
    )
    return csv


def run(engine, tmp_path, embedder=None, **kw):
    from elusion.flows.index import build_index

    embedder = embedder or FakeEmbedder()
    kw.setdefault("review_model", MODEL)
    return build_index(
        CRITERION,
        embedder,
        engine=engine,
        abstracts_dir=tmp_path / "abstracts",
        gold_csv=tmp_path / "gold" / f"{CRITERION}.csv",
        **kw,
    )


def indexed(engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(sa.select(index.abstract_index.c.doc_id).order_by("doc_id")).scalars()
        )


def test_only_scores_at_or_above_the_threshold_are_indexed(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 3, "c": 2, "d": 0})
    summary = run(index_engine, tmp_path)
    assert indexed(index_engine) == ["a", "b"]
    assert summary == {
        "screened_in": 2,
        "held_back_removed": 0,
        "no_text": 0,
        "rows_added": 2,
        "already_embedded": 0,
        "embedded": 2,
        "input_tokens": summary["input_tokens"],
        "cost_usd": 0.0,
    }
    assert summary["input_tokens"] > 0
    assert index.indexed_pair(index_engine) == (MODEL, KEY, 3)
    assert index.count_vectors(index_engine, "fake-embedder") == 2


def test_a_gold_label_does_not_change_the_set(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 4, "c": 4})
    run(index_engine, tmp_path)  # no gold file yet
    before = indexed(index_engine)
    outcomes = []
    for label in (0, 1):
        # The same flagged id, labelled 0 and then 1: held back either way.
        csv = gold(tmp_path, ("b", label))
        run(index_engine, tmp_path)
        outcomes.append(indexed(index_engine))
        csv.unlink()
        run(index_engine, tmp_path)  # without the label it is indexed again
        assert indexed(index_engine) == before
    assert outcomes[0] == outcomes[1] == ["a", "c"]


def test_held_back_ids_are_not_indexed_and_are_removed_if_already_there(
    index_engine, tmp_path
) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 4, "c": 4, "d": 4})
    gold(tmp_path, ("a", 1))
    summary = run(index_engine, tmp_path)
    assert indexed(index_engine) == ["b", "c", "d"]
    assert summary["held_back_removed"] == 0

    # The gold set grew after the run: a judge refusal and a new label.
    gold(tmp_path, ("b", 0))
    append_refusals(refusals_path(tmp_path / "gold" / f"{CRITERION}.csv"), ["c"], "m", "v1")
    summary = run(index_engine, tmp_path)
    assert indexed(index_engine) == ["d"]
    assert summary["held_back_removed"] == 2
    assert index.count_vectors(index_engine, "fake-embedder") == 1  # cascade


def test_a_second_run_embeds_nothing(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 3})
    run(index_engine, tmp_path)
    again = FakeEmbedder()
    summary = run(index_engine, tmp_path, again)
    assert again.calls == []
    assert summary["rows_added"] == 0
    assert summary["embedded"] == 0
    assert summary["already_embedded"] == 2
    assert summary["cost_usd"] == 0.0


def test_a_new_embedding_model_embeds_everything_and_keeps_the_old_vectors(
    index_engine, tmp_path
) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 3})
    run(index_engine, tmp_path, FakeEmbedder("first"))
    second = FakeEmbedder("second")
    summary = run(index_engine, tmp_path, second)
    assert summary["embedded"] == 2 and summary["already_embedded"] == 0
    assert len(second.calls) == 1
    assert index.count_vectors(index_engine, "first") == 2
    assert index.count_vectors(index_engine, "second") == 2


def test_spend_guard_raises_before_any_call(index_engine, tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(
        PRICES, "pricey", {"input": 1000.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}
    )
    seed(index_engine, tmp_path, {"a": 4, "b": 4})
    embedder = FakeEmbedder("pricey")
    with pytest.raises(BudgetExceeded, match="above the limit"):
        run(index_engine, tmp_path, embedder, max_usd=0.0001)
    assert embedder.calls == []
    assert index.count_vectors(index_engine, "pricey") == 0
    # The same run under a roomy limit goes through.
    run(index_engine, tmp_path, FakeEmbedder("pricey"), max_usd=100.0)
    assert index.count_vectors(index_engine, "pricey") == 2


def test_a_crash_in_batch_two_keeps_batch_one(index_engine, tmp_path, monkeypatch) -> None:
    import elusion.flows.index as flow_mod

    monkeypatch.setattr(flow_mod, "BATCH", 2)
    seed(index_engine, tmp_path, {d: 4 for d in "abcde"})
    with pytest.raises(RuntimeError, match="failed on call 2"):
        run(index_engine, tmp_path, FakeEmbedder(fail_on_call=2))
    assert index.count_vectors(index_engine, "fake-embedder") == 2

    rerun = FakeEmbedder()
    summary = run(index_engine, tmp_path, rerun)
    assert [len(c) for c in rerun.calls] == [2, 1]
    assert summary["embedded"] == 3 and summary["already_embedded"] == 2
    assert index.count_vectors(index_engine, "fake-embedder") == 5


def test_a_different_pair_is_refused(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 4})
    run(index_engine, tmp_path)
    review_db.insert_scores(index_engine, [score("a", 4, model="other-model")])
    with pytest.raises(index.IndexPairMismatch):
        run(index_engine, tmp_path, review_model="other-model")
    with pytest.raises(index.IndexPairMismatch):
        run(index_engine, tmp_path, threshold=4)
    assert indexed(index_engine) == ["a"]


def test_the_10k_criterion_is_refused(index_engine, tmp_path) -> None:
    from elusion.flows.index import build_index

    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="abstracts only"):
        build_index("cloud-concentration", embedder, review_model=MODEL, engine=index_engine)
    assert embedder.calls == []


def test_an_empty_pair_is_refused_naming_it(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 1})
    with pytest.raises(ValueError, match=r"auditable-decisions/v9"):
        run(index_engine, tmp_path, prompt_version="v9")


def test_papers_without_text_are_counted_and_skipped(index_engine, tmp_path) -> None:
    seed(index_engine, tmp_path, {"a": 4, "b": 4})
    review_db.insert_scores(index_engine, [score("ghost", 4)])  # scored, but no Parquet row
    summary = run(index_engine, tmp_path)
    assert indexed(index_engine) == ["a", "b"]
    assert summary["screened_in"] == 3
    assert summary["no_text"] == 1
