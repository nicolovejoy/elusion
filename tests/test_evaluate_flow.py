"""The evaluate flow with a fake engine (stubbed readers), a temp gold CSV and a temp
reports directory: no Postgres, no API."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from prefect.testing.utilities import prefect_test_harness

from elusion.evaluate import metrics
from elusion.evaluate.gold import (
    DEFAULT_SEED,
    HUMAN,
    GoldRow,
    append_judge_run,
    append_rows,
    gold_path,
    split_piles,
)

JUDGE = "fake-judge"
# A real Engine object (Prefect validates flow parameter types) that is never queried:
# every reader that would touch Postgres is replaced in the `env` fixture.
FAKE_ENGINE = sa.create_engine("sqlite://")
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


def row(doc_id: str, label: int, labeler: str = JUDGE, pile: str = "flagged") -> GoldRow:
    pv = "" if labeler == HUMAN else "c/judge-v1"
    return GoldRow(doc_id, label, pile, labeler, pv, "r", "2026-11-04T00:00:00+00:00")


# f00-f03 flagged (score 3), g00-g15 discard (score 0); 20 docs in the population.
SCORES = {**{f"f{i:02d}": 3 for i in range(4)}, **{f"g{i:02d}": 0 for i in range(16)}}


def seeded_draw(scores, n):
    return {d for d, pile in split_piles(scores, 3, n, DEFAULT_SEED) if pile == "discard"}


DRAW_10 = sorted(seeded_draw(SCORES, 10))


@pytest.fixture
def env(monkeypatch, tmp_path):
    import elusion.flows.evaluate as mod

    seen: dict = {}

    def fake_scores_for(engine, model, prompt_version, years):
        seen["scores_for"] = (engine, model, prompt_version, years)
        return dict(SCORES)

    def fake_scores_cost(engine, model, prompt_version, years):
        seen["scores_cost_usd"] = (engine, model, prompt_version, years)
        return 0.60  # not the jobs' 0.75: the report's review cost is the scores' own

    def fake_count_documents(engine, year, corpus):
        seen.setdefault("count_documents", []).append((year, corpus))
        return {2018: 600, 2019: 400, 2020: 5000}[year]

    def fake_jobs_summary(engine, model, prompt_version, years):
        seen["jobs_summary"] = (engine, model, prompt_version, years)
        return {
            "jobs": 2,
            "cost_usd": 0.75,
            "started_at": T0,
            "finished_at": T0 + timedelta(hours=1, minutes=2, seconds=3),
            "unfinished": False,
            "unfinished_ids": [],
            "busy_s": 3600.0,  # less than the 1:02:03 span: a gap between the two jobs
            "run_envs": {"local": 1, "aks-x": 1},
        }

    def fake_company_counts(engine, doc_ids):
        seen["company_ids"] = sorted(doc_ids)
        return [("Acme Corp", len(seen["company_ids"]))]

    monkeypatch.setattr(mod, "scores_for", fake_scores_for)
    monkeypatch.setattr(mod, "jobs_summary", fake_jobs_summary)
    monkeypatch.setattr(mod, "scores_cost_usd", fake_scores_cost)
    monkeypatch.setattr(mod, "count_documents", fake_count_documents)
    monkeypatch.setattr(mod, "company_counts", fake_company_counts)

    csv = gold_path("c", tmp_path)
    append_rows(
        csv,
        [row("f00", 1), row("f01", 1), row("f02", 1), row("f03", 0)]
        # the judge labeled exactly the seeded draw of 10 from the 16-doc discard pile
        + [row(d, 1 if d == "g00" else 0, pile="discard") for d in DRAW_10]
        # another judge model's row: not this judge, ignored
        + [row("g11", 1, labeler="other-judge", pile="discard")]
        + [row("f00", 1, labeler=HUMAN), row("g00", 1, labeler=HUMAN)],
    )
    append_judge_run(csv.with_name("c.judge-runs.jsonl"), {"cost_usd": 0.5})
    append_judge_run(csv.with_name("c.judge-runs.jsonl"), {"cost_usd": 0.25})
    return tmp_path, seen


def run(tmp_path, **kw):
    from elusion.flows.evaluate import evaluate

    args = dict(
        prompt_version="v2",
        years=[2019, 2018],
        engine=FAKE_ENGINE,
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        review_model="rev-model",
        discard_n=10,
        judge_model=JUDGE,
        today="2026-11-04",
        log_mlflow=False,  # only the logging tests touch MLflow, and then on a file:// store
    )
    return evaluate("c", **{**args, **kw}).report_path


def test_writes_the_report_and_returns_its_path(env) -> None:
    tmp_path, seen = env
    path = run(tmp_path)
    assert path == tmp_path / "reports" / "c-rev-model-v2-2018-2019.md"
    md = path.read_text()

    # every reader is asked for the same population: the pair, filed in those years
    population = (FAKE_ENGINE, "rev-model", "c/v2", [2018, 2019])
    assert seen["scores_for"] == population
    assert seen["jobs_summary"] == population
    assert seen["scores_cost_usd"] == population
    assert seen["company_ids"] == ["f00", "f01", "f02"]  # flagged and judged relevant

    assert "- filing years: 2018-2019" in md
    assert "- population N: 20 of 1000 paragraphs filed in 2018-2019, in ingestion order" in md
    assert "- flagged (score >= 3): 4" in md
    assert "- discard (score < 3): 16" in md
    assert (
        "- discard sample: 10 drawn with seed 20261104 from the pair's discard pile; "
        "10 labeled by the judge" in md
    )
    assert "(k/n = 3/4)" in md  # precision
    assert "(k/n = 1/10)" in md  # elusion; the other judge's g11 row is not counted
    assert "- agreement: 1.000" in md and "(k/n = 2/2)" in md
    assert "- judge model: fake-judge" in md
    assert "- judge prompt version: c/judge-v1" in md
    assert "- seed: 20261104" in md and "- date: 2026-11-04" in md
    assert "- review run: $0.6000 (the cost of this population's scores, summed)" in md
    assert "- review jobs that scored this population: 2 (1 local, 1 on aks-x)" in md
    assert "- judge run: $0.7500" in md
    assert "summed over 2 review_jobs rows (each from its start to its finish): 1:00:00" in md
    assert "- Acme Corp: 3" in md
    assert "-1/" not in md and "corpus" not in md


def test_years_is_required(env) -> None:
    from elusion.flows.evaluate import evaluate

    tmp_path, _ = env
    with pytest.raises(Exception, match="years"):
        evaluate("c", engine=FAKE_ENGINE, data_dir=tmp_path, log_mlflow=False)


def test_another_year_is_another_population_and_another_report_file(env) -> None:
    tmp_path, seen = env
    path = run(tmp_path, years=[2020])
    assert path.name == "c-rev-model-v2-2020.md"
    assert seen["scores_for"][3] == [2020]
    assert "- population N: 20 of 5000 paragraphs filed in 2020, in ingestion order" in (
        path.read_text()
    )


def test_piles_come_from_scores_not_the_csv_pile_column(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    # g00 now scores 3: it moves into the flagged pile (its judge label is 1) although
    # the CSV still says "discard"; f03 drops to 2 and is now a discard-sample row.
    rescored = {**SCORES, "g00": 3, "f03": 2}
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p, y: dict(rescored))
    # the draw changes with the pile, so label every g doc (all 0) to cover whichever it picks
    append_rows(
        gold_path("c", tmp_path), [row(f"g{i:02d}", 0, pile="discard") for i in range(1, 16)]
    )
    md = run(tmp_path).read_text()
    assert "- flagged (score >= 3): 4" in md
    assert "(k/n = 4/4)" in md  # f00 f01 f02 g00
    assert "(k/n = 0/10)" in md  # seeded draw of 10 from the 15 remaining discard docs


def test_refuses_when_flagged_docs_lack_a_judge_label(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(
        mod, "scores_for", lambda e, m, p, y: {**SCORES, "x01": 4, "x02": 3, "x03": 2}
    )
    with pytest.raises(mod.MissingLabels, match=r"2 flagged .*elusion judge"):
        run(tmp_path)
    assert not (tmp_path / "reports").exists()


def test_no_scores_is_an_error(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p, y: {})
    with pytest.raises(RuntimeError, match=r"no scores for \(rev-model, c/v2\)"):
        run(tmp_path)


def test_no_human_rows_means_no_agreement(env) -> None:
    tmp_path, _ = env
    csv = gold_path("c", tmp_path)
    lines = [ln for ln in csv.read_text().splitlines(keepends=True) if f",{HUMAN}," not in ln]
    csv.write_text("".join(lines))
    md = run(tmp_path).read_text()
    assert "No human audit labels" in md


def test_warns_but_writes_the_report_while_a_review_job_is_unfinished(
    env, monkeypatch, capsys
) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(
        mod,
        "jobs_summary",
        lambda e, m, p, y: {
            "jobs": 1,
            "cost_usd": 0.1,
            "started_at": T0,
            "finished_at": None,
            "unfinished": True,
            "unfinished_ids": ["job-abc"],
            "busy_s": 60.0,
            "run_envs": {"local": 1},
        },
    )
    path = run(tmp_path)
    assert path.is_file()
    err = capsys.readouterr().err
    assert "WARNING" in err and "job-abc" in err and "still running" in err


def test_no_warning_when_every_review_job_finished(env, capsys) -> None:
    tmp_path, _ = env
    run(tmp_path)
    assert "still running" not in capsys.readouterr().err


def test_discard_sample_is_the_seeded_draw_not_every_labeled_doc(env) -> None:
    tmp_path, _ = env
    outside = sorted(d for d in SCORES if d.startswith("g") and d not in DRAW_10)[0]
    assert outside not in seeded_draw(SCORES, 10)  # precondition
    append_rows(gold_path("c", tmp_path), [row(outside, 1, pile="discard")])
    text = run(tmp_path).read_text()
    est = metrics.clopper_pearson(1, 10)  # g00 only; the extra relevant label is not drawn
    assert f"elusion: {1 / 10:.3f} [{est[0]:.3f}, {est[1]:.3f}] (k/n = 1/10)" in text
    assert "drawn with seed 20261104 from the pair's discard pile" in text


def test_missing_discard_label_refuses_and_counts(env) -> None:
    from elusion.flows.evaluate import MissingDiscardLabels

    tmp_path, _ = env
    unlabeled = seeded_draw(SCORES, 12) - set(DRAW_10)
    assert len(unlabeled) == 2  # precondition: the draw of 12 reaches two unlabeled docs
    with pytest.raises(
        MissingDiscardLabels, match="2 sampled discard paragraphs have no fake-judge label"
    ):
        run(tmp_path, discard_n=12)
    assert not (tmp_path / "reports").exists()


def test_review_model_option_selects_the_pair(env) -> None:
    tmp_path, seen = env
    run(tmp_path, review_model="other-model")
    assert seen["scores_for"][1] == "other-model"


def test_evaluate_logs_one_run_and_returns_its_id(env, monkeypatch) -> None:
    import mlflow

    import elusion.flows.evaluate as mod
    from elusion.config import Settings

    tmp_path, _ = env
    uri = f"file://{tmp_path}/mlruns"
    monkeypatch.setattr(mod, "settings", Settings(mlflow_tracking_uri=uri))
    monkeypatch.setattr(mod, "provider_for", lambda e, m, v: "anthropic")
    (tmp_path / "prompts" / "c").mkdir(parents=True)
    (tmp_path / "prompts" / "c" / "v2.md").write_text("prompt")
    (tmp_path / "criteria").mkdir()
    (tmp_path / "criteria" / "c.md").write_text("criterion")
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path / "prompts")
    monkeypatch.chdir(tmp_path)
    from elusion.flows.evaluate import evaluate

    result = evaluate(
        "c",
        prompt_version="v2",
        years=[2018, 2019],
        engine=FAKE_ENGINE,
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        review_model="rev-model",
        discard_n=10,
        judge_model=JUDGE,
        today="2026-11-04",
        log_mlflow=True,
    )
    assert result.run_id and len(result.run_id) == 32
    client = mlflow.MlflowClient(tracking_uri=uri)
    run = client.get_run(result.run_id)
    assert run.data.params["provider"] == "anthropic"
    assert run.data.params["report_path"] == str(result.report_path)
    assert run.data.params["review_jobs"] == "2"
    assert run.data.params["run_envs"] == "aks-x,local"
    assert run.data.params["years"] == "2018,2019"
    names = {a.path for a in client.list_artifacts(result.run_id)}
    assert "c-rev-model-v2-2018-2019.md" in names
    assert {"v2.md", "c.md"} <= names  # the prompt file and the criterion file
    assert not any(n.endswith(".csv") for n in names)


def test_report_is_written_even_when_logging_fails(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(mod, "log_run", boom)
    monkeypatch.setattr(mod, "provider_for", lambda e, m, v: "anthropic")
    with pytest.raises(RuntimeError, match="report written to .*c-rev-model-v2-2018-2019.md"):
        run(tmp_path, log_mlflow=True)
    assert (tmp_path / "reports" / "c-rev-model-v2-2018-2019.md").is_file()


def add_grades(tmp_path, grader=JUDGE):
    from elusion.evaluate.grades import (
        GradeRow,
        append_grade_run,
        grade_runs_path,
        grades_path,
    )
    from elusion.evaluate.grades import append_rows as append_grades

    def g(doc, sound, quote, model="rev-model", pv="c/v2", who=grader):
        return GradeRow(doc, model, pv, 3, who, "c/grade-v1", sound, quote, "r", "2026-11-05")

    path = grades_path("c", tmp_path)
    append_grades(
        path,
        [g("f00", 1, 1), g("f01", 1, 1), g("f02", 0, 1), g("f03", 0, 0)]
        # not counted: a non-flagged doc, another grader, another reviewer, another prompt
        + [g("g00", 1, 1), g("f00", 0, 0, who="other"), g("f00", 0, 0, model="x")]
        + [g("f01", 0, 0, pv="c/v3")],
    )
    ledger = grade_runs_path(path)
    pair = {"review_model": "rev-model", "review_prompt_version": "c/v2"}
    append_grade_run(ledger, {**pair, "cost_usd": 0.25})  # no `years`: read as 2018, 2019
    # the same pair's other population: never counted for 2018-2019
    append_grade_run(ledger, {**pair, "years": [2020], "cost_usd": 2.0})
    # other pairs' spend, same ledger: never counted for this pair
    append_grade_run(ledger, {**pair, "review_prompt_version": "c/v3", "cost_usd": 4.0})
    append_grade_run(ledger, {**pair, "review_model": "x", "cost_usd": 8.0})


def test_report_has_rationale_quality_when_graded(env) -> None:
    tmp_path, _ = env
    add_grades(tmp_path)
    md = run(tmp_path).read_text()
    assert "## Rationale quality" in md
    assert "- rationale sound: 0.500 [" in md and "(k/n = 2/4)" in md
    assert "- quote verbatim: 0.750 [" in md and "(k/n = 3/4)" in md
    # f00,f01: sound and relevant; f02: unsound and relevant; f03: unsound, not relevant
    assert "- sound and relevant: 2" in md
    assert "- sound and not relevant: 0" in md
    assert "- unsound and relevant: 1" in md
    assert "- unsound and not relevant: 1" in md
    assert "- grade run: $0.2500" in md
    assert "- total for this population (review + grade): $0.8500" in md  # 0.60 + 0.25


def test_report_without_grades_says_so(env) -> None:
    tmp_path, _ = env
    md = run(tmp_path).read_text()
    assert "No grades yet; run `elusion grade` to add them." in md
    assert "rationale sound" not in md


def run_logged(tmp_path) -> str:
    from elusion.flows.evaluate import evaluate

    return evaluate(
        "c",
        prompt_version="v2",
        years=[2018, 2019],
        engine=FAKE_ENGINE,
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        review_model="rev-model",
        discard_n=10,
        judge_model=JUDGE,
        today="2026-11-04",
        log_mlflow=True,
    ).run_id


def test_logged_run_has_grade_metrics_only_when_graded(env, monkeypatch) -> None:
    import mlflow

    import elusion.flows.evaluate as mod
    from elusion.config import Settings

    tmp_path, _ = env
    uri = f"file://{tmp_path}/mlruns"
    monkeypatch.setattr(mod, "settings", Settings(mlflow_tracking_uri=uri))
    monkeypatch.setattr(mod, "provider_for", lambda e, m, v: "anthropic")
    monkeypatch.chdir(tmp_path)
    client = mlflow.MlflowClient(tracking_uri=uri)

    data = client.get_run(run_logged(tmp_path)).data
    assert "rationale_sound_point" not in data.metrics and "grader_model" not in data.params

    add_grades(tmp_path)
    second = run_logged(tmp_path)
    data = client.get_run(second).data
    assert data.metrics["rationale_sound_point"] == 0.5
    assert data.metrics["quote_verbatim_k"] == 3.0
    assert data.metrics["grade_cost_usd"] == 0.25
    assert data.params["grader_model"] == JUDGE
    assert data.params["grader_prompt_version"] == "c/grade-v1"
    names = {a.path for a in client.list_artifacts(second)}
    assert not any(n.endswith(".csv") for n in names)


def test_report_date_is_the_pacific_calendar_day() -> None:
    """00:38 UTC on the 8th is still the 7th in California: the report shows the owner's day."""
    from datetime import UTC, datetime

    from elusion.flows.evaluate import report_date

    assert report_date(datetime(2026, 11, 8, 0, 38, tzinfo=UTC)) == "2026-11-07"
    assert report_date(datetime(2026, 11, 8, 9, 0, tzinfo=UTC)) == "2026-11-08"


def test_report_path_has_no_character_windows_rejects(tmp_path) -> None:
    """`:` in an Ollama tag broke a Windows checkout of the committed report."""
    from elusion.flows.evaluate import report_path

    got = report_path(tmp_path, "cloud-concentration", "qwen2.5:14b", "v2", [2019, 2018])
    assert got.name == "cloud-concentration-qwen2.5-14b-v2-2018-2019.md"
    bad = report_path(tmp_path, "c", 'a<b>c:d"e/f\\g|h?i*j', "x/y", [2020])
    assert bad.name == "c-a-b-c-d-e-f-g-h-i-j-x-y-2020.md"


def test_filed_counts_only_documents_of_the_criterions_corpus(env) -> None:
    tmp_path, seen = env
    run(tmp_path)
    assert seen["count_documents"] == [(2018, "edgar"), (2019, "edgar")]


# The flagged sample: 20 flagged (h00-h19) and 16 discard (g00-g15); a draw of 5 flagged.
BIG = {**{f"h{i:02d}": 4 for i in range(20)}, **{f"g{i:02d}": 0 for i in range(16)}}
DRAWN = sorted(d for d, p in split_piles(BIG, 3, 10, DEFAULT_SEED, flagged_n=5) if p == "flagged")
OUTSIDE = sorted(set(BIG) - set(DRAWN) - {d for d in BIG if d.startswith("g")})[0]


@pytest.fixture
def sampled(env, monkeypatch):
    """`evaluate` over BIG with its own gold CSV; returns (run, reports seen, readers seen).

    The judge labeled the five drawn flagged ids (the first three relevant), the seeded
    discard draw of 10 (none relevant) and one flagged citation seed outside the draw.
    """
    import elusion.flows.evaluate as mod

    tmp_path, seen = env
    data_dir = tmp_path / "sampled"
    scores = dict(BIG)
    reports = []
    real_render = mod.render
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p, y: dict(scores))
    monkeypatch.setattr(mod, "render", lambda r: reports.append(r) or real_render(r))
    rows = [row(d, int(i < 3)) for i, d in enumerate(DRAWN)]
    rows += [row(d, 0, pile="discard") for d in sorted(seeded_draw(BIG, 10))]
    rows += [row(OUTSIDE, 1, pile="seed")]
    append_rows(gold_path("c", data_dir), rows)

    def go(**kw):
        run(tmp_path, data_dir=data_dir, **kw)
        return reports[-1]

    go.scores, go.data_dir, go.seen = scores, data_dir, seen
    return go


def test_precision_comes_from_the_flagged_draw_only(sampled) -> None:
    report = sampled(flagged_n=5)
    assert (report.precision.k, report.precision.n) == (3, 5)
    assert (report.n_flagged, report.n_discard) == (20, 16)  # the whole piles' sizes
    assert report.recall == metrics.recall(report.precision, report.elusion, 20, 16)
    assert report.richness == metrics.richness(report.precision, report.elusion, 20, 16)
    assert report.flagged_n == 5
    # the relevant seed outside the draw is in neither precision nor the works found
    assert sampled.seen["company_ids"] == DRAWN[:3]


def test_flagged_limit_does_not_move_the_discard_sample(sampled) -> None:
    with_limit = sampled(flagged_n=5).elusion
    # without a limit every flagged id needs a label: give the other fourteen one
    rest = [d for d in BIG if d.startswith("h") and d not in DRAWN and d != OUTSIDE]
    append_rows(gold_path("c", sampled.data_dir), [row(d, 0) for d in rest])
    assert sampled().elusion == with_limit  # and no MissingDiscardLabels: the same draw


def test_missing_label_in_the_flagged_draw_names_the_flag(sampled) -> None:
    from elusion.flows.evaluate import MissingLabels

    # a draw of 6 holds an id the judge, given 5, never labeled
    with pytest.raises(MissingLabels, match=r"of the draw have no .*--flagged-n"):
        sampled(flagged_n=6)


def test_evaluate_without_the_judges_flagged_limit_says_how_many_are_missing(sampled) -> None:
    from elusion.flows.evaluate import MissingLabels

    with pytest.raises(MissingLabels, match=r"14 flagged paragraphs have no.*--flagged-n"):
        sampled()


def test_precision_by_score_splits_the_same_sample(sampled) -> None:
    # of the five drawn: the first two score 3 (both relevant), the other three score 4
    # (one relevant)
    for d in DRAWN[:2]:
        sampled.scores[d] = 3
    report = sampled(flagged_n=5)
    assert {s: (e.k, e.n) for s, e in report.precision_by_score.items()} == {3: (2, 2), 4: (1, 3)}


def test_precision_by_score_leaves_out_a_score_nothing_was_drawn_at(sampled) -> None:
    assert set(sampled(flagged_n=5).precision_by_score) == {4}


def test_without_a_flagged_limit_nothing_changes(env) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    reports = []
    real_render = mod.render
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "render", lambda r: reports.append(r) or real_render(r))
        run(tmp_path)
    (report,) = reports
    assert report.flagged_n is None and report.precision_by_score is None


def test_grades_outside_the_flagged_draw_are_not_counted(sampled) -> None:
    from elusion.evaluate.grades import GradeRow, grades_path
    from elusion.evaluate.grades import append_rows as append_grades

    def g(doc, sound):
        return GradeRow(doc, "rev-model", "c/v2", 4, JUDGE, "c/grade-v1", sound, 1, "r", "t")

    not_judged = sorted(set(BIG) - set(DRAWN) - {OUTSIDE} - {d for d in BIG if d[0] == "g"})[0]
    append_grades(
        grades_path("c", sampled.data_dir),
        [g(d, 1) for d in DRAWN] + [g(OUTSIDE, 0), g(not_judged, 0)],
    )
    report = sampled(flagged_n=5)
    assert (report.rationale_sound.k, report.rationale_sound.n) == (5, 5)


# --- A criterion over the abstracts corpus: the literature sections (phase 7, task 6) ---


@pytest.fixture
def literature(sampled, monkeypatch):
    """`sampled`, with the criterion reviewed over the abstracts corpus.

    Every doc dNN is work W-dNN. The citation seeds are OUTSIDE (flagged, judged
    relevant, also found by a search term), DRAWN[0] (flagged, judged relevant, found
    by no term), DRAWN[4] (flagged, judged not relevant), one more flagged doc the judge
    never read, g00 (discarded) and a work that was never stored.
    """
    import elusion.flows.evaluate as mod
    from elusion.collect.custody import QueryCount, Seed
    from elusion.corpora import OPENALEX

    unjudged = sorted(set(BIG) - set(DRAWN) - {OUTSIDE} - {d for d in BIG if d[0] == "g"})[0]
    seeds = {
        f"W-{OUTSIDE}": Seed(("W3001807593",), True),
        f"W-{DRAWN[0]}": Seed(("W3001807593",), False),
        f"W-{DRAWN[4]}": Seed(("W3001807593",), True),
        f"W-{unjudged}": Seed(("W3001807593",), True),
        "W-g00": Seed(("W3001807593",), True),
        "W-gone": Seed(("W3001807593",), False),
    }
    monkeypatch.setattr(mod, "corpus_for", lambda criterion: OPENALEX)
    monkeypatch.setattr(mod, "seed_works", lambda engine: seeds)
    monkeypatch.setattr(mod, "distinct_works", lambda engine: 37)
    monkeypatch.setattr(
        mod,
        "works_per_query",
        lambda engine: [
            QueryCount("search", '"audit trail"', "stemmed", 36),
            QueryCount("citation", "W3001807593", None, 6),
            QueryCount("citation", "W-unknown-anchor", None, 1),
        ],
    )
    monkeypatch.setattr(mod, "doc_ids_by_work", lambda d: {f"W-{x}": {x} for x in BIG})
    monkeypatch.setattr(mod, "load_texts", lambda d: {x: f"Title of {x}\n\nBody." for x in BIG})
    return sampled


def test_literature_harvest_recall_counts_relevant_seeds_a_term_also_found(literature) -> None:
    lit = literature(flagged_n=5).literature
    assert (lit.seeds, lit.seeds_flagged, lit.seeds_judged, lit.seeds_relevant) == (6, 4, 3, 2)
    assert lit.harvest_recall == metrics.proportion(1, 2)


def test_literature_identified_names_an_anchor_by_its_label(literature) -> None:
    lit = literature(flagged_n=5).literature
    assert [(i.route, i.query, i.form, i.works) for i in lit.identified] == [
        ("search", '"audit trail"', "stemmed", 36),
        ("citation", "Raji and others 2020 (W3001807593)", None, 6),
        ("citation", "W-unknown-anchor", None, 1),
    ]
    assert lit.distinct_works == 37


def test_literature_works_are_the_relevant_of_the_flagged_sample(literature) -> None:
    lit = literature(flagged_n=5).literature
    assert lit.works == [(f"W-{d}", f"Title of {d}") for d in DRAWN[:3]]


def test_literature_report_file_has_the_sections(literature) -> None:
    literature(flagged_n=5)
    md = next((literature.seen and literature.data_dir).parent.glob("reports/*.md")).read_text()
    assert "## Records (PRISMA-style counts)" in md
    assert "## Harvest recall" in md
    assert "## Works kept" in md
    assert "## Companies found" not in md


def test_edgar_report_has_no_literature(sampled) -> None:
    assert sampled(flagged_n=5).literature is None
