"""Log one arm of `ask-eval` to MLflow, so the three arms can be compared side by side.

MLflow vocabulary, in plain words: an *experiment* is a folder of related runs (here
`elusion/ask`); a *run* is one attempt (here one arm, evaluated once); a *param* is an input
worth remembering; a *metric* is a number the run produced; an *artifact* is a file attached
to the run. Each proportion is logged as `<name>_point`, `_low`, `_high`, `_k` and `_n`, so
the leaderboard shows the interval and the sample behind a figure, not only the figure.

A run is logged once: `find_run` looks for a finished run of the same arm with the same params
and the same figures, so a re-run that only finishes a later arm (the logging of an earlier arm
had already succeeded) does not add a second run for the arms done. A changed figure or param
makes a new run.
Never logged: the questions file, the `ask_log`, any gold file or label. The caller picks
the artifacts and this module attaches exactly what it is given.

`import mlflow` happens inside `log_arm_run`, so `elusion --help` stays fast.
"""

from __future__ import annotations

from pathlib import Path

from elusion.ask.metrics import ArmReport
from elusion.evaluate.metrics import Estimate

EXPERIMENT = "elusion/ask"


def _estimate(name: str, e: Estimate) -> dict[str, float]:
    return {
        f"{name}_point": e.point,
        f"{name}_low": e.low,
        f"{name}_high": e.high,
        f"{name}_k": float(e.k),
        f"{name}_n": float(e.n),
    }


def arm_metrics(report: ArmReport) -> dict[str, float]:
    """The numbers of one arm. An estimate that does not exist (no claims at all) is left out."""
    m = _estimate("recall_at_k", report.recall_at_k)
    for name in ("groundedness_claims", "groundedness_questions"):
        estimate = getattr(report, name)
        if estimate is not None:
            m.update(_estimate(name, estimate))
    m["refusals_correct"] = float(report.refusals_correct)
    m["refusals_n"] = float(report.n_unanswerable)
    if report.refusals_correct_estimate is not None:
        e = report.refusals_correct_estimate
        m.update(
            {
                "refusals_correct_point": e.point,
                "refusals_correct_low": e.low,
                "refusals_correct_high": e.high,
            }
        )
    m["refusals_wrong"] = float(report.refusals_wrong)
    m["refusals_no_passage_unanswerable"] = float(report.no_passage_unanswerable)
    m["refusals_no_passage_answerable"] = float(report.no_passage_answerable)
    m["errors"] = float(report.errors)
    m["judge_unusable"] = float(report.judge_unusable)
    m["questions_not_judged"] = float(report.questions_not_judged)
    m["cost_usd"] = report.cost_usd
    return m


def find_run(
    report: ArmReport, params: dict[str, str], tracking_uri: str, *, max_runs: int = 1000
) -> str | None:
    """The id of a finished run in the experiment with exactly these params and these figures,
    or None. A run that was not finished (a crash mid-logging) never counts."""
    import math

    import mlflow

    client = mlflow.MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment_by_name(EXPERIMENT)
    if experiment is None:
        return None
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string="attributes.status = 'FINISHED'",
        max_results=max_runs,
        order_by=["attributes.start_time DESC"],
    )
    figures = arm_metrics(report)
    for run in runs:
        if run.data.params != params:
            continue
        logged = run.data.metrics
        if figures.keys() <= logged.keys() and all(
            math.isclose(logged[name], value, rel_tol=1e-9, abs_tol=1e-12)
            for name, value in figures.items()
        ):
            return run.info.run_id
    return None


def log_arm_run(
    report: ArmReport, params: dict[str, str], artifacts: list[Path], tracking_uri: str
) -> str:
    """Create one MLflow run for the arm and return its run id. `params` must name
    `arm`, `answer_model` and `prompt_version`: they make the run's name."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    name = f"{params['arm']}/{params['answer_model']}/{params['prompt_version']}"
    with mlflow.start_run(run_name=name) as run:
        mlflow.set_tags({"phase": "7", "elusion.arm": params["arm"]})
        mlflow.log_params(params)
        mlflow.log_metrics(arm_metrics(report))
        for path in artifacts:
            mlflow.log_artifact(str(path))
        return run.info.run_id
