"""MLflow logging of one `ask-eval` arm, against a file:// store under tmp_path."""

import mlflow
import pytest

from elusion.ask.metrics import ClaimResult, EvalRow, arm_report
from elusion.ask.tracking import EXPERIMENT, arm_metrics, log_arm_run

OK = ClaimResult(True, True)
PARAMS = {
    "arm": "hybrid",
    "k": "5",
    "answer_model": "claude-sonnet-5-5",
    "prompt_version": "ask/v1",
    "judge_model": "claude-opus-5-5",
    "report_path": "docs/reports/r.md",
}


def make_report():
    rows = [
        EvalRow("q01", "answerable", True, "answered", (OK, OK)),
        EvalRow("q02", "answerable", False, "answered", (OK, ClaimResult(True, False))),
        EvalRow("q03", "answerable", True, "refused", ()),
        EvalRow("u1", "unanswerable", None, "refused", ()),
        EvalRow("u2", "unanswerable", None, "error", ()),
    ]
    return arm_report("hybrid", 5, rows, 0.25)


def test_params_and_metrics_are_logged(tmp_path) -> None:
    uri = f"file://{tmp_path}/mlruns"
    report_file = tmp_path / "report.md"
    report_file.write_text("report")
    report = make_report()
    run_id = log_arm_run(report, PARAMS, [report_file], uri)
    client = mlflow.MlflowClient(tracking_uri=uri)
    run = client.get_run(run_id)
    assert run.data.params == PARAMS
    assert run.data.tags["phase"] == "7" and run.data.tags["elusion.arm"] == "hybrid"
    assert run.info.run_name == "hybrid/claude-sonnet-5-5/ask/v1"
    assert client.get_experiment(run.info.experiment_id).name == EXPERIMENT == "elusion/ask"
    m = run.data.metrics
    assert (m["recall_at_k_k"], m["recall_at_k_n"]) == (2, 3)
    assert m["recall_at_k_point"] == pytest.approx(2 / 3)
    assert m["recall_at_k_low"] < m["recall_at_k_point"] < m["recall_at_k_high"]
    assert (m["groundedness_claims_k"], m["groundedness_claims_n"]) == (3, 4)
    assert (m["groundedness_questions_k"], m["groundedness_questions_n"]) == (1, 2)
    assert (m["refusals_correct"], m["refusals_n"], m["refusals_wrong"]) == (1, 2, 1)
    assert (m["errors"], m["judge_unusable"], m["cost_usd"]) == (1, 0, 0.25)
    assert {a.path for a in client.list_artifacts(run_id)} == {"report.md"}


def test_an_estimate_that_does_not_exist_logs_no_metric() -> None:
    rows = [EvalRow("q01", "answerable", True, "refused", ())]
    m = arm_metrics(arm_report("lexical", 5, rows, 0.0))
    assert "groundedness_claims_point" not in m and "groundedness_questions_point" not in m
    assert m["recall_at_k_point"] == 1.0


def test_the_questions_file_is_never_an_artifact(tmp_path) -> None:
    """The function attaches exactly the files it is given, and the flow never gives it the
    questions file; the file's hash is a param, its content is nowhere."""
    uri = f"file://{tmp_path}/mlruns"
    report_file = tmp_path / "report.md"
    report_file.write_text("report")
    questions = tmp_path / "auditable-decisions.questions.csv"
    questions.write_text("question_id,question\nq01,secret question?\n")
    run_id = log_arm_run(
        make_report(), {**PARAMS, "questions_sha256": "ab" * 32}, [report_file], uri
    )
    client = mlflow.MlflowClient(tracking_uri=uri)
    names = {a.path for a in client.list_artifacts(run_id)}
    assert questions.name not in names and not any(n.endswith(".csv") for n in names)
    assert "secret question" not in str(client.get_run(run_id).data.params)
