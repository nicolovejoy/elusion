"""The reviewer as a registrable MLflow model: provider, pinned model and prompt in one unit.

MLflow vocabulary, in plain words:

- A *pyfunc* model is MLflow's generic "anything with a predict()" wrapper. Ours holds no
  weights: it is a recipe (which provider, which pinned model id, which prompt file) that
  calls the provider when asked.
- The *registry* is a named list of model versions. Promoting a run registers its logged
  reviewer as the next version of `elusion-reviewer`.
- An *alias* is a movable label on one version. `champion` marks the reviewer we trust now;
  `elusion review --reviewer elusion-reviewer@champion` runs whatever it points at, so
  changing the champion needs no change to the command.

The artifacts are the manifest JSON and the review prompt file, nothing else: the gold set
and the grades never enter a model (hard rule). This module imports mlflow at the top, so callers
import it lazily to keep `elusion --help` fast.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.exceptions import MlflowException
from mlflow.pyfunc import PythonModel

from elusion.review.providers import make_provider

DEFAULT_NAME = "elusion-reviewer"
DEFAULT_ALIAS = "champion"
MODEL_PATH = "reviewer"
REGISTRY_URL = "http://localhost:5001/#/models"


@dataclass(frozen=True)
class Manifest:
    """What a reviewer is. `prompt_version` is the full pair key `<criterion>/<v>`."""

    provider: str
    model: str
    prompt_version: str
    criterion: str
    threshold: int
    generation_settings: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        return cls(**json.loads(text))


# MLflow 3.17 warns, as a class is defined, that predict() has no type hints, and its own
# "show once" filter makes the warning impossible to silence from outside. We leave the
# hints off on purpose: predict takes a list of strings or a frame, and a hint would make
# MLflow validate against only one of the two. This attribute is MLflow's own opt-out.
class ReviewerModel(PythonModel):
    """Scores paragraphs with the provider, model and prompt named in the manifest."""

    _skip_type_hint_validation = True

    provider = None  # tests inject a fake before predict; load_context builds the real one

    def load_context(self, context) -> None:
        self.manifest = Manifest.from_json(
            Path(context.artifacts["manifest"]).read_text(encoding="utf-8")
        )
        self.system = Path(context.artifacts["prompt"]).read_text(encoding="utf-8")
        self.provider = make_provider(self.manifest.provider, self.manifest.model)

    def predict(self, context, model_input, params=None) -> pd.DataFrame:
        texts = (
            model_input["text"].tolist()
            if isinstance(model_input, pd.DataFrame)
            else list(model_input)
        )
        rows = []
        for text in texts:
            r = self.provider.review(self.system, text)
            rows.append(
                {
                    "score": r.verdict.score,
                    "rationale": r.verdict.rationale,
                    "quote": r.verdict.quote,
                    "model": r.model,
                    "cost_usd": r.cost_usd,
                }
            )
        return pd.DataFrame(rows, columns=["score", "rationale", "quote", "model", "cost_usd"])


def log_reviewer(manifest: Manifest, prompt_path: Path) -> None:
    """Log the reviewer as a model of the active run. Only the manifest and prompt go in."""
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        mlflow.pyfunc.log_model(
            name=MODEL_PATH,
            python_model=ReviewerModel(),
            artifacts={"manifest": str(manifest_path), "prompt": str(prompt_path)},
            pip_requirements=["elusion"],
        )


def promote(
    run_id: str,
    name: str = DEFAULT_NAME,
    alias: str = DEFAULT_ALIAS,
    tracking_uri: str | None = None,
) -> int:
    """Register the run's reviewer as a new version of `name` and point `alias` at it."""
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_registry_uri(tracking_uri)
    client = mlflow.MlflowClient()
    # MLflow 3 stores a logged model apart from the run's artifacts, so `runs:/<id>/reviewer`
    # only works through a warned fallback. Find the logged model by its source run instead.
    experiment_id = client.get_run(run_id).info.experiment_id
    found = client.search_logged_models(
        experiment_ids=[experiment_id],
        filter_string=f"source_run_id = '{run_id}' AND name = '{MODEL_PATH}'",
        order_by=[{"field_name": "creation_time", "ascending": False}],
        max_results=1,
    )
    if not found:
        raise LookupError(f"run {run_id} has no logged {MODEL_PATH!r} model")
    version = mlflow.register_model(f"models:/{found[0].model_id}", name)
    client.set_registered_model_alias(name, alias, version.version)
    return int(version.version)


def resolve(reviewer: str, tracking_uri: str | None = None) -> Manifest:
    """Read the manifest of the version `<name>@<alias>` points at."""
    name, sep, alias = reviewer.partition("@")
    if not sep or not name or not alias:
        raise ValueError(f"--reviewer expects <name>@<alias>, got {reviewer!r}")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_registry_uri(tracking_uri)
    try:
        local = mlflow.artifacts.download_artifacts(artifact_uri=f"models:/{name}@{alias}")
    except MlflowException as exc:
        raise LookupError(f"no alias {alias!r} on registered model {name!r}") from exc
    return Manifest.from_json((Path(local) / "artifacts" / "manifest.json").read_text("utf-8"))
