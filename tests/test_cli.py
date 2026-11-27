from typer.testing import CliRunner

from elusion import __version__
from elusion.cli import app

# judge, grade and evaluate refuse to run without the filing years of their population.
YEARS = ["--year", "2019", "--year", "2018"]


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
        "prompt_version": None,
        "max_run_usd": None,
        "provider_name": None,
        "model": None,
        "reviewer": None,
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
        [
            "judge",
            "--criterion",
            "criteria/cloud-concentration.md",
            "--prompt-version",
            "v2",
            *YEARS,
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "criterion": "cloud-concentration",
        "prompt_version": "v2",
        "years": [2018, 2019],
        "threshold": 3,
        "discard_n": 200,
        "seed": DEFAULT_SEED,
        "review_model": None,
        "max_run_usd": None,
        "force": False,
        "flagged_n": None,
        "only_human": False,
        "seeds": False,
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
        [
            "evaluate",
            "--criterion",
            "criteria/cloud-concentration.md",
            "--prompt-version",
            "v2",
            *YEARS,
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "criterion": "cloud-concentration",
        "prompt_version": "v2",
        "years": [2018, 2019],
        "threshold": 3,
        "seed": DEFAULT_SEED,
        "review_model": None,
        "discard_n": 200,
        "flagged_n": None,
    }
    assert "docs/reports/cloud-concentration-m-v2.md" in result.output
    assert f"mlflow run {'a' * 32}" in result.output
    assert "/#/experiments" in result.output


def test_evaluate_cli_passes_flagged_n(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    import elusion.flows.evaluate as mod
    from elusion.flows.evaluate import EvalResult

    seen = {}
    monkeypatch.setattr(
        mod, "evaluate", lambda criterion, **kw: seen.update(kw) or EvalResult(Path("r.md"), None)
    )
    args = ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--flagged-n", "400"])
    assert r.exit_code == 0, r.output
    assert seen["flagged_n"] == 400


def _crit(tmp_path):
    crit = tmp_path / "c.md"
    crit.write_text("x")
    return str(crit)


def test_judge_force_flag_is_passed(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    seen = {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen.update(kw) or "ok")
    r = CliRunner().invoke(
        app, ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS, "--force"]
    )
    assert r.exit_code == 0, r.output
    assert seen["force"] is True


def test_judge_cli_passes_flagged_n_and_only_labeled(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    seen = {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen.update(kw) or "ok")
    args = ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--flagged-n", "400", "--only-labeled"])
    assert r.exit_code == 0, r.output
    assert (seen["flagged_n"], seen["only_human"]) == (400, True)


def test_judge_cli_passes_seeds(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    seen = {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen.update(kw) or "ok")
    args = ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--seeds"])
    assert r.exit_code == 0, r.output
    assert seen["seeds"] is True


def test_judge_cli_rejects_seeds_with_only_labeled(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    called = []
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: called.append(kw) or "ok")
    args = ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--seeds", "--only-labeled"])
    assert r.exit_code == 2
    assert "--seeds" in r.output and "--only-labeled" in r.output
    assert called == []


def test_judge_refusal_exits_1_without_traceback(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def refuse(criterion, **kw):
        raise mod.ReviewStillRunning("review job job-abc is still running")

    monkeypatch.setattr(mod, "judge", refuse)
    r = CliRunner().invoke(
        app, ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
    )
    assert r.exit_code == 1
    assert "job-abc is still running" in r.output
    assert not isinstance(r.exception, mod.ReviewStillRunning)


def test_evaluate_missing_labels_exits_1_without_traceback(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def missing(criterion, **kw):
        raise mod.MissingLabels("2 flagged paragraphs have no label")

    monkeypatch.setattr(mod, "evaluate", missing)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
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
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
    )
    assert r.exit_code == 1
    assert "no labeled rows in the discard pile" in r.output


def test_evaluate_other_value_errors_still_raise(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    def bug(criterion, **kw):
        raise ValueError("something else")

    monkeypatch.setattr(mod, "evaluate", bug)
    r = CliRunner().invoke(
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
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
    base = ["--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS, "--review-model", "x"]
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
        app, ["evaluate", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
    )
    assert r.exit_code == 1 and "Traceback" not in r.output
    assert "2 sampled discard paragraphs" in r.output


def test_review_provider_and_model_pass_through(monkeypatch) -> None:
    import elusion.flows.review as mod

    seen = {}
    monkeypatch.setattr(mod, "review", lambda criterion, sample, **kw: seen.update(kw) or "ok")
    base = ["review", "--criterion", "criteria/cloud-concentration.md", "--sample", "5"]
    assert CliRunner().invoke(app, base).exit_code == 0
    assert (seen["provider_name"], seen["model"]) == (None, None)
    r = CliRunner().invoke(app, [*base, "--provider", "openai", "--model", "m"])
    assert r.exit_code == 0, r.output
    assert (seen["provider_name"], seen["model"]) == ("openai", "m")


def test_review_reviewer_passes_through(monkeypatch) -> None:
    import elusion.flows.review as mod

    seen = {}
    monkeypatch.setattr(mod, "review", lambda criterion, sample, **kw: seen.update(kw) or "ok")
    args = ["review", "--criterion", "criteria/cloud-concentration.md", "--sample", "5"]
    r = CliRunner().invoke(app, [*args, "--reviewer", "elusion-reviewer@champion"])
    assert r.exit_code == 0, r.output
    assert seen["reviewer"] == "elusion-reviewer@champion"


def test_review_user_errors_exit_1_without_traceback(monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.review.providers import ProviderConfigError
    from elusion.review.registry import ReviewerError

    args = ["review", "--criterion", "criteria/cloud-concentration.md", "--sample", "5"]
    for exc in (
        ReviewerError("no alias 'x' on registered model 'm'"),
        ProviderConfigError("no key"),
    ):

        def fail(criterion, sample, _exc=exc, **kw):
            raise _exc

        monkeypatch.setattr(mod, "review", fail)
        r = CliRunner().invoke(app, [*args, "--reviewer", "m@x"])
        assert r.exit_code == 1 and str(exc) in r.output
        assert "Traceback" not in r.output


def test_review_other_lookup_and_value_errors_still_raise(monkeypatch) -> None:
    """A KeyError from the corpus is a bug, not a reviewer problem: keep its traceback."""
    import elusion.flows.review as mod

    args = ["review", "--criterion", "criteria/cloud-concentration.md", "--sample", "5"]
    for exc in (KeyError("3 ids not in the Parquet corpus"), ValueError("a bug")):

        def fail(criterion, sample, _exc=exc, **kw):
            raise _exc

        monkeypatch.setattr(mod, "review", fail)
        r = CliRunner().invoke(app, args)
        assert r.exit_code == 1 and r.exception is exc


def test_promote_command_prints_version_alias_and_registry_url(monkeypatch) -> None:
    import elusion.review.registry as mod

    seen = {}

    def fake_promote(run_id, name, alias, tracking_uri):
        seen.update(run_id=run_id, name=name, alias=alias)
        return 3

    monkeypatch.setattr(mod, "promote", fake_promote)
    r = CliRunner().invoke(app, ["promote", "--run", "abc123"])
    assert r.exit_code == 0, r.output
    assert seen == {"run_id": "abc123", "name": "elusion-reviewer", "alias": "champion"}
    assert "elusion-reviewer version 3 aliased champion" in r.output
    assert "http://localhost:5001/#/models/elusion-reviewer" in r.output
    r = CliRunner().invoke(
        app, ["promote", "--run", "abc123", "--name", "other", "--alias", "challenger"]
    )
    assert (seen["name"], seen["alias"]) == ("other", "challenger")


def test_grade_cli_passes_flagged_n_and_seed(monkeypatch, tmp_path) -> None:
    import elusion.flows.grade as mod

    seen = {}
    monkeypatch.setattr(mod, "grade", lambda criterion, **kw: seen.update(kw) or "ok")
    args = ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--flagged-n", "400", "--seed", "11"])
    assert r.exit_code == 0, r.output
    assert (seen["flagged_n"], seen["seed"]) == (400, 11)


def test_grade_refusal_exits_1_without_traceback(monkeypatch, tmp_path) -> None:
    import elusion.flows.grade as mod
    from elusion.flows.evaluate import ReviewStillRunning

    seen = {}

    def flow(criterion, **kw):
        seen.update(kw)
        if not kw["force"]:
            raise ReviewStillRunning("review job(s) job-abc still running")
        return "ok"

    monkeypatch.setattr(mod, "grade", flow)
    args = ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v1", *YEARS]
    r = CliRunner().invoke(app, [*args, "--flagged-n", "400"])
    assert r.exit_code == 1 and "error: review job(s) job-abc" in r.stderr
    assert "Traceback" not in r.output
    assert CliRunner().invoke(app, [*args, "--flagged-n", "400", "--force"]).exit_code == 0


def test_grade_command_passes_options_through(monkeypatch, tmp_path) -> None:
    import elusion.flows.grade as mod
    from elusion.evaluate.gold import DEFAULT_SEED

    seen = {}
    monkeypatch.setattr(
        mod, "grade", lambda criterion, **kw: seen.update(criterion=criterion, **kw) or "grade ok"
    )
    r = CliRunner().invoke(
        app, ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v2", *YEARS]
    )
    assert r.exit_code == 0, r.output
    assert seen == {
        "criterion": "c",
        "prompt_version": "v2",
        "years": [2018, 2019],
        "review_model": None,
        "threshold": 3,
        "max_run_usd": None,
        "flagged_n": None,
        "seed": DEFAULT_SEED,
        "force": False,
    }
    assert "grade ok" in r.output
    seen.clear()
    args = ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v3", *YEARS]
    args += ["--review-model", "m", "--threshold", "4", "--max-run-usd", "2.5"]
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0, r.output
    got = (seen["prompt_version"], seen["review_model"], seen["threshold"], seen["max_run_usd"])
    assert got == ("v3", "m", 4, 2.5)


def test_promote_prints_the_registry_under_the_configured_tracking_uri(monkeypatch) -> None:
    import elusion.config as config
    import elusion.review.registry as mod

    monkeypatch.setattr(config, "settings", config.Settings(mlflow_tracking_uri="http://m:9"))
    monkeypatch.setattr(mod, "promote", lambda *a: 4)
    r = CliRunner().invoke(app, ["promote", "--run", "abc"])
    assert "http://m:9/#/models/elusion-reviewer" in r.output


def test_promote_reviewer_error_exits_1_without_traceback(monkeypatch) -> None:
    import elusion.review.registry as mod

    def bad(*a):
        raise mod.ReviewerError("no run 'x'")

    monkeypatch.setattr(mod, "promote", bad)
    r = CliRunner().invoke(app, ["promote", "--run", "x"])
    assert r.exit_code == 1 and "error: no run 'x'" in r.output


def test_judge_grade_and_evaluate_refuse_to_run_without_a_year(monkeypatch, tmp_path) -> None:
    """No default: a pair can hold several populations, and a run must name its own."""
    import elusion.flows.evaluate as ev
    import elusion.flows.grade as gr

    called = []
    monkeypatch.setattr(ev, "judge", lambda *a, **kw: called.append("judge"))
    monkeypatch.setattr(ev, "evaluate", lambda *a, **kw: called.append("evaluate"))
    monkeypatch.setattr(gr, "grade", lambda *a, **kw: called.append("grade"))
    for command in ("judge", "grade", "evaluate"):
        r = CliRunner().invoke(
            app, [command, "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
        )
        assert r.exit_code == 2, command
        assert "--year" in r.output, command
    assert called == []


def test_grade_command_passes_the_years(monkeypatch, tmp_path) -> None:
    import elusion.flows.grade as mod

    seen = {}
    monkeypatch.setattr(mod, "grade", lambda criterion, **kw: seen.update(kw) or "graded")
    r = CliRunner().invoke(
        app, ["grade", "--criterion", _crit(tmp_path), "--prompt-version", "v2", "--year", "2020"]
    )
    assert r.exit_code == 0, r.output
    assert seen["years"] == [2020] and "graded" in r.output


def test_a_year_given_twice_counts_once(monkeypatch, tmp_path) -> None:
    import elusion.flows.evaluate as mod

    seen = {}
    monkeypatch.setattr(mod, "judge", lambda criterion, **kw: seen.update(kw) or "ok")
    args = ["judge", "--criterion", _crit(tmp_path), "--prompt-version", "v2"]
    r = CliRunner().invoke(app, [*args, "--year", "2020", "--year", "2020"])
    assert r.exit_code == 0, r.output
    assert seen["years"] == [2020]


def test_harvest_command_runs_the_flow_and_prints_its_summary(monkeypatch) -> None:
    import elusion.flows.harvest as mod

    seen = {}

    def fake_flow(**kw):
        seen.update(kw)
        return "harvest summary"

    monkeypatch.setattr(mod, "harvest", fake_flow)
    result = CliRunner().invoke(app, ["harvest", "--max-pages", "1"])
    assert result.exit_code == 0, result.output
    assert seen == {"max_pages": 1, "seeds_only": False}
    assert "harvest summary" in result.output

    assert CliRunner().invoke(app, ["harvest", "--seeds-only"]).exit_code == 0
    assert seen == {"max_pages": None, "seeds_only": True}


def test_concept_command_adds_a_version_with_search_terms_and_criterion(monkeypatch) -> None:
    import elusion.board.log as log
    import elusion.collect.db as db

    seen = {}
    monkeypatch.setattr(db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(log, "ensure_schema", lambda engine: seen.update(schema=engine))

    def fake_add(engine, concept, wording, actor, detail=None):
        seen.update(engine=engine, concept=concept, wording=wording, actor=actor, detail=detail)
        return 3

    monkeypatch.setattr(log, "add_concept", fake_add)
    result = CliRunner().invoke(
        app,
        [
            "concept",
            "--name",
            "stopping",
            "--wording",
            "When should a review stop?",
            "--term",
            "stopping rule",
            "--term",
            "recall target",
            "--criterion",
            "auditable-decisions",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "schema": "engine",
        "engine": "engine",
        "concept": "stopping",
        "wording": "When should a review stop?",
        "actor": "claude",
        "detail": {
            "search_terms": ["stopping rule", "recall target"],
            "criterion": "auditable-decisions",
        },
    }
    assert "stopping" in result.output and "3" in result.output


def _stub_concept(monkeypatch):
    import elusion.board.log as log
    import elusion.collect.db as db

    seen = {}
    monkeypatch.setattr(db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(log, "ensure_schema", lambda engine: None)

    def fake_add(engine, concept, wording, actor, detail=None):
        seen.update(detail=detail)
        return 1

    monkeypatch.setattr(log, "add_concept", fake_add)
    return seen


def _concept_args(*criterion):
    return ["concept", "--name", "s", "--wording", "w?", "--term", "t", *criterion]


def test_concept_stores_the_stem_of_a_path_form_criterion(monkeypatch) -> None:
    seen = _stub_concept(monkeypatch)
    result = CliRunner().invoke(
        app, _concept_args("--criterion", "criteria/auditable-decisions.md")
    )
    assert result.exit_code == 0, result.output
    assert seen["detail"]["criterion"] == "auditable-decisions"


def test_concept_refuses_a_criterion_with_no_file_or_no_corpus_and_writes_nothing(
    monkeypatch, tmp_path
) -> None:
    seen = _stub_concept(monkeypatch)
    result = CliRunner().invoke(app, _concept_args("--criterion", "nope"))
    assert result.exit_code == 1
    assert result.output.startswith("error: ") and "nope" in result.output
    assert "Traceback" not in result.output
    assert seen == {}

    # A criteria file exists but the stem names no corpus.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "criteria").mkdir()
    (tmp_path / "criteria" / "orphan.md").write_text("x")
    result = CliRunner().invoke(app, _concept_args("--criterion", "criteria/orphan.md"))
    assert result.exit_code == 1
    assert result.output.startswith("error: ") and "orphan" in result.output
    assert seen == {}


def test_concept_requires_a_criterion(monkeypatch) -> None:
    seen = _stub_concept(monkeypatch)
    result = CliRunner().invoke(app, _concept_args())
    assert result.exit_code != 0
    assert seen == {}


def test_first_read_command_builds_the_reader_and_prints_the_summary(monkeypatch) -> None:
    import elusion.flows.first_read as mod
    import elusion.review.providers as providers

    seen = {}

    class StubProvider:
        def __init__(self, model):
            self.model = model

    def fake_flow(concept, reader, **kw):
        seen.update(concept=concept, reader=reader, **kw)
        return {
            "matched": 5,
            "already_read": 1,
            "read": 2,
            "holds": 1,
            "does_not_hold": 1,
            "failed": 0,
            "cost_usd": 0.5,
        }

    monkeypatch.setattr(providers, "AnthropicProvider", StubProvider)
    monkeypatch.setattr(mod, "first_read", fake_flow)
    result = CliRunner().invoke(app, ["first-read", "--concept", "stopping"])
    assert result.exit_code == 0, result.output
    assert seen["concept"] == "stopping"
    assert seen["reader"].model == "claude-haiku-5-5"
    # The verdict counts are what the blind audit must not see before it is done.
    assert "holds" not in result.output and "does_not_hold" not in result.output
    assert "'read': 2" in result.output and "'matched': 5" in result.output

    CliRunner().invoke(app, ["first-read", "--concept", "stopping", "--model", "other-model"])
    assert seen["reader"].model == "other-model"


def test_first_read_budget_and_unknown_concept_exit_1_without_traceback(monkeypatch) -> None:
    import elusion.flows.first_read as mod
    import elusion.review.providers as providers
    from elusion.board.log import UnknownConcept
    from elusion.flows.review import BudgetExceeded

    monkeypatch.setattr(providers, "AnthropicProvider", lambda model: object())
    for exc, text in [
        (BudgetExceeded("projected cost $2 is above the limit of $1"), "above the limit"),
        (UnknownConcept("nope"), "nope"),
    ]:

        def fake_flow(concept, reader, _exc=exc, **kw):
            raise _exc

        monkeypatch.setattr(mod, "first_read", fake_flow)
        result = CliRunner().invoke(app, ["first-read", "--concept", "nope"])
        assert result.exit_code == 1
        assert text in result.output
        assert "Traceback" not in result.output


def test_index_command_passes_the_pair_and_prints_the_summary(monkeypatch, tmp_path) -> None:
    import elusion.ask.embed as embed
    import elusion.flows.index as mod

    seen = {}

    class StubEmbedder:
        model = "stub-embedder"

    def fake_flow(criterion, embedder, **kw):
        seen.update(criterion=criterion, embedder=embedder, **kw)
        return {"screened_in": 5, "embedded": 4, "cost_usd": 0.01}

    monkeypatch.setattr(embed, "OpenAIEmbedder", StubEmbedder)
    monkeypatch.setattr(mod, "build_index", fake_flow)
    args = ["index", "--criterion", _crit(tmp_path), "--prompt-version", "v1"]
    result = CliRunner().invoke(app, [*args, "--review-model", "gpt-6-luna"])
    assert result.exit_code == 0, result.output
    assert seen["criterion"] == "c"
    assert isinstance(seen["embedder"], StubEmbedder)
    assert seen["review_model"] == "gpt-6-luna"
    assert seen["prompt_version"] == "v1"
    assert seen["threshold"] == 3
    assert seen["max_usd"] is None
    assert "'screened_in': 5" in result.output and "'embedded': 4" in result.output

    result = CliRunner().invoke(
        app, [*args, "--review-model", "other", "--threshold", "4", "--max-usd", "2.5"]
    )
    assert result.exit_code == 0, result.output
    assert (seen["review_model"], seen["threshold"], seen["max_usd"]) == ("other", 4, 2.5)

    missing = CliRunner().invoke(
        app,
        [
            "index",
            "--criterion",
            "nope.md",
            "--prompt-version",
            "v1",
            "--review-model",
            "gpt-6-luna",
        ],
    )
    assert missing.exit_code == 2
    assert "criterion file not found" in missing.output


def test_index_errors_exit_1_without_a_traceback(monkeypatch, tmp_path) -> None:
    import elusion.ask.embed as embed
    import elusion.flows.index as mod
    from elusion.ask.index import IndexPairMismatch
    from elusion.corpora import UnknownCriterion
    from elusion.flows.review import BudgetExceeded
    from elusion.review.providers import ProviderConfigError

    monkeypatch.setattr(embed, "OpenAIEmbedder", lambda: object())
    args = ["index", "--criterion", _crit(tmp_path), "--prompt-version", "v1"]
    args += ["--review-model", "gpt-6-luna"]
    for exc, text in [
        (BudgetExceeded("projected cost $2 is above the limit of $1"), "above the limit"),
        (IndexPairMismatch("index holds another pair"), "another pair"),
        (UnknownCriterion("no corpus"), "no corpus"),
        (ValueError("ask searches abstracts only"), "abstracts only"),
    ]:

        def fake_flow(criterion, embedder, _exc=exc, **kw):
            raise _exc

        monkeypatch.setattr(mod, "build_index", fake_flow)
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1, result.output
        assert text in result.output
        assert "Traceback" not in result.output

    def no_key():
        raise ProviderConfigError(
            "OPENAI_API_KEY is not set; run with op run --env-file=.env.tpl --"
        )

    monkeypatch.setattr(embed, "OpenAIEmbedder", no_key)
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "OPENAI_API_KEY is not set" in result.output
    assert "Traceback" not in result.output


def test_index_with_an_unpriced_embedding_model_exits_1_before_touching_anything(
    monkeypatch, tmp_path
) -> None:
    """The real flow, not a stub: it refuses before it makes an engine, so no database is
    reached. The error names the model, and there is no traceback."""
    import elusion.ask.embed as embed

    class Unpriced:
        name = "stub"
        model = "no-such-embedding-model"

        def embed(self, texts):
            raise AssertionError("must not be called")

    monkeypatch.setattr(embed, "OpenAIEmbedder", Unpriced)
    crit = tmp_path / "auditable-decisions.md"
    crit.write_text("x")
    result = CliRunner().invoke(
        app,
        ["index", "--criterion", str(crit), "--prompt-version", "v1", "--review-model", "m"],
    )
    assert result.exit_code == 1, result.output
    assert "error:" in result.output and "no-such-embedding-model" in result.output
    assert "Traceback" not in result.output


def _stub_board(
    monkeypatch, tmp_path, *, held=frozenset(), criterion="auditable-decisions", gold_exists=True
):
    """Patch everything `board` touches, so no server starts and no database is reached."""
    from types import SimpleNamespace

    import elusion.board.audit as audit_mod
    import elusion.board.batch as batch
    import elusion.board.log as log
    import elusion.board.web as web
    import elusion.collect.db as db
    import elusion.evaluate.gold as gold_mod

    seen = {}

    class FakeApp:
        def run(self, **kw):
            seen["run"] = kw

    papers = [SimpleNamespace(doc_id=d) for d in ("a", "b", "c")]
    concept = SimpleNamespace(concept_version=2, detail={"criterion": criterion})
    readings = [SimpleNamespace(doc_id=d) for d in ("a", "c", "zz")]
    monkeypatch.setattr(db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(log, "ensure_schema", lambda engine: None)
    monkeypatch.setattr(log, "current_concept", lambda engine, name: concept)
    monkeypatch.setattr(log, "entries", lambda engine, name, version=None, kind=None: readings)
    monkeypatch.setattr(audit_mod, "reader_of", lambda engine, name, version: seen.update(r=1))
    monkeypatch.setattr(batch, "load_papers", lambda directory: papers)

    def fake_held(path):
        seen["gold"] = path
        return set(held)

    monkeypatch.setattr(batch, "held_back_ids", fake_held)
    # The gold CSV the board looks for exists unless a test says it does not.
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(exist_ok=True)
    if gold_exists:
        (gold_dir / f"{criterion}.csv").write_text("")
    monkeypatch.setattr(gold_mod, "gold_path", lambda c, data_dir: gold_dir / f"{c}.csv")

    def fake_create(engine, name, kept, held_back):
        seen.update(engine=engine, concept=name, papers=kept, held_back=held_back)
        return FakeApp()

    monkeypatch.setattr(web, "create_app", fake_create)
    return seen


def test_board_serves_the_papers_with_a_reading_and_holds_back_the_gold_set(
    monkeypatch, tmp_path
) -> None:
    seen = _stub_board(monkeypatch, tmp_path, held={"c"})
    result = CliRunner().invoke(app, ["board", "--concept", "stopping"])
    assert result.exit_code == 0, result.output
    assert "http://localhost:8010" in result.output
    assert "1 paper held back" in result.output
    assert seen["gold"].parts[-2:] == ("gold", "auditable-decisions.csv")
    assert (seen["concept"], seen["engine"]) == ("stopping", "engine")
    assert sorted(seen["papers"]) == ["a", "c"]  # "b" has no reading, "zz" is not a paper
    assert seen["held_back"] == {"c"}
    assert seen["run"] == {"host": "127.0.0.1", "port": 8010, "debug": False}


def test_board_release_gold_holds_nothing_back_and_takes_a_port(monkeypatch, tmp_path) -> None:
    seen = _stub_board(monkeypatch, tmp_path, held={"c"})
    result = CliRunner().invoke(
        app, ["board", "--concept", "stopping", "--port", "8123", "--release-gold"]
    )
    assert result.exit_code == 0, result.output
    assert "http://localhost:8123" in result.output
    assert "0 papers held back" in result.output
    assert "gold" not in seen
    assert seen["held_back"] == set()
    assert seen["run"]["port"] == 8123


def test_board_without_a_criterion_refuses_to_start(monkeypatch, tmp_path) -> None:
    seen = _stub_board(monkeypatch, tmp_path, held={"c"}, criterion=None)
    result = CliRunner().invoke(app, ["board", "--concept", "stopping"])
    assert result.exit_code == 1
    assert result.output.startswith("error: ") and "criterion" in result.output
    assert "run" not in seen


def test_board_refuses_to_start_without_a_gold_file(monkeypatch, tmp_path) -> None:
    seen = _stub_board(monkeypatch, tmp_path, held={"c"}, gold_exists=False)
    result = CliRunner().invoke(app, ["board", "--concept", "stopping"])
    assert result.exit_code == 1
    assert result.output.startswith("error: ") and "gold" in result.output
    assert "Traceback" not in result.output
    assert "run" not in seen


def test_board_release_gold_needs_no_criterion_and_no_gold_file(monkeypatch, tmp_path) -> None:
    seen = _stub_board(monkeypatch, tmp_path, held={"c"}, criterion=None, gold_exists=False)
    result = CliRunner().invoke(app, ["board", "--concept", "stopping", "--release-gold"])
    assert result.exit_code == 0, result.output
    assert seen["held_back"] == set()


def test_board_unknown_concept_and_unreadable_readings_exit_1(monkeypatch, tmp_path) -> None:
    import elusion.board.audit as audit_mod
    import elusion.board.log as log

    seen = _stub_board(monkeypatch, tmp_path)

    def unknown(engine, name):
        raise log.UnknownConcept(name)

    monkeypatch.setattr(log, "current_concept", unknown)
    result = CliRunner().invoke(app, ["board", "--concept", "nope"])
    assert result.exit_code == 1 and "nope" in result.output
    assert "Traceback" not in result.output

    seen = _stub_board(monkeypatch, tmp_path)

    def no_reader(engine, name, version):
        raise ValueError("no readings of stopping v2 yet")

    monkeypatch.setattr(audit_mod, "reader_of", no_reader)
    result = CliRunner().invoke(app, ["board", "--concept", "stopping"])
    assert result.exit_code == 1 and "no readings" in result.output
    assert "run" not in seen


def _stub_ask(monkeypatch, result, seen=None, embedder_error=None):
    """Stub everything `elusion ask` builds, and `ask` itself, so no database or model is used."""
    import elusion.ask.answer as answer_mod
    import elusion.ask.embed as embed
    import elusion.ask.log as ask_log
    import elusion.collect.db as collect_db
    import elusion.review.providers as providers

    seen = {} if seen is None else seen

    class StubProvider:
        def __init__(self, model):
            self.model = model

    class StubEmbedder:
        model = "stub-embedder"

        def __init__(self):
            if embedder_error is not None:
                raise embedder_error
            seen["embedder_built"] = True

    def fake_ask(engine, question, answerer, embedder, **kw):
        seen.update(question=question, answerer=answerer, embedder=embedder, **kw)
        return result

    monkeypatch.setattr(providers, "AnthropicProvider", StubProvider)
    monkeypatch.setattr(embed, "OpenAIEmbedder", StubEmbedder)
    monkeypatch.setattr(collect_db, "make_engine", lambda url: object())
    monkeypatch.setattr(ask_log, "ensure_schema", lambda engine: None)
    monkeypatch.setattr(answer_mod, "ask", fake_ask)
    return seen


def _ask_result(outcome, claims=(), checks=(), answerable=True, **kw):
    from elusion.ask.answer import Answer, AskResult, Claim, ClaimCheck

    answer = Answer(answerable=answerable, claims=[Claim(**c) for c in claims])
    return AskResult(
        ask_id=12,
        outcome=outcome,
        answer=answer,
        checks=[ClaimCheck(*c) for c in checks],
        retrieved=[],
        cost_usd=0.012345,
        **kw,
    )


CLAIMS = [
    {"claim": "It ends at a recall target.", "doc_id": "a", "quote": "a fixed recall"},
    {"claim": "It is logged.", "doc_id": "b", "quote": "every decision"},
]


def test_ask_prints_claims_and_checks(monkeypatch) -> None:
    result = _ask_result("answered", CLAIMS, [(0, "a", True, True), (1, "b", True, True)])
    seen = _stub_ask(monkeypatch, result)
    out = CliRunner().invoke(app, ["ask", "when does a review stop?"])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines() == [
        "ask 12: answered, arm hybrid, k 5, $0.0123",
        "1. It ends at a recall target.",
        '   [a] "a fixed recall"',
        "   check: ok",
        "2. It is logged.",
        '   [b] "every decision"',
        "   check: ok",
    ]
    assert seen["question"] == "when does a review stop?"
    assert seen["answerer"].model == "claude-sonnet-5-5"
    assert seen["arm"] == "hybrid" and seen["k"] == 5
    assert seen["embedder"] is not None

    CliRunner().invoke(
        app, ["ask", "q", "--arm", "vector", "--k", "3", "--model", "claude-opus-5-5"]
    )
    assert (seen["arm"], seen["k"], seen["answerer"].model) == ("vector", 3, "claude-opus-5-5")


def test_ask_refusal_text_is_exact(monkeypatch) -> None:
    result = _ask_result("refused", answerable=False)
    _stub_ask(monkeypatch, result)
    out = CliRunner().invoke(app, ["ask", "q"])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines() == [
        "ask 12: refused, arm hybrid, k 5, $0.0123",
        "The passages do not answer the question.",
    ]


def test_failed_ask_exits_1(monkeypatch) -> None:
    result = _ask_result("failed", CLAIMS, [(0, "a", True, False), (1, "zz", False, False)])
    _stub_ask(monkeypatch, result)
    out = CliRunner().invoke(app, ["ask", "q"])
    assert out.exit_code == 1
    assert out.output.splitlines() == [
        "ask 12: failed, arm hybrid, k 5, $0.0123",
        "1. It ends at a recall target.",
        '   [a] "a fixed recall"',
        "   check failed: quote not found in the abstract",
        "2. It is logged.",
        '   [b] "every decision"',
        "   check failed: not among the retrieved passages",
    ]
    assert "Traceback" not in out.output


def test_a_failed_ask_with_a_contradictory_answer_says_so(monkeypatch) -> None:
    result = _ask_result("failed", [], [])
    _stub_ask(monkeypatch, result)
    out = CliRunner().invoke(app, ["ask", "q"])
    assert out.exit_code == 1
    assert "contradict" in out.output


def test_an_error_ask_names_the_stop_reason_and_the_ask(monkeypatch) -> None:
    from elusion.ask.answer import AskResult

    result = AskResult(12, "error", None, [], [], 0.5, stop_reason="max_tokens")
    _stub_ask(monkeypatch, result)
    out = CliRunner().invoke(app, ["ask", "q"])
    assert out.exit_code == 1
    assert (
        "error: the model gave no usable answer (stop_reason=max_tokens); ask 12 holds the cost"
        in out.output
    )


def test_ask_lexical_builds_no_embedder(monkeypatch) -> None:
    seen = _stub_ask(monkeypatch, _ask_result("refused", answerable=False))
    out = CliRunner().invoke(app, ["ask", "q", "--arm", "lexical"])
    assert out.exit_code == 0, out.output
    assert seen["embedder"] is None and "embedder_built" not in seen
    assert seen["arm"] == "lexical"


def test_ask_user_errors_exit_1_without_traceback(monkeypatch) -> None:
    from elusion.review.providers import ProviderConfigError

    _stub_ask(
        monkeypatch,
        _ask_result("refused", answerable=False),
        embedder_error=ProviderConfigError("OPENAI_API_KEY is not set"),
    )
    out = CliRunner().invoke(app, ["ask", "q"])
    assert out.exit_code == 1
    assert "error: OPENAI_API_KEY is not set" in out.output
    assert "Traceback" not in out.output

    out = CliRunner().invoke(app, ["ask", "q", "--arm", "fuzzy"])
    assert out.exit_code == 1
    assert "error: unknown arm" in out.output
    assert "Traceback" not in out.output


def _stub_ask_draft(monkeypatch, tmp_path, flow, provider_error=None, schema_error=None):
    """Stub what `elusion ask-draft` builds (the provider, the engine, the schema) and the
    drafting itself, and point the data directory at `tmp_path`."""
    import dataclasses

    import elusion.ask.index as ask_index
    import elusion.ask.questions as questions
    import elusion.collect.db as collect_db
    import elusion.config as config
    import elusion.review.providers as providers

    class StubProvider:
        def __init__(self, model):
            if provider_error is not None:
                raise provider_error
            self.model = model

    def ensure_schema(engine):
        if schema_error is not None:
            raise schema_error

    monkeypatch.setattr(providers, "AnthropicProvider", StubProvider)
    monkeypatch.setattr(collect_db, "make_engine", lambda url: object())
    monkeypatch.setattr(ask_index, "ensure_schema", ensure_schema)
    monkeypatch.setattr(questions, "draft_questions", flow)
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, data_dir=tmp_path / "data")
    )


def _database_down():
    import sqlalchemy.exc

    return sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_ask_draft_drafts_into_the_criterions_draft_path_and_prints_the_summary(
    monkeypatch, tmp_path
) -> None:
    seen = {}

    def fake_flow(engine, drafter, path, **kw):
        seen.update(drafter=drafter, path=path, **kw)
        return {"drawn": 40, "already_drafted": 0, "drafted": 40, "failed": 0, "cost_usd": 0.5}

    _stub_ask_draft(monkeypatch, tmp_path, fake_flow)
    args = ["ask-draft", "--criterion", _crit(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert seen["drafter"].model == "claude-opus-5-5"
    assert seen["path"] == tmp_path / "data" / "gold" / "c.questions-draft.csv"
    assert seen["max_usd"] is None
    assert seen["held_back"] == set()  # no gold file yet
    assert "'drafted': 40" in result.output and "c.questions-draft.csv" in result.output

    result = CliRunner().invoke(app, [*args, "--max-usd", "2.5"])
    assert result.exit_code == 0, result.output
    assert seen["max_usd"] == 2.5

    missing = CliRunner().invoke(app, ["ask-draft", "--criterion", "nope.md"])
    assert missing.exit_code == 2 and "criterion file not found" in missing.output


def test_ask_draft_passes_the_held_back_ids_of_the_gold_files(monkeypatch, tmp_path) -> None:
    seen = {}

    def fake_flow(engine, drafter, path, **kw):
        seen.update(kw)
        return {}

    _stub_ask_draft(monkeypatch, tmp_path, fake_flow)
    gold = tmp_path / "data" / "gold"
    gold.mkdir(parents=True)
    (gold / "c.judge-refusals.jsonl").write_text('{"doc_id": "abc"}\n', encoding="utf-8")
    result = CliRunner().invoke(app, ["ask-draft", "--criterion", _crit(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen["held_back"] == {"abc"}


def test_ask_draft_help_says_what_a_pending_and_a_deleted_row_do() -> None:
    result = CliRunner().invoke(app, ["ask-draft", "--help"])
    assert "paid for, again" in " ".join(result.output.split())
    assert "Deleting a row does not redraw it" in " ".join(result.output.split())


def test_ask_draft_errors_exit_1_without_a_traceback(monkeypatch, tmp_path) -> None:
    import anthropic
    import httpx

    from elusion.flows.review import BudgetExceeded
    from elusion.review.providers import ProviderConfigError

    api_error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
    failures = [
        {"flow_error": BudgetExceeded("projected cost $9 is above the limit")},
        {"flow_error": ValueError("the index holds 3 abstracts and the draw needs 40")},
        {"flow_error": api_error},
        {"flow_error": PermissionError("cannot write the draft file")},
        {"provider_error": ProviderConfigError("no key")},
        {"schema_error": _database_down()},
    ]
    for failure in failures:
        error = failure.get("flow_error")

        def fake_flow(engine, drafter, path, error=error, **kw):
            raise error

        _stub_ask_draft(
            monkeypatch,
            tmp_path,
            fake_flow,
            provider_error=failure.get("provider_error"),
            schema_error=failure.get("schema_error"),
        )
        result = CliRunner().invoke(app, ["ask-draft", "--criterion", _crit(tmp_path)])
        raised = error or failure.get("provider_error") or failure.get("schema_error")
        assert result.exit_code == 1, result.output
        assert f"error: {raised}" in result.output
        assert "Traceback" not in result.output


def _stub_ask_questions(monkeypatch, tmp_path, engine, schema_error=None):
    import dataclasses

    import elusion.ask.index as ask_index
    import elusion.collect.db as collect_db
    import elusion.config as config

    def ensure_schema(_engine):
        if schema_error is not None:
            raise schema_error

    monkeypatch.setattr(collect_db, "make_engine", lambda url: engine)
    monkeypatch.setattr(ask_index, "ensure_schema", ensure_schema)
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, data_dir=tmp_path / "data")
    )


def test_ask_questions_writes_the_final_file_and_prints_the_counts(
    monkeypatch, tmp_path, index_engine
) -> None:
    from tests.test_ask_questions import fill_index, write_draft

    fill_index(index_engine, 41)
    _stub_ask_questions(monkeypatch, tmp_path, index_engine)
    write_draft(tmp_path / "data" / "gold" / "c.questions-draft.csv")
    args = ["ask-questions", "--criterion", _crit(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    final = tmp_path / "data" / "gold" / "c.questions.csv"
    assert final.is_file()
    assert "'answerable': 25" in result.output and "'unanswerable': 5" in result.output
    assert "c.questions.csv" in result.output

    again = CliRunner().invoke(app, args)  # never overwrites
    assert again.exit_code == 1
    assert "error:" in again.output and "exists" in again.output
    assert "Traceback" not in again.output


def test_ask_questions_refuses_a_held_back_source(monkeypatch, tmp_path, index_engine) -> None:
    from tests.test_ask_questions import doc_id, fill_index, write_draft

    fill_index(index_engine, 41)
    _stub_ask_questions(monkeypatch, tmp_path, index_engine)
    gold = tmp_path / "data" / "gold"
    write_draft(gold / "c.questions-draft.csv")
    (gold / "c.judge-refusals.jsonl").write_text(f'{{"doc_id": "{doc_id(2)}"}}\n', encoding="utf-8")
    result = CliRunner().invoke(app, ["ask-questions", "--criterion", _crit(tmp_path)])
    assert result.exit_code == 1
    assert "held back as a gold label" in result.output
    assert not (gold / "c.questions.csv").exists()


def test_ask_questions_errors_exit_1_without_a_traceback(
    monkeypatch, tmp_path, index_engine
) -> None:
    from tests.test_ask_questions import fill_index, write_draft

    fill_index(index_engine, 41)
    _stub_ask_questions(monkeypatch, tmp_path, index_engine)
    args = ["ask-questions", "--criterion", _crit(tmp_path)]
    nothing = CliRunner().invoke(app, args)  # no draft file yet
    assert nothing.exit_code == 1 and "error:" in nothing.output
    assert "Traceback" not in nothing.output

    write_draft(tmp_path / "data" / "gold" / "c.questions-draft.csv", struck=14)
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert (
        "error: the draft keeps 26 answerable and 5 unanswerable questions; it must keep 25 and 5"
        in result.output
    )
    assert not (tmp_path / "data" / "gold" / "c.questions.csv").exists()

    down = _database_down()
    _stub_ask_questions(monkeypatch, tmp_path, index_engine, schema_error=down)
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1 and f"error: {down}" in result.output
    assert "Traceback" not in result.output
