from datetime import timedelta

from elusion.evaluate.metrics import Estimate
from elusion.evaluate.report import RunReport, render


def make_report(**kw) -> RunReport:
    base = dict(
        criterion="cloud-concentration",
        review_model="claude-haiku-5-5",
        prompt_version="cloud-concentration/v2",
        judge_model="claude-opus-5-5",
        judge_prompt_version="cloud-concentration/judge-v1",
        date="2026-11-04",
        seed=20261104,
        threshold=2,
        population=12906,
        n_flagged=117,
        n_discard=12789,
        precision=Estimate(48 / 117, 0.3214, 0.5081, 48, 117),
        elusion=Estimate(0.01, 0.0012, 0.0357, 2, 200),
        richness=Estimate(0.0136, 0.0045, 0.0400, -1, 12906),
        recall=Estimate(0.83, 0.41, 0.97, -1, 12906),
        agreement=Estimate(0.9, 0.683, 0.988, 18, 20),
        review_cost_usd=0.834,
        judge_cost_usd=1.23,
        review_jobs=2,
        review_wall_clock=timedelta(minutes=45, seconds=12, microseconds=7),
        review_unfinished=False,
        companies=[("Acme Corp", 5), ("Beta Inc", 2)],
    )
    return RunReport(**{**base, **kw})


def test_render_has_every_section() -> None:
    md = render(make_report())
    for heading in (
        "# Review report: cloud-concentration",
        "## Population",
        "## Metrics",
        "## Judge agreement with the owner",
        "## Cost",
        "## Wall-clock",
        "## Companies found",
    ):
        assert heading in md, heading
    for value in (
        "claude-haiku-5-5",
        "cloud-concentration/v2",
        "claude-opus-5-5",
        "cloud-concentration/judge-v1",
        "2026-11-04",
        "20261104",
        "12906",
        "117",
        "12789",
        "threshold: 2",
    ):
        assert value in md, value


def test_render_metric_lines_exact() -> None:
    md = render(make_report())
    assert "- precision: 0.410 [0.321, 0.508] (k/n = 48/117)" in md
    assert "- elusion: 0.010 [0.001, 0.036] (k/n = 2/200)" in md
    assert "- richness: 0.014 [0.004, 0.040], derived from the two pile intervals" in md
    assert "- recall: 0.830 [0.410, 0.970], derived from the two pile intervals" in md
    assert "- agreement: 0.900 [0.683, 0.988] (k/n = 18/20)" in md
    assert "-1/" not in md


def test_render_says_population_not_corpus() -> None:
    md = render(make_report())
    assert "population" in md
    assert "corpus" not in md.lower()


def test_render_cost_wall_clock_and_companies() -> None:
    md = render(make_report())
    assert "$0.8340" in md and "$1.2300" in md and "$2.0640" in md
    assert "first start to last finish across 2 review_jobs rows: 0:45:12" in md
    assert "- Acme Corp: 5\n- Beta Inc: 2" in md


def test_render_without_human_labels_jobs_or_companies() -> None:
    md = render(make_report(agreement=None, review_jobs=0, review_wall_clock=None, companies=[]))
    assert "No human audit labels" in md
    assert "no review_jobs rows" in md
    assert "No flagged paragraph was labeled relevant" in md


def test_render_marks_an_unfinished_review() -> None:
    md = render(make_report(review_unfinished=True))
    assert "to now" in md


def test_render_states_judge_effort() -> None:
    from elusion.review.providers import EFFORT

    assert f"- judge effort: {EFFORT}" in render(make_report())
    assert EFFORT == "low"


def test_render_states_derived_interval_coverage() -> None:
    md = " ".join(render(make_report()).split())
    assert "at least about 90% (0.95 × 0.95), not 95%" in md
    assert "monotone" in md and "independent" in md
