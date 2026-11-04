"""The judge flow with a fake judge, a fake gold id set and a temp CSV: no Postgres, no API.

Prefect needs an API to record flow runs; `prefect_test_harness` starts a throwaway local
one (SQLite-backed) for this module, so these tests run without the compose stack.
"""

import json

import pytest
from prefect.testing.utilities import prefect_test_harness

from elusion.evaluate.gold import human_rows, judge_cost_usd, judge_rows, judge_runs_path
from elusion.review.prompt import Prompt
from elusion.review.providers import Judgement, ReviewError
from tests.fakes import FakeJudge


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


def make_texts(n: int) -> dict[str, str]:
    return {f"d{i:03d}": f"paragraph {i} about hosting" for i in range(n)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Patch the text and prompt loaders; return the CSV path the flow should write."""
    import elusion.flows.evaluate as mod

    texts = make_texts(40)
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(texts))
    monkeypatch.setattr(
        mod, "load_prompt", lambda c, v: Prompt(f"judge system for {c}", f"{c}/{v}")
    )
    return tmp_path / "gold" / "c.csv", texts


def gold_of(n: int, flagged: int = 2) -> list[tuple[str, str]]:
    return [(f"d{i:03d}", "flagged" if i < flagged else "discard") for i in range(n)]


def test_labels_every_gold_id_and_writes_rows(env) -> None:
    from elusion.flows.evaluate import judge

    path, texts = env
    j = FakeJudge(relevant=lambda t: t.startswith("paragraph 0 "))
    s = judge("c", gold=gold_of(5), provider=j, gold_csv=path)
    assert (s.labeled, s.skipped, s.failed) == (5, 0, 0)
    assert s.cost_projected_usd is None  # fewer than 20 to label: nothing to project

    rows = judge_rows(path)
    assert [r.doc_id for r in rows] == [f"d{i:03d}" for i in range(5)]
    assert [r.label for r in rows] == [1, 0, 0, 0, 0]
    assert [r.pile for r in rows] == ["flagged", "flagged", "discard", "discard", "discard"]
    assert {r.labeler for r in rows} == {"fake-judge"}
    assert {r.prompt_version for r in rows} == {"c/judge-v1"}
    assert all(r.rationale == "canned" and r.labeled_at for r in rows)
    assert human_rows(path) == []

    # The judge saw the judge prompt and the paragraph text, nothing else.
    assert sorted(j.calls) == sorted(("judge system for c", texts[d]) for d, _ in gold_of(5))


def test_rerun_skips_labeled_ids(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    judge("c", gold=gold_of(3), provider=FakeJudge(), gold_csv=path)
    j = FakeJudge()
    s = judge("c", gold=gold_of(5), provider=j, gold_csv=path)
    assert (s.labeled, s.skipped) == (2, 3)
    assert len(j.calls) == 2
    assert len(judge_rows(path)) == 5
    # A different judge model is a different labeler: nothing is skipped for it.
    s = judge("c", gold=gold_of(5), provider=FakeJudge(model="other"), gold_csv=path)
    assert (s.labeled, s.skipped) == (5, 0)


class Flaky(FakeJudge):
    """ReviewError (billed $0.40 at Opus input price, no label) for the listed texts."""

    def __init__(self, bad: set[str]) -> None:
        super().__init__(model="claude-opus-5-5")
        self.bad = bad

    def label(self, system: str, paragraph: str) -> Judgement:
        if paragraph in self.bad:
            self.calls.append((system, paragraph))
            raise ReviewError("refusal", self.model, 100_000, 0)
        return super().label(system, paragraph)


def test_failed_calls_write_no_row_and_are_retried(env) -> None:
    from elusion.flows.evaluate import judge

    path, texts = env
    s = judge("c", gold=gold_of(5), provider=Flaky({texts["d002"]}), gold_csv=path)
    assert (s.labeled, s.failed) == (4, 1)
    assert s.cost_usd == pytest.approx(0.40)
    assert "d002" not in {r.doc_id for r in judge_rows(path)}

    j = Flaky(set())
    s = judge("c", gold=gold_of(5), provider=j, gold_csv=path)
    assert (s.labeled, s.skipped, s.failed) == (1, 4, 0)
    assert [p for _, p in j.calls] == [texts["d002"]]


class EchoMismatch(FakeJudge):
    def label(self, system: str, paragraph: str) -> Judgement:
        j = super().label(system, paragraph)
        j.model = "fake-judge-20990101"
        return j


def test_model_echo_mismatch_aborts_and_writes_nothing(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    with pytest.raises(RuntimeError, match="fake-judge-20990101"):
        judge("c", gold=gold_of(5), provider=EchoMismatch(), gold_csv=path)
    assert judge_rows(path) == []


class Costly(FakeJudge):
    def label(self, system: str, paragraph: str) -> Judgement:
        j = super().label(system, paragraph)
        j.cost_usd = 0.01
        return j


def test_budget_exceeded_stops_after_probe(env) -> None:
    from elusion.flows.evaluate import judge
    from elusion.flows.review import BudgetExceeded

    path, _ = env
    j = Costly()
    # mean $0.01 x 10 still to label = $0.10 projected > $0.05
    with pytest.raises(BudgetExceeded, match="refusing to continue"):
        judge("c", gold=gold_of(30), provider=j, gold_csv=path, max_run_usd=0.05)
    assert len(j.calls) == 20
    assert len(judge_rows(path)) == 20  # the paid probes are kept, so a re-run skips them


def test_budget_ok_reports_projection(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    s = judge("c", gold=gold_of(30), provider=Costly(), gold_csv=path, max_run_usd=1.0)
    assert s.labeled == 30
    assert s.cost_usd == pytest.approx(0.30)
    assert s.cost_projected_usd == pytest.approx(0.01 * 10)


class AlwaysRefuses(FakeJudge):
    def __init__(self) -> None:
        super().__init__(model="claude-opus-5-5")

    def label(self, system: str, paragraph: str) -> Judgement:
        self.calls.append((system, paragraph))
        raise ReviewError("max_tokens", self.model, 100_000, 0)


def test_no_usable_label_in_probe_fails(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    j = AlwaysRefuses()
    with pytest.raises(RuntimeError, match="no usable label in 20 attempts"):
        judge("c", gold=gold_of(30), provider=j, gold_csv=path, max_run_usd=100.0)
    assert len(j.calls) == 20 and judge_rows(path) == []


class BrokenOnOne(FakeJudge):
    def label(self, system: str, paragraph: str) -> Judgement:
        if paragraph.startswith("paragraph 3 "):
            raise ValueError("bad request for this paragraph")
        return super().label(system, paragraph)


def test_unexpected_error_keeps_paid_rows_and_fails(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    with pytest.raises(ValueError, match="bad request"):
        judge("c", gold=gold_of(6), provider=BrokenOnOne(), gold_csv=path)
    assert {r.doc_id for r in judge_rows(path)} == {f"d{i:03d}" for i in range(6)} - {"d003"}


def test_unknown_id_fails_before_any_call(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    j = FakeJudge()
    with pytest.raises(KeyError, match="not in the Parquet corpus"):
        judge("c", gold=[("nope", "flagged")], provider=j, gold_csv=path)
    assert j.calls == []


def runs(path) -> list[dict]:
    p = judge_runs_path(path)
    return [json.loads(line) for line in p.read_text().splitlines()] if p.is_file() else []


def test_each_run_that_calls_the_judge_is_recorded(env) -> None:
    from elusion.flows.evaluate import judge

    path, texts = env
    judge("c", gold=gold_of(5), provider=Flaky({texts["d002"]}), gold_csv=path)
    judge("c", gold=gold_of(5), provider=Flaky(set()), gold_csv=path)
    judge("c", gold=gold_of(5), provider=Flaky(set()), gold_csv=path)  # nothing to do
    recs = runs(path)
    assert len(recs) == 2  # the third run made no call and spent nothing
    assert [(r["labeled"], r["failed"]) for r in recs] == [(4, 1), (1, 0)]
    assert recs[0]["cost_usd"] == pytest.approx(0.40)
    assert {r["model"] for r in recs} == {"claude-opus-5-5"}
    assert {r["prompt_version"] for r in recs} == {"c/judge-v1"}
    assert all(r["finished_at"] for r in recs)
    assert judge_cost_usd(judge_runs_path(path)) == pytest.approx(0.40)


def test_run_stopped_by_the_budget_is_still_recorded(env) -> None:
    from elusion.flows.evaluate import judge
    from elusion.flows.review import BudgetExceeded

    path, _ = env
    with pytest.raises(BudgetExceeded):
        judge("c", gold=gold_of(30), provider=Costly(), gold_csv=path, max_run_usd=0.05)
    (rec,) = runs(path)
    assert rec["labeled"] == 20 and rec["cost_usd"] == pytest.approx(0.20)
