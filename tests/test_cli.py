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

    missing = CliRunner().invoke(app, ["index", "--criterion", "nope.md", "--prompt-version", "v1"])
    assert missing.exit_code != 0


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
