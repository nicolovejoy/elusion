"""The judge flow with a fake judge, a fake gold id set and a temp CSV: no Postgres, no API.

Prefect needs an API to record flow runs; `prefect_test_harness` starts a throwaway local
one (SQLite-backed) for this module, so these tests run without the compose stack.
"""

import json

import pytest
from prefect.testing.utilities import prefect_test_harness

from elusion.evaluate.gold import (
    HUMAN,
    GoldRow,
    append_rows,
    human_rows,
    judge_cost_usd,
    judge_rows,
    judge_runs_path,
    labeled_ids,
)
from elusion.review.prompt import Prompt
from elusion.review.providers import Judgement, ReviewError
from tests.fakes import FakeJudge


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


YEARS = [2018, 2019]  # the population every judge() call below names


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
    s = judge("c", years=YEARS, gold=gold_of(5), provider=j, gold_csv=path)
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
    judge("c", years=YEARS, gold=gold_of(3), provider=FakeJudge(), gold_csv=path)
    j = FakeJudge()
    s = judge("c", years=YEARS, gold=gold_of(5), provider=j, gold_csv=path)
    assert (s.labeled, s.skipped) == (2, 3)
    assert len(j.calls) == 2
    assert len(judge_rows(path)) == 5
    # A different judge model is a different labeler: nothing is skipped for it.
    s = judge("c", years=YEARS, gold=gold_of(5), provider=FakeJudge(model="other"), gold_csv=path)
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
    s = judge("c", years=YEARS, gold=gold_of(5), provider=Flaky({texts["d002"]}), gold_csv=path)
    assert (s.labeled, s.failed) == (4, 1)
    assert s.cost_usd == pytest.approx(0.40)
    assert "d002" not in {r.doc_id for r in judge_rows(path)}

    j = Flaky(set())
    s = judge("c", years=YEARS, gold=gold_of(5), provider=j, gold_csv=path)
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
        judge("c", years=YEARS, gold=gold_of(5), provider=EchoMismatch(), gold_csv=path)
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
        judge("c", years=YEARS, gold=gold_of(30), provider=j, gold_csv=path, max_run_usd=0.05)
    assert len(j.calls) == 20
    assert len(judge_rows(path)) == 20  # the paid probes are kept, so a re-run skips them


def test_budget_ok_reports_projection(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    s = judge("c", years=YEARS, gold=gold_of(30), provider=Costly(), gold_csv=path, max_run_usd=1.0)
    assert s.labeled == 30
    assert s.cost_usd == pytest.approx(0.30)
    # The whole run: 20 probes already billed + 10 labels still to go.
    assert s.cost_projected_usd == pytest.approx(0.01 * 20 + 0.01 * 10)


def test_probe_spend_counts_toward_the_limit(env) -> None:
    from elusion.flows.evaluate import judge
    from elusion.flows.review import BudgetExceeded

    path, _ = env
    # The 10 labels left cost $0.10 < $0.25, but with the $0.20 of probes the run is $0.30.
    with pytest.raises(BudgetExceeded, match=r"\$0\.30"):
        judge(
            "c", years=YEARS, gold=gold_of(30), provider=Costly(), gold_csv=path, max_run_usd=0.25
        )


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
        judge("c", years=YEARS, gold=gold_of(30), provider=j, gold_csv=path, max_run_usd=100.0)
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
        judge("c", years=YEARS, gold=gold_of(6), provider=BrokenOnOne(), gold_csv=path)
    assert {r.doc_id for r in judge_rows(path)} == {f"d{i:03d}" for i in range(6)} - {"d003"}


def test_unknown_id_fails_before_any_call(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    j = FakeJudge()
    with pytest.raises(KeyError, match="not in the Parquet corpus"):
        judge("c", years=YEARS, gold=[("nope", "flagged")], provider=j, gold_csv=path)
    assert j.calls == []


def runs(path) -> list[dict]:
    p = judge_runs_path(path)
    return [json.loads(line) for line in p.read_text().splitlines()] if p.is_file() else []


def test_each_run_that_calls_the_judge_is_recorded(env) -> None:
    from elusion.flows.evaluate import judge

    path, texts = env
    judge("c", years=YEARS, gold=gold_of(5), provider=Flaky({texts["d002"]}), gold_csv=path)
    judge("c", years=YEARS, gold=gold_of(5), provider=Flaky(set()), gold_csv=path)
    judge("c", years=YEARS, gold=gold_of(5), provider=Flaky(set()), gold_csv=path)  # nothing to do
    recs = runs(path)
    assert len(recs) == 2  # the third run made no call and spent nothing
    assert [(r["labeled"], r["failed"]) for r in recs] == [(4, 1), (1, 0)]
    assert recs[0]["cost_usd"] == pytest.approx(0.40)
    assert {r["model"] for r in recs} == {"claude-opus-5-5"}
    assert {r["prompt_version"] for r in recs} == {"c/judge-v1"}
    # the review pair the run was for, so a per-pair judge cost is possible for new runs
    assert {(r["review_model"], r["review_prompt_version"]) for r in recs} == {
        ("claude-haiku-5-5", "c/v2")
    }
    assert all(r["finished_at"] for r in recs)
    assert judge_cost_usd(judge_runs_path(path)) == pytest.approx(0.40)


def test_run_stopped_by_the_budget_is_still_recorded(env) -> None:
    from elusion.flows.evaluate import judge
    from elusion.flows.review import BudgetExceeded

    path, _ = env
    with pytest.raises(BudgetExceeded):
        judge(
            "c", years=YEARS, gold=gold_of(30), provider=Costly(), gold_csv=path, max_run_usd=0.05
        )
    (rec,) = runs(path)
    assert rec["labeled"] == 20 and rec["cost_usd"] == pytest.approx(0.20)


@pytest.fixture
def running_review(monkeypatch):
    """judge() without `gold`: Postgres readers stubbed, one review job still unfinished."""
    import elusion.flows.evaluate as mod

    monkeypatch.setattr(mod.db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(mod, "select_gold", lambda *a, **kw: gold_of(5))
    monkeypatch.setattr(
        mod,
        "jobs_summary",
        lambda engine, model, version, years: {
            "jobs": 2,
            "unfinished": True,
            "unfinished_ids": ["job-abc"],
        },
    )


def test_refuses_while_a_review_job_is_unfinished(env, running_review) -> None:
    from elusion.flows.evaluate import ReviewStillRunning, judge

    path, _ = env
    j = FakeJudge()
    with pytest.raises(ReviewStillRunning, match=r"job-abc.*--force"):
        judge("c", years=YEARS, provider=j, gold_csv=path)
    assert j.calls == [] and judge_rows(path) == []


def test_force_labels_despite_an_unfinished_review_job(env, running_review) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    s = judge("c", years=YEARS, provider=FakeJudge(), gold_csv=path, force=True)
    assert s.labeled == 5


def test_review_model_and_years_select_the_population(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod
    from elusion.flows.evaluate import judge

    seen = {}
    monkeypatch.setattr(mod.db, "make_engine", lambda url: "engine")

    def select(engine, model, version, threshold, discard_n, seed, years, **kw):
        seen["select"] = (model, years)
        return gold_of(5)

    def jobs(engine, model, version, years):
        seen["jobs"] = (model, years)
        return {"jobs": 1, "unfinished": False, "unfinished_ids": []}

    monkeypatch.setattr(mod, "select_gold", select)
    monkeypatch.setattr(mod, "jobs_summary", jobs)
    path, _ = env
    judge("c", years=[2020], provider=FakeJudge(), gold_csv=path, review_model="other-model")
    assert seen == {"jobs": ("other-model", [2020]), "select": ("other-model", [2020])}


def test_years_is_required(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    with pytest.raises(Exception, match="years"):
        judge("c", gold=gold_of(2), provider=FakeJudge(), gold_csv=path)


def test_ledger_records_the_filing_years(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    judge("c", years=[2020, 2019], gold=gold_of(3), provider=FakeJudge(), gold_csv=path)
    assert runs(path)[-1]["years"] == [2019, 2020]


def test_judge_reads_the_texts_of_the_criterions_corpus(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod
    from elusion.config import settings
    from elusion.flows.evaluate import judge

    path, texts = env
    seen = []
    monkeypatch.setattr(mod, "load_texts", lambda text_dir: seen.append(text_dir) or dict(texts))
    judge("c", years=YEARS, gold=gold_of(2), provider=FakeJudge(), gold_csv=path)
    judge(
        "auditable-decisions",
        years=YEARS,
        gold=gold_of(2),
        provider=FakeJudge(),
        gold_csv=path.with_name("a.csv"),
    )
    assert seen == [settings.data_dir / "corpus", settings.data_dir / "abstracts"]


def human(doc_id: str, pile: str) -> GoldRow:
    return GoldRow(doc_id, 1, pile, HUMAN, "", "", "2026-11-20T12:00:00+00:00")


def test_only_human_labels_just_the_ids_the_owner_labelled(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    gold = gold_of(6, flagged=3)
    append_rows(path, [human("d000", "flagged"), human("d003", "discard")])
    j = FakeJudge()
    s = judge("c", years=YEARS, gold=gold, provider=j, gold_csv=path, only_human=True)
    assert s.labeled == 2
    assert labeled_ids(path, j.model) == {"d000", "d003"}


def test_only_human_with_no_human_labels_is_an_error(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    j = FakeJudge()
    with pytest.raises(RuntimeError, match="no human labels"):
        judge("c", years=YEARS, gold=gold_of(6), provider=j, gold_csv=path, only_human=True)
    assert j.calls == []


def test_only_human_ignores_human_labels_outside_the_draw(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    append_rows(path, [human("d030", "flagged")])  # labelled once, but not in this draw
    with pytest.raises(RuntimeError, match="no human labels"):
        judge(
            "c", years=YEARS, gold=gold_of(6), provider=FakeJudge(), gold_csv=path, only_human=True
        )


def test_full_run_after_only_human_does_not_pay_twice(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    gold = gold_of(6, flagged=3)
    append_rows(path, [human("d000", "flagged"), human("d003", "discard")])
    judge("c", years=YEARS, gold=gold, provider=FakeJudge(), gold_csv=path, only_human=True)
    j = FakeJudge()
    second = judge("c", years=YEARS, gold=gold, provider=j, gold_csv=path)
    assert second.skipped == 2 and second.labeled == 4
    assert len(j.calls) == 4


def test_draw_gold_refuses_an_unfinished_review(running_review) -> None:
    from elusion.flows.evaluate import ReviewStillRunning, draw_gold

    with pytest.raises(ReviewStillRunning, match=r"job-abc.*--force"):
        draw_gold("c", "v2", years=YEARS)
    assert draw_gold("c", "v2", years=YEARS, force=True) == gold_of(5)


def test_draw_gold_passes_the_flagged_limit(monkeypatch) -> None:
    import elusion.evaluate.gold as gold_mod
    import elusion.flows.evaluate as mod
    from elusion.flows.evaluate import draw_gold

    scores = {f"f{i}": 4 for i in range(5)} | {f"d{i}": 0 for i in range(5)}
    monkeypatch.setattr(gold_mod, "scores_for", lambda engine, model, version, years: scores)
    monkeypatch.setattr(
        mod, "jobs_summary", lambda *a: {"jobs": 1, "unfinished": False, "unfinished_ids": []}
    )
    got = draw_gold("c", "v2", years=YEARS, discard_n=2, flagged_n=1, engine="engine")
    assert [p for _, p in got] == ["flagged", "discard", "discard"]


def test_draw_gold_with_no_scores_is_an_error(monkeypatch) -> None:
    import elusion.evaluate.gold as gold_mod
    import elusion.flows.evaluate as mod
    from elusion.flows.evaluate import draw_gold

    monkeypatch.setattr(gold_mod, "scores_for", lambda engine, model, version, years: {})
    monkeypatch.setattr(
        mod, "jobs_summary", lambda *a: {"jobs": 0, "unfinished": False, "unfinished_ids": []}
    )
    with pytest.raises(RuntimeError, match="no scores"):
        draw_gold("c", "v2", years=YEARS, engine="engine")


def test_seed_rows_carry_the_seed_pile(env) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    gold = [("d000", "flagged"), ("d001", "discard"), ("d002", "seed")]
    judge("c", years=YEARS, gold=gold, provider=FakeJudge(), gold_csv=path)
    assert {r.doc_id: r.pile for r in judge_rows(path)} == dict(gold)


@pytest.fixture
def scored(monkeypatch):
    """judge() without `gold`: 4 flagged and 4 discard scores, the review finished, and
    two citation seeds: W1 flagged (d001, d003 its two documents), W2 discarded (d005)."""
    import elusion.evaluate.gold as gold_mod
    import elusion.flows.evaluate as mod

    scores = {f"d{i:03d}": (4 if i < 4 else 0) for i in range(8)}
    monkeypatch.setattr(mod.db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(gold_mod, "scores_for", lambda engine, model, version, years: scores)
    monkeypatch.setattr(mod, "scores_for", lambda engine, model, version, years: scores)
    monkeypatch.setattr(
        mod, "jobs_summary", lambda *a: {"jobs": 1, "unfinished": False, "unfinished_ids": []}
    )
    monkeypatch.setattr(mod, "seed_works", lambda engine: {"W1": object(), "W2": object()})
    monkeypatch.setattr(
        mod,
        "doc_ids_by_work",
        lambda d: {"W1": {"d001", "d003"}, "W2": {"d005"}, "W9": {"d002"}},
    )
    return scores


def test_seeds_adds_the_flagged_seeds_the_draw_left_out(env, scored) -> None:
    from elusion.evaluate.gold import split_piles
    from elusion.flows.evaluate import judge

    path, _ = env
    kw = dict(years=YEARS, discard_n=2, flagged_n=1, seed=7, gold_csv=path)
    drawn = {d for d, p in split_piles(scored, 3, 2, 7, flagged_n=1) if p == "flagged"}
    s = judge("auditable-decisions", "v1", provider=FakeJudge(), seeds=True, **kw)
    piles = {r.doc_id: r.pile for r in judge_rows(path)}
    assert {d for d, p in piles.items() if p == "flagged"} == drawn
    assert {d for d, p in piles.items() if p == "seed"} == {"d001", "d003"} - drawn
    assert "d005" not in piles  # a discarded seed is not judged; d002 is not a seed
    assert s.labeled == len(piles)
    # Without --seeds the same draw has no seed rows to add; nothing is paid twice.
    again = judge("auditable-decisions", "v1", provider=FakeJudge(), **kw)
    assert again.labeled == 0


def test_seeds_is_refused_for_a_criterion_without_citation_seeds(env, scored) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    j = FakeJudge()
    with pytest.raises(RuntimeError, match="no citation seeds"):
        judge("cloud-concentration", years=YEARS, provider=j, gold_csv=path, seeds=True)
    assert j.calls == []


def test_only_human_warns_about_human_labels_outside_the_draw(env, capsys) -> None:
    """Sizes or a seed that differ from `label`'s would otherwise shrink the 40 silently."""
    from elusion.flows.evaluate import judge

    path, _ = env
    append_rows(path, [human("d000", "flagged"), human("d030", "flagged"), human("d031", "x")])
    s = judge(
        "c", years=YEARS, gold=gold_of(6), provider=FakeJudge(), gold_csv=path, only_human=True
    )
    assert s.labeled == 1
    err = capsys.readouterr().err
    assert "WARNING: 2 of the 3 human labels" in err and "--flagged-n" in err


def test_only_human_is_quiet_when_every_human_label_is_in_the_draw(env, capsys) -> None:
    from elusion.flows.evaluate import judge

    path, _ = env
    append_rows(path, [human("d000", "flagged")])
    judge("c", years=YEARS, gold=gold_of(6), provider=FakeJudge(), gold_csv=path, only_human=True)
    assert "WARNING" not in capsys.readouterr().err


def test_a_new_judge_prompt_relabels_what_the_old_one_labeled(env, monkeypatch):
    """Rows are keyed by judge prompt too: judge-v2 labels ids judge-v1 already did."""
    import elusion.flows.evaluate as mod
    from elusion.evaluate.gold import read_rows

    csv, _ = env
    gold = [("d000", "flagged"), ("d001", "discard")]
    kw = dict(years=[2019], gold=gold, gold_csv=csv)
    first = mod.judge("c", provider=FakeJudge(), **kw)
    monkeypatch.setattr(mod, "latest_judge_version", lambda c: "judge-v2")
    second = mod.judge("c", provider=FakeJudge(), **kw)
    again = mod.judge("c", provider=FakeJudge(), **kw)
    assert (first.labeled, second.labeled, again.labeled, again.skipped) == (2, 2, 0, 2)
    assert (
        sorted(r.prompt_version for r in read_rows(csv)) == ["c/judge-v1"] * 2 + ["c/judge-v2"] * 2
    )
