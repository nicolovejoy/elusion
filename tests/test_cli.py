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
        "threshold": 2,
        "discard_n": 200,
        "seed": DEFAULT_SEED,
        "max_run_usd": None,
    }
    assert "judge summary" in result.output


def test_cli_seed_default_matches_gold_module() -> None:
    from elusion import cli
    from elusion.evaluate.gold import DEFAULT_SEED

    assert cli.DEFAULT_SEED == DEFAULT_SEED
