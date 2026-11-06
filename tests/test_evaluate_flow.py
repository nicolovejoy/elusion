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

    def fake_scores_for(engine, model, prompt_version):
        seen["scores_for"] = (engine, model, prompt_version)
        return dict(SCORES)

    def fake_jobs_summary(engine, model, prompt_version):
        seen["jobs_summary"] = (engine, model, prompt_version)
        return {
            "jobs": 2,
            "cost_usd": 0.75,
            "started_at": T0,
            "finished_at": T0 + timedelta(hours=1, minutes=2, seconds=3),
            "unfinished": False,
            "unfinished_ids": [],
        }

    def fake_company_counts(engine, doc_ids):
        seen["company_ids"] = sorted(doc_ids)
        return [("Acme Corp", len(seen["company_ids"]))]

    monkeypatch.setattr(mod, "scores_for", fake_scores_for)
    monkeypatch.setattr(mod, "jobs_summary", fake_jobs_summary)
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
    assert path == tmp_path / "reports" / "c-rev-model-v2.md"
    md = path.read_text()

    assert seen["scores_for"] == (FAKE_ENGINE, "rev-model", "c/v2")
    assert seen["jobs_summary"] == (FAKE_ENGINE, "rev-model", "c/v2")
    assert seen["company_ids"] == ["f00", "f01", "f02"]  # flagged and judged relevant

    assert "- population N: 20" in md
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
    assert "$0.7500 (sum over 2 review_jobs rows)" in md
    assert "- judge run: $0.7500" in md
    assert "across 2 review_jobs rows: 1:02:03" in md
    assert "- Acme Corp: 3" in md
    assert "-1/" not in md and "corpus" not in md


def test_piles_come_from_scores_not_the_csv_pile_column(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    # g00 now scores 3: it moves into the flagged pile (its judge label is 1) although
    # the CSV still says "discard"; f03 drops to 2 and is now a discard-sample row.
    rescored = {**SCORES, "g00": 3, "f03": 2}
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: dict(rescored))
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
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: {**SCORES, "x01": 4, "x02": 3, "x03": 2})
    with pytest.raises(mod.MissingLabels, match=r"2 flagged .*elusion judge"):
        run(tmp_path)
    assert not (tmp_path / "reports").exists()


def test_no_scores_is_an_error(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: {})
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
        lambda e, m, p: {
            "jobs": 1,
            "cost_usd": 0.1,
            "started_at": T0,
            "finished_at": None,
            "unfinished": True,
            "unfinished_ids": ["job-abc"],
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
    names = {a.path for a in client.list_artifacts(result.run_id)}
    assert "c-rev-model-v2.md" in names
    assert {"v2.md", "c.md"} <= names  # the prompt file and the criterion file
    assert not any(n.endswith(".csv") for n in names)


def test_report_is_written_even_when_logging_fails(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(mod, "log_run", boom)
    monkeypatch.setattr(mod, "provider_for", lambda e, m, v: "anthropic")
    with pytest.raises(RuntimeError, match="report written to .*c-rev-model-v2.md"):
        run(tmp_path, log_mlflow=True)
    assert (tmp_path / "reports" / "c-rev-model-v2.md").is_file()


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
    append_grade_run(ledger, {**pair, "cost_usd": 0.25})
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
    assert "- total: $1.7500" in md  # 0.75 review + 0.75 judge + 0.25 grade


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
