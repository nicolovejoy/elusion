"""Registry round trip against a file:// MLflow store under tmp_path: nothing leaves the machine."""

from pathlib import Path

import mlflow
import pytest

from elusion.review.providers import Verdict
from elusion.review.registry import Manifest, ReviewerModel, log_reviewer, promote, resolve
from tests.fakes import FakeProvider

MANIFEST = Manifest("anthropic", "claude-x-1", "c/v2", "c", 2, {"effort": "low"})


@pytest.fixture
def uri(tmp_path):
    uri = f"file://{tmp_path}/mlruns"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    return uri


@pytest.fixture
def prompt(tmp_path):
    p = tmp_path / "v2.md"
    p.write_text("system prompt")
    return p


def _log(uri, prompt) -> str:
    mlflow.set_experiment("t")
    with mlflow.start_run() as run:
        log_reviewer(MANIFEST, prompt)
        return run.info.run_id


def test_manifest_round_trip() -> None:
    assert Manifest.from_json(MANIFEST.to_json()) == MANIFEST


def test_log_promote_resolve_round_trip(uri, prompt) -> None:
    run_id = _log(uri, prompt)
    assert promote(run_id, tracking_uri=uri) == 1
    assert resolve("elusion-reviewer@champion", tracking_uri=uri) == MANIFEST
    # Promoting the same run again makes version 2 and moves the alias to it.
    assert promote(run_id, tracking_uri=uri) == 2
    client = mlflow.MlflowClient(tracking_uri=uri, registry_uri=uri)
    assert client.get_model_version_by_alias("elusion-reviewer", "champion").version == 2


def test_resolve_missing_alias_raises_lookup_error(uri, prompt) -> None:
    promote(_log(uri, prompt), tracking_uri=uri)
    with pytest.raises(LookupError, match="no alias 'challenger' on registered model"):
        resolve("elusion-reviewer@challenger", tracking_uri=uri)


def test_predict_uses_injected_provider_and_returns_frame() -> None:
    model = ReviewerModel()
    model.system = "sys"
    model.provider = FakeProvider(verdict=Verdict(score=4, rationale="r", quote="q"))
    out = model.predict(None, ["one", "two"])
    assert list(out.columns) == ["score", "rationale", "quote", "model", "cost_usd"]
    assert out["score"].tolist() == [4, 4] and out["model"].tolist() == ["fake-model"] * 2
    assert [c[1] for c in model.provider.calls] == ["one", "two"]


def test_logged_model_artifacts_contain_no_gold_or_grades(uri, prompt) -> None:
    run_id = _log(uri, prompt)
    version = promote(run_id, tracking_uri=uri)
    client = mlflow.MlflowClient(tracking_uri=uri, registry_uri=uri)
    source = client.get_model_version("elusion-reviewer", str(version)).source
    local = Path(mlflow.artifacts.download_artifacts(artifact_uri=source))
    names = sorted(str(p.relative_to(local)) for p in local.rglob("*") if p.is_file())
    assert "artifacts/manifest.json" in names and "artifacts/v2.md" in names
    assert not [n for n in names if "gold" in n or "grades" in n]
