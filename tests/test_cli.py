from typer.testing import CliRunner

from elusion import __version__
from elusion.cli import app


def test_version_command_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_cli_import_is_lazy() -> None:
    import subprocess
    import sys

    code = "import sys, elusion.cli; print('prefect' in sys.modules, 'anthropic' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"


def test_review_command_passes_criterion_name(monkeypatch) -> None:
    import elusion.flows.review as mod

    seen = {}

    def fake_flow(criterion, sample, **kw):
        seen.update(criterion=criterion, sample=sample, **kw)
        return "summary"

    monkeypatch.setattr(mod, "review", fake_flow)
    result = CliRunner().invoke(
        app,
        [
            "review",
            "--criterion",
            "criteria/cloud-concentration.md",
            "--sample",
            "20",
            "--batch-size",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "criterion": "cloud-concentration",
        "sample": 20,
        "batch_size": 10,
        "concurrency": 8,
        "prompt_version": "v1",
        "max_run_usd": None,
        "provider_name": "anthropic",
        "model": None,
    }
    assert "summary" in result.output


def test_judge_command_passes_criterion_name_and_defaults(monkeypatch) -> None:
    import elusion.flows.evaluate as mod
    from elusion.evaluate.gold import DEFAULT_SEED

    seen = {}

    def fake_flow(criterion, **kw):
        seen.update(criterion=criterion, **kw)
        return "judge summary"

    monkeypatch.setattr(mod, "judge", fake_flow)
    result = CliRunner().invoke(
        app,
        ["judge", "--criterion", "criteria/cloud-concentration.md", "--prompt-version", "v2"],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "criterion": "cloud-concentration",
        "prompt_version": "v2",
        "threshold": 3,
        "discard_n": 200,
        "seed": DEFAULT_SEED,
        "review_model": None,
        "max_run_usd": None,
        "force": False,
    }
    assert "judge summary" in result.output


def test_cli_seed_default_matches_gold_module() -> None:
    from elusion import cli
    from elusion.evaluate.gold import DEFAULT_SEED

    assert cli.DEFAULT_SEED == DEFAULT_SEED


def test_evaluate_command_passes_criterion_name_and_prints_path(monkeypatch) -> None:
    from pathlib import Path

    import elusion.flows.evaluate as mod
    from elusion.evaluate.gold import DEFAULT_SEED
    from elusion.flows.evaluate import EvalResult

    seen = {}

    def fake_flow(criterion, **kw):
        seen.update(criterion=criterion, **kw)
        return EvalResult(Path("docs/reports/cloud-concentration-m-v2.md"), "a" * 32)

    monkeypatch.setattr(mod, "evaluate", fake_flow)
    result = CliRunner().invoke(
        app,
        ["evaluate", "--criterion", "criteria/cloud-concentration.md", "--prompt-version", "v2"],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "criterion": "cloud-concentration",
        "prompt_version": "v2",
        "threshold": 3,
        "seed": DEFAULT_SEED,
        "review_model": None,
        "discard_n": 200,
    }
    assert "docs/reports/cloud-concentration-m-v2.md" in result.output
    assert f"mlflow run {'a' * 32}" in result.output
    assert "/#/experiments" in result.output


def _crit(tmp_path):
    crit = tmp_path / "c.md"
    crit.write_text("x")
    return str(crit)


def test_judge_force_flag_is_passed(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    seen = {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen.update(kw) or "ok")
    r = CliRunner().invoke(
        app, ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v2", "--force"]
    )
    assert r.exit_code == 0, r.output
    assert seen["force"] is True


def test_judge_refusal_exits_1_without_traceback(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def refuse(criterion, **kw):
        raise mod.ReviewStillRunning("review job job-abc is still running")

    monkeypatch.setattr(mod, "judge", refuse)
    r = CliRunner().invoke(app, ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v2"])
    assert r.exit_code == 1
    assert "job-abc is still running" in r.output
    assert not isinstance(r.exception, mod.ReviewStillRunning)


def test_evaluate_missing_labels_exits_1_without_traceback(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def missing(criterion, **kw):
        raise mod.MissingLabels("2 flagged paragraphs have no label")

    monkeypatch.setattr(mod, "evaluate", missing)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
    )
    assert r.exit_code == 1
    assert "2 flagged paragraphs have no label" in r.output
    assert not isinstance(r.exception, mod.MissingLabels)


def test_evaluate_no_labeled_rows_exits_1(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def empty(criterion, **kw):
        raise ValueError("no labeled rows in the discard pile")

    monkeypatch.setattr(mod, "evaluate", empty)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
    )
    assert r.exit_code == 1
    assert "no labeled rows in the discard pile" in r.output


def test_evaluate_other_value_errors_still_raise(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def bug(criterion, **kw):
        raise ValueError("something else")

    monkeypatch.setattr(mod, "evaluate", bug)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
    )
    assert isinstance(r.exception, ValueError)


def test_review_model_and_discard_n_pass_through(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    import elusion.flows.evaluate as mod
    from elusion.flows.evaluate import EvalResult

    seen_j, seen_e = {}, {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen_j.update(kw) or "ok")
    monkeypatch.setattr(
        mod,
        "evaluate",
        lambda criterion, **kw: seen_e.update(kw) or EvalResult(Path("p.md"), None),
    )
    base = ["--criterion", _crit(tmp_path), "--prompt-version", "v2", "--review-model", "x"]
    assert CliRunner().invoke(app, ["judge", *base]).exit_code == 0
    assert CliRunner().invoke(app, ["evaluate", *base, "--discard-n", "50"]).exit_code == 0
    assert seen_j["review_model"] == "x"
    assert seen_e["review_model"] == "x" and seen_e["discard_n"] == 50


def test_evaluate_missing_discard_labels_exits_1(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def missing(criterion, **kw):
        raise mod.MissingDiscardLabels("2 sampled discard paragraphs have no m label")

    monkeypatch.setattr(mod, "evaluate", missing)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
    )
    assert r.exit_code == 1 and "Traceback" not in r.output
    assert "2 sampled discard paragraphs" in r.output


def test_review_provider_and_model_pass_through(monkeypatch) -> None:
    import elusion.flows.review as mod

    seen = {}
    monkeypatch.setattr(mod, "review", lambda criterion, sample, **kw: seen.update(kw) or "ok")
    base = ["review", "--criterion", "criteria/cloud-concentration.md", "--sample", "5"]
    assert CliRunner().invoke(app, base).exit_code == 0
    assert (seen["provider_name"], seen["model"]) == ("anthropic", None)
    r = CliRunner().invoke(app, [*base, "--provider", "openai", "--model", "m"])
    assert r.exit_code == 0, r.output
    assert (seen["provider_name"], seen["model"]) == ("openai", "m")


def test_grade_command_passes_options_through(monkeypatch, tmp_path) -> None:
    import elusion.flows.grade as mod

    seen = {}
    monkeypatch.setattr(
        mod, "grade", lambda criterion, **kw: seen.update(criterion=criterion, **kw) or "grade ok"
    )
    r = CliRunner().invoke(app, ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v2"])
    assert r.exit_code == 0, r.output
    assert seen == {
        "criterion": "c",
        "prompt_version": "v2",
        "review_model": None,
        "threshold": 3,
        "max_run_usd": None,
    }
    assert "grade ok" in r.output
    seen.clear()
    args = ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v3"]
    args += ["--review-model", "m", "--threshold", "4", "--max-run-usd", "2.5"]
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0, r.output
    got = (seen["prompt_version"], seen["review_model"], seen["threshold"], seen["max_run_usd"])
    assert got == ("v3", "m", 4, 2.5)
