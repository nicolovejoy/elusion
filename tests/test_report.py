from datetime import timedelta

from elusion.evaluate.metrics import Estimate
from elusion.evaluate.report import RunReport, render, years_label


def make_report(**kw) -> RunReport:
    base = dict(
        criterion="cloud-concentration",
        review_model="claude-haiku-5-5",
        prompt_version="cloud-concentration/v2",
        judge_model="claude-opus-5-5",
        judge_prompt_version="cloud-concentration/judge-v1",
        date="2026-11-04",
        seed=20261104,
        discard_n=200,
        threshold=2,
        years=(2018, 2019),
        filed=1004312,
        population=12906,
        n_flagged=117,
        n_discard=12789,
        precision=Estimate(48 / 117, 0.3214, 0.5081, 48, 117),
        elusion=Estimate(0.01, 0.0012, 0.0357, 2, 200),
        richness=Estimate(0.0136, 0.0045, 0.0400, -1, 12906),
        recall=Estimate(0.83, 0.41, 0.97, -1, 12906),
        agreement=Estimate(0.9, 0.683, 0.988, 18, 20),
        review_cost_usd=0.834,
        judge_cost_criterion_usd=1.23,
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
    assert "$0.8340" in md and "$1.2300" in md and "$2.0640" not in md
    assert "- total for this population (review + grade): $0.8340" in md
    assert md.index("judge run:") < md.index("total for this population")
    assert "summed over 2 review_jobs rows (each from its start to its finish): 0:45:12" in md
    assert "judge run: $1.2300 (whole criterion, every review pair: judge-runs ledger)" in md
    assert "(this population: grade-runs ledger)" in md
    assert "- Acme Corp: 5\n- Beta Inc: 2" in md


def test_render_without_human_labels_jobs_or_companies() -> None:
    md = render(make_report(agreement=None, review_jobs=0, review_wall_clock=None, companies=[]))
    assert "No human audit labels" in md
    assert "no review_jobs rows" in md
    assert "No flagged paragraph was labeled relevant" in md


def test_render_marks_an_unfinished_review() -> None:
    md = render(make_report(review_unfinished=True))
    assert "or to now for one still running" in md


def test_render_states_judge_effort() -> None:
    from elusion.review.providers import EFFORT

    assert f"- judge effort: {EFFORT}" in render(make_report())
    assert EFFORT == "low"


def test_render_states_derived_interval_coverage() -> None:
    md = " ".join(render(make_report()).split())
    assert "at least about 90% (0.95 × 0.95), not 95%" in md
    assert "monotone" in md and "independent" in md


def test_render_discard_sample_line() -> None:
    md = render(make_report())
    assert (
        "- discard sample: 200 drawn with seed 20261104 from the pair's discard pile; "
        "200 labeled by the judge" in md
    )


GRADED = dict(
    rationale_sound=Estimate(0.5, 0.0676, 0.9324, 2, 4),
    quote_verbatim=Estimate(0.75, 0.1941, 0.9937, 3, 4),
    sound_by_label={(1, 1): 2, (1, 0): 0, (0, 1): 1, (0, 0): 1},
    grader_model="claude-opus-5-5",
    grade_cost_usd=0.25,
)


def test_render_rationale_quality_section() -> None:
    md = render(make_report(**GRADED))
    assert "## Rationale quality" in md
    assert "- rationale sound: 0.500 [0.068, 0.932] (k/n = 2/4)" in md
    assert "- quote verbatim: 0.750 [0.194, 0.994] (k/n = 3/4)" in md
    assert "- sound and relevant: 2" in md
    assert "- sound and not relevant: 0" in md
    assert "- unsound and relevant: 1" in md
    assert "- unsound and not relevant: 1" in md
    assert "- grader model: claude-opus-5-5" in md
    assert "No grades yet" not in md


def test_render_without_grades_says_so() -> None:
    md = render(make_report())
    assert "## Rationale quality" in md
    assert "No grades yet; run `elusion grade` to add them." in md
    assert "rationale sound" not in md


def test_render_cost_includes_the_grade_run() -> None:
    md = render(make_report(**GRADED))
    assert "- grade run: $0.2500" in md
    assert f"- total for this population (review + grade): ${0.834 + 0.25:.4f}" in md


def test_render_lists_the_jobs_that_scored_the_population_per_environment() -> None:
    md = render(make_report(review_jobs=6, review_envs={"aks-elusion": 1, "local": 5}))
    line = "- review jobs that scored this population: 6 (5 local, 1 on aks-elusion)"
    assert line in md
    assert md.index("review run:") < md.index(line) < md.index("judge run:")


def test_render_one_environment() -> None:
    md = render(make_report(review_envs={"local": 2}))
    assert "- review jobs that scored this population: 2 (2 local)\n" in md


def test_render_without_jobs_has_no_jobs_line() -> None:
    md = render(make_report(review_jobs=0, review_wall_clock=None))
    assert "review jobs that scored" not in md


def test_years_label_lists_every_year_in_order() -> None:
    assert years_label([2019, 2018]) == "2018-2019"
    assert years_label([2020]) == "2020"
    assert years_label((2018, 2020)) == "2018-2020"  # every year is listed, so this is two
    assert years_label([2020, 2018, 2019, 2019]) == "2018-2019-2020"


def test_render_names_the_filing_years_and_what_the_population_is_a_part_of() -> None:
    md = render(make_report())
    assert "- filing years: 2018-2019\n" in md
    assert (
        "- population N: 12906 of 1004312 paragraphs filed in 2018-2019, "
        "in ingestion order, not a random draw\n" in md
    )
    one = render(make_report(years=(2020,), filed=599703, population=20000))
    assert "- filing years: 2020\n" in one
    assert "- population N: 20000 of 599703 paragraphs filed in 2020, in ingestion order" in one


def test_render_review_cost_is_the_populations_own_scores() -> None:
    md = " ".join(render(make_report()).split())
    assert "- review run: $0.8340 (the cost of this population's scores, summed)" in md
    assert "A billed call that left no score" in md and "is not in this sum" in md


# A population whose flagged pile (9000) is larger than the sample of 400 drawn from it.
SAMPLED = dict(
    flagged_n=400,
    n_flagged=9000,
    n_discard=3906,
    precision=Estimate(0.8, 0.7574, 0.8381, 320, 400),
)


def test_flagged_sample_line_and_by_score_lines_render() -> None:
    by_score = {3: Estimate(0.25, 0.0866, 0.4910, 5, 20), 4: Estimate(0.9, 0.683, 0.988, 18, 20)}
    md = render(make_report(threshold=3, **SAMPLED, precision_by_score=by_score))
    lines = md.splitlines()
    sample = (
        "- flagged sample: 400 drawn with seed 20261104 from the pair's flagged pile; "
        "400 labeled by the judge"
    )
    assert sample in lines
    assert lines[lines.index(sample) + 1].startswith("- discard sample: ")
    note = lines.index(
        "Precision by score, from the same flagged sample; a description, not a separate"
    )
    assert lines[note + 1] == "estimate of the population."
    assert lines[note + 3 : note + 5] == [
        "- score 3: 0.250 [0.087, 0.491] (k/n = 5/20)",
        "- score 4: 0.900 [0.683, 0.988] (k/n = 18/20)",
    ]
    assert note > lines.index("## Metrics") and note < lines.index(
        "## Judge agreement with the owner"
    )


def test_render_is_unchanged_without_a_flagged_limit() -> None:
    md = render(make_report())
    assert "flagged sample" not in md and "score 3" not in md and "by score" not in md


def test_a_flagged_pile_no_larger_than_the_limit_is_reported_as_taken_whole() -> None:
    md = render(make_report(flagged_n=400))  # the fixture's flagged pile holds 117
    assert (
        "- flagged sample: the whole flagged pile, 117, which is no larger than the 400 "
        "asked for; 117 labeled by the judge" in md
    )
    assert "400 drawn" not in md


def test_with_a_flagged_sample_the_prose_says_sample_not_pile() -> None:
    md = render(make_report(**SAMPLED, **GRADED))
    assert "read each paragraph of the flagged sample with the reviewer's" in md
    assert "against the judge's label, over the flagged sample:" in md
    assert "Companies of the paragraphs of the flagged sample the judge labeled relevant" in md
    for whole_pile in ("read each flagged paragraph", "over the flagged paragraphs:"):
        assert whole_pile not in md
    plain = render(make_report(**GRADED))
    assert "read each flagged paragraph with the reviewer's" in plain
    assert "over the flagged paragraphs:" in plain
    assert "Companies of the flagged paragraphs the judge labeled relevant" in plain
