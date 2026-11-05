"""MLflow logging, against a file:// tracking store under tmp_path: nothing leaves the machine."""

from pathlib import Path

import mlflow
import pytest

from elusion.evaluate.tracking import experiment_name, log_run, run_metrics, run_params
from tests.test_report import make_report


@pytest.fixture
def report():
    return make_report(criterion="c")


@pytest.fixture
def report_without_agreement():
    return make_report(agreement=None)


def test_experiment_name() -> None:
    assert experiment_name("c") == "elusion/c"


def test_run_metrics_has_point_low_high_k_n_for_each_estimate(report):
    m = run_metrics(report)
    for name in ("precision", "elusion", "richness", "recall", "agreement"):
        assert {
            f"{name}_point",
            f"{name}_low",
            f"{name}_high",
            f"{name}_k",
            f"{name}_n",
        } <= m.keys()
    assert m["recall_k"] == -1
    assert m["total_cost_usd"] == pytest.approx(report.review_cost_usd + report.judge_cost_usd)
    assert m["cost_per_1000_docs_usd"] == pytest.approx(
        report.review_cost_usd / report.population * 1000
    )
    assert m["review_wall_clock_s"] == report.review_wall_clock.total_seconds()
    assert m["docs_per_minute"] == pytest.approx(
        report.population / (report.review_wall_clock.total_seconds() / 60)
    )


def test_run_metrics_omits_wall_clock_metrics_when_unknown():
    m = run_metrics(make_report(review_wall_clock=None))
    assert "review_wall_clock_s" not in m and "docs_per_minute" not in m


def test_run_params_names_the_pair_and_settings(report):
    p = run_params(report, "anthropic", Path("docs/reports/r.md"))
    assert p["review_model"] == report.review_model and p["provider"] == "anthropic"
    assert p["generation_settings"] == '{"effort": "low"}'
    assert p["threshold"] == "2" and p["discard_n"] == "200"
    assert p["review_jobs"] == str(report.review_jobs) == "2"
    assert p["report_path"] == "docs/reports/r.md"


def test_log_run_writes_params_metrics_and_only_safe_artifacts(report, tmp_path):
    uri = f"file://{tmp_path}/mlruns"
    prompt = tmp_path / "v2.md"
    prompt.write_text("prompt")
    gold = tmp_path / "gold.csv"
    gold.write_text("doc_id,label\n")
    run_id = log_run(report, "anthropic", [prompt], uri, Path("docs/reports/r.md"))
    client = mlflow.MlflowClient(tracking_uri=uri)
    run = client.get_run(run_id)
    assert run.data.params["prompt_version"] == report.prompt_version
    assert run.data.params["report_path"] == "docs/reports/r.md"
    assert run.data.params["review_jobs"] == "2"
    assert run.data.metrics["precision_point"] == pytest.approx(report.precision.point)
    assert run.data.tags["elusion.pair"] == f"{report.review_model}|{report.prompt_version}"
    names = {a.path for a in client.list_artifacts(run_id)}
    assert "v2.md" in names and "gold.csv" not in names
    exp = client.get_experiment(run.info.experiment_id)
    assert exp.name == "elusion/c"


def test_agreement_none_logs_no_agreement_metrics(report_without_agreement):
    assert not any(k.startswith("agreement_") for k in run_metrics(report_without_agreement))
