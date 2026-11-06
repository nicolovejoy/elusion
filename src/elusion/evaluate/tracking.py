"""Log one `evaluate` result to MLflow, so runs can be compared side by side.

MLflow vocabulary, in plain words:

- An *experiment* is a folder of related runs. Here: one per criterion, `elusion/<criterion>`.
- A *run* is one attempt, here one (review model, prompt version) evaluated once.
- A *param* is an input you chose and want remembered (model, prompt version, threshold).
- A *metric* is a number the run produced (precision, cost). Each estimate is logged as
  several numbers, `<name>_point`, `_low`, `_high`, `_k`, `_n`, so the leaderboard can
  show the interval and the sample behind it, not just the point value. `_k` is -1 for
  derived estimates (richness, recall), which have no k of their own.
- An *artifact* is a file attached to the run: the report, the prompt, the criterion.

Never logged: the gold CSV, any grades file, any label. The caller picks the artifacts;
this module attaches exactly what it is given.

`import mlflow` happens inside `log_run`, so `elusion --help` stays fast.
"""

from __future__ import annotations

import json
from pathlib import Path

from elusion.evaluate.metrics import Estimate
from elusion.evaluate.report import RunReport
from elusion.review.providers import GENERATION_SETTINGS


def experiment_name(criterion: str) -> str:
    return f"elusion/{criterion}"


def pair_tag(report: RunReport) -> str:
    return f"{report.review_model}|{report.prompt_version}"


def run_params(report: RunReport, provider: str, report_path: Path) -> dict[str, str]:
    params = {
        "criterion": report.criterion,
        "review_model": report.review_model,
        "prompt_version": report.prompt_version,
        "provider": provider,
        "generation_settings": json.dumps(GENERATION_SETTINGS.get(provider, {}), sort_keys=True),
        "judge_model": report.judge_model,
        "judge_prompt_version": report.judge_prompt_version,
        "threshold": str(report.threshold),
        "seed": str(report.seed),
        "discard_n": str(report.discard_n),
        "review_jobs": str(report.review_jobs),
        "report_path": str(report_path),
    }
    if report.grader_model is not None:
        params["grader_model"] = report.grader_model
    if report.grader_prompt_version is not None:
        params["grader_prompt_version"] = report.grader_prompt_version
    return params


def _estimate(name: str, e: Estimate) -> dict[str, float]:
    return {
        f"{name}_point": e.point,
        f"{name}_low": e.low,
        f"{name}_high": e.high,
        f"{name}_k": float(e.k),
        f"{name}_n": float(e.n),
    }


def run_metrics(report: RunReport) -> dict[str, float]:
    m: dict[str, float] = {}
    for name in ("precision", "elusion", "richness", "recall"):
        m.update(_estimate(name, getattr(report, name)))
    if report.agreement is not None:
        m.update(_estimate("agreement", report.agreement))
    m["population"] = float(report.population)
    m["n_flagged"] = float(report.n_flagged)
    m["n_discard"] = float(report.n_discard)
    m["review_cost_usd"] = report.review_cost_usd
    m["judge_cost_usd"] = report.judge_cost_usd
    if report.rationale_sound is not None:
        m.update(_estimate("rationale_sound", report.rationale_sound))
    if report.quote_verbatim is not None:
        m.update(_estimate("quote_verbatim", report.quote_verbatim))
    if report.rationale_sound is not None or report.quote_verbatim is not None:
        m["grade_cost_usd"] = report.grade_cost_usd
    m["total_cost_usd"] = report.review_cost_usd + report.judge_cost_usd + report.grade_cost_usd
    m["cost_per_1000_docs_usd"] = (
        report.review_cost_usd / report.population * 1000 if report.population else 0.0
    )
    if report.review_wall_clock is not None:
        seconds = report.review_wall_clock.total_seconds()
        m["review_wall_clock_s"] = seconds
        if seconds > 0:
            m["docs_per_minute"] = report.population / (seconds / 60)
    return m


def log_run(
    report: RunReport,
    provider: str,
    artifacts: list[Path],
    tracking_uri: str,
    report_path: Path,
) -> str:
    """Create one MLflow run for `report` and return its run id."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name(report.criterion))
    with mlflow.start_run(run_name=f"{report.review_model}/{report.prompt_version}") as run:
        mlflow.set_tags({"phase": "4", "elusion.pair": pair_tag(report)})
        mlflow.log_params(run_params(report, provider, report_path))
        mlflow.log_metrics(run_metrics(report))
        for path in artifacts:
            mlflow.log_artifact(str(path))
        return run.info.run_id
