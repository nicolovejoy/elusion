# Phase 4 Leaderboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five validated review runs compared in MLflow, one promoted in the registry as `elusion-reviewer@champion`, each with a rationale-quality score from a grader model.

**Architecture:** The evaluate flow stays the single place where numbers exist; it gains an MLflow logging step (new module `evaluate/tracking.py`) and a pyfunc "reviewer" model logged to each run (new module `review/registry.py`). Two new providers (`review/openai_provider.py`) sit behind the existing `Provider`/`Judge` protocols. Rationale grading is a new flow (`flows/grade.py`) writing its own CSV (`evaluate/grades.py`), read by evaluate when present.

**Tech Stack:** Python 3.12, Prefect 3.9, MLflow 3.17.2 (client and server), Anthropic SDK 2.4, OpenAI SDK (to be added), pydantic, Typer, pytest.

**Spec:** `docs/plans/phase-4-leaderboard.md`. Read it first; the six numbered decisions there are settled.

## Global Constraints

- Every LLM call carries a pinned model snapshot id and a prompt version string, and both are stored with the result (CLAUDE.md hard rule).
- Idempotent by document id: no re-spend for a (document, model, prompt version) that already has a score.
- The gold set (`data/gold/`) and the grades file (`data/grades/`) never enter a prompt, a few-shot example, a retrieval context, or an MLflow artifact.
- Spend guard: review, judge and grade refuse runs projected above `ELUSION_MAX_RUN_USD` (default 10).
- No new top-level dependency except moving `openai` from optional extra to dependency. No `ollama` package.
- No test makes a network call. Providers and clients are faked.
- The judge model (`settings.judge_model`) is never used as a reviewer.
- Each task: `uv run ruff check . && uv run ruff format --check . && uv run pytest` green before commit.
- Prices in `pricing.py` are looked up at implementation time, never recalled, with a `DECISIONS.md` line naming the source and date.
- Owner steps (spend money, need `.env`, install software) are handed to the owner as a copy-paste command and waited on; the implementer does not run them.
- Explain-as-you-go: each task's hand-back includes two to four plain sentences on any new tool or concept (what an MLflow run is, what an alias is, why a pyfunc), for the phase note.

## Branch

All work happens on `phase-4-leaderboard`, branched from `main` after the spec commit. Each task is one commit on it; Task 12 opens the PR.

## Review Focus

1. MLflow server unreachable when `evaluate` runs: the report must still be written to disk and the error must name the report path. Test in Task 2.
2. A pinned OpenAI id that is an alias, not a dated snapshot: the API echoes the dated id, the flow must stop before storing anything and point at `--model`. Test in Task 4.
3. A local Ollama model that ignores the schema and returns prose: must be a counted `ReviewError("invalid_json")`, not a crash. Test in Task 6.
4. `--reviewer elusion-reviewer@champion` when no such alias exists: a clear error before any token is spent. Test in Task 11.
5. The seeded discard draw selects a doc the judge never labeled (scores changed after judging): evaluate refuses and names the count, as it does for flagged docs. Test in Task 1.

---

### Task 1: Explicit review model and exact discard sample

**Files:**
- Modify: `src/elusion/flows/evaluate.py` (judge and evaluate flows)
- Modify: `src/elusion/evaluate/report.py` (`RunReport`, `render`)
- Modify: `src/elusion/cli.py` (judge, evaluate commands)
- Test: `tests/test_evaluate_flow.py`, `tests/test_judge_flow.py`, `tests/test_cli.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: `split_piles(scores, threshold, discard_n, seed) -> list[tuple[str, Pile]]` from `evaluate/gold.py` (already exists and is deterministic).
- Produces: `judge(..., review_model: str | None = None)`; `evaluate(..., review_model: str | None = None, discard_n: int = 200)`; `RunReport.discard_n: int`; new exception `MissingDiscardLabels(MissingLabels)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_evaluate_flow.py`, extend the `env` fixture's CSV with a second judge-labeled flagged doc that is *not* in the seeded draw for the population, then:

```python
def test_discard_sample_is_the_seeded_draw_not_every_labeled_doc(env):
    # Add a label for g15 (score 0) that the seeded draw of discard_n=10 does not pick.
    # Precondition computed in the test: split_piles(SCORES, 3, 10, DEFAULT_SEED) excludes g15.
    tmp_path, seen = env
    append_rows(gold_path("c", tmp_path), [row("g15", 1, pile="discard")])
    text = run(tmp_path, discard_n=10).read_text()
    assert "elusion: 0.100 [0.003, 0.445] (k/n = 1/10)" in text  # g15 not counted
    assert "drawn with seed 20261104 from the pair's discard pile" in text

def test_missing_discard_label_refuses_and_counts(env):
    tmp_path, _ = env
    with pytest.raises(MissingDiscardLabels, match="2 sampled discard paragraphs have no fake-judge label"):
        run(tmp_path, discard_n=12)   # draw of 12 reaches two unlabeled g-docs

def test_review_model_option_selects_the_pair(env):
    tmp_path, seen = env
    run(tmp_path, review_model="other-model")
    assert seen["scores_for"][1] == "other-model"
```

The exact elusion string: compute k/n from the fixture's labels inside the drawn set and use `metrics.clopper_pearson` in the test to format the expected interval rather than hard-coding digits.

In `tests/test_judge_flow.py`: `test_review_model_option_selects_the_pair` asserting the `select_gold` stub was called with `"other-model"`.

In `tests/test_cli.py`: `judge` and `evaluate` pass `--review-model x` through; `evaluate --discard-n 50` passes through.

In `tests/test_report.py`: the rendered population section contains `- discard sample: 200 drawn with seed 20261104 from the pair's discard pile; 200 labeled by the judge`.

- [ ] **Step 2: Run the tests, confirm they fail**

Run: `uv run pytest tests/test_evaluate_flow.py tests/test_judge_flow.py tests/test_cli.py tests/test_report.py -q`
Expected: FAIL on unknown keyword `review_model` / `discard_n` and missing report line.

- [ ] **Step 3: Implement**

- `judge(...)`: add `review_model: str | None = None`; `review_model = review_model or settings.review_model`; use it everywhere `settings.review_model` was used.
- `evaluate(...)`: add `discard_n: int = 200`. Replace the `discard_rows` computation with: `draw = {d for d, pile in split_piles(scores, threshold, discard_n, seed) if pile == "discard"}`; `missing_d = draw - judged.keys()`; raise `MissingDiscardLabels(f"{len(missing_d)} sampled discard paragraphs have no {judge_model} label in {csv}; re-run `elusion judge` with the same seed")` if non-empty; `discard_rows = [judged[d] for d in sorted(draw)]`.
- `RunReport` gains `discard_n: int`; `render` replaces the "discard sample labeled by the judge" line with the line in Step 1.
- CLI: `--review-model` (default None, help "Pinned review model of the pair; default ELUSION_REVIEW_MODEL") on `judge` and `evaluate`; `--discard-n` on `evaluate` (default 200). Both exceptions exit 1 without traceback.

- [ ] **Step 4: Run the full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: all pass.

- [ ] **Step 5: Regenerate the v2 report and diff it**

Run (no tokens, needs the compose Postgres):
`uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 && git diff --stat docs/reports/`
Expected: only the `date` line and the new discard-sample line change. If any k/n changes, stop and report.

- [ ] **Step 6: Commit**

```bash
git add src tests docs/reports
git commit -m "Phase 4 task 1: --review-model on judge/evaluate; discard sample is the seeded draw"
```

---

### Task 2: MLflow logging from evaluate

**Files:**
- Modify: `src/elusion/config.py`
- Create: `src/elusion/evaluate/tracking.py`
- Modify: `src/elusion/flows/evaluate.py`, `src/elusion/cli.py`
- Modify: `src/elusion/review/providers.py` (add `GENERATION_SETTINGS`)
- Modify: `src/elusion/review/db.py` (add `provider_for`)
- Test: `tests/test_tracking.py`, `tests/test_evaluate_flow.py`, `tests/test_review_db.py`

**Interfaces:**
- Consumes: `RunReport` (Task 1), `Estimate`.
- Produces:
  - `settings.mlflow_tracking_uri: str` (env `MLFLOW_TRACKING_URI`, default `http://localhost:5001`).
  - `providers.GENERATION_SETTINGS: dict[str, dict] = {"anthropic": {"effort": "low"}}` (later tasks add `openai`, `ollama`).
  - `db.provider_for(engine, model, prompt_version) -> str | None` (distinct `scores.provider` for the pair; None if no rows).
  - `tracking.experiment_name(criterion) -> str` = `f"elusion/{criterion}"`.
  - `tracking.run_params(report: RunReport, provider: str) -> dict[str, str]` and `tracking.run_metrics(report: RunReport) -> dict[str, float]` (pure).
  - `tracking.log_run(report: RunReport, provider: str, artifacts: list[Path], tracking_uri: str) -> str` returning the MLflow run id.
  - `evaluate(...)` now returns `EvalResult(report_path: Path, run_id: str | None)` (dataclass in `flows/evaluate.py`); `run_id` is None only when `log_mlflow=False` (new flow parameter, default True; tests pass False unless testing logging).

- [ ] **Step 1: Write the failing tests**

`tests/test_tracking.py` (build a `RunReport` fixture with fixed values; use `mlflow.set_tracking_uri(f"file://{tmp_path}/mlruns")` so nothing leaves the machine):

```python
def test_run_metrics_has_point_low_high_k_n_for_each_estimate(report):
    m = run_metrics(report)
    for name in ("precision", "elusion", "richness", "recall", "agreement"):
        assert {f"{name}_point", f"{name}_low", f"{name}_high", f"{name}_k", f"{name}_n"} <= m.keys()
    assert m["total_cost_usd"] == pytest.approx(report.review_cost_usd + report.judge_cost_usd)
    assert m["cost_per_1000_docs_usd"] == pytest.approx(report.review_cost_usd / report.population * 1000)
    assert m["review_wall_clock_s"] == report.review_wall_clock.total_seconds()

def test_run_params_names_the_pair_and_settings(report):
    p = run_params(report, "anthropic")
    assert p["review_model"] == report.review_model and p["provider"] == "anthropic"
    assert p["generation_settings"] == '{"effort": "low"}'
    assert p["threshold"] == "3" and p["discard_n"] == "200"

def test_log_run_writes_params_metrics_and_only_safe_artifacts(report, tmp_path):
    uri = f"file://{tmp_path}/mlruns"
    prompt = tmp_path / "v2.md"; prompt.write_text("prompt")
    gold = tmp_path / "gold.csv"; gold.write_text("doc_id,label\n")
    run_id = log_run(report, "anthropic", [prompt], uri)
    client = mlflow.MlflowClient(tracking_uri=uri)
    run = client.get_run(run_id)
    assert run.data.params["prompt_version"] == report.prompt_version
    assert run.data.metrics["precision_point"] == pytest.approx(report.precision.point)
    assert run.data.tags["elusion.pair"] == f"{report.review_model}|{report.prompt_version}"
    names = {a.path for a in client.list_artifacts(run_id)}
    assert "v2.md" in names and "gold.csv" not in names
    exp = client.get_experiment(run.info.experiment_id)
    assert exp.name == "elusion/c"

def test_agreement_none_logs_no_agreement_metrics(report_without_agreement):
    assert not any(k.startswith("agreement_") for k in run_metrics(report_without_agreement))
```

`tests/test_evaluate_flow.py`: `test_evaluate_logs_one_run_and_returns_its_id` (tracking uri to tmp; `run(..., log_mlflow=True)`; `result.run_id` is a 32-hex string; the report file and the prompt file are artifacts) and `test_report_is_written_even_when_logging_fails` (monkeypatch `mod.log_run` to raise `RuntimeError("down")`; `pytest.raises(RuntimeError, match="report written to .*c-fake-model-v2.md")`; the file exists).

`tests/test_review_db.py`: `provider_for` returns the provider of inserted rows and None when empty (use the existing sqlite/engine fixture pattern in that file).

- [ ] **Step 2: Run, confirm failure**

Run: `uv run pytest tests/test_tracking.py tests/test_evaluate_flow.py tests/test_review_db.py -q`
Expected: ImportError on `elusion.evaluate.tracking`.

- [ ] **Step 3: Implement**

- `tracking.py`: module docstring explaining experiment, run, params, metrics, artifacts in plain words. `import mlflow` inside `log_run` (keep `elusion --help` fast; `tests/test_cli.py::test_cli_import_is_lazy` must still pass). `log_run`: `mlflow.set_tracking_uri(uri)`, `mlflow.set_experiment(experiment_name(report.criterion))`, `with mlflow.start_run(run_name=f"{report.review_model}/{report.prompt_version}") as run:` log params, metrics, `mlflow.log_artifact(p)` per artifact, tags `{"phase": "4", "elusion.pair": ...}`; return `run.info.run_id`. Metrics for a derived `Estimate` log `k` as -1. `generation_settings` param is `json.dumps(GENERATION_SETTINGS.get(provider, {}), sort_keys=True)`. Metric names: `<metric>_{point,low,high,k,n}`, `population`, `n_flagged`, `n_discard`, `review_cost_usd`, `judge_cost_usd`, `total_cost_usd`, `cost_per_1000_docs_usd`, `review_wall_clock_s` (omit when None), `docs_per_minute` (omit when wall-clock None or 0).
- `evaluate`: after writing the report, if `log_mlflow`: `provider = provider_for(engine, review_model, version) or "unknown"`, artifacts = `[path, PROMPTS_DIR / criterion / f"{prompt_version}.md", Path("criteria") / f"{criterion}.md"]` filtered to existing files; wrap `log_run` in `try/except Exception as exc: raise RuntimeError(f"report written to {path}, but MLflow logging failed: {exc}") from exc`. Return `EvalResult(path, run_id)`.
- CLI `evaluate` prints the report path and, when present, `mlflow run <id>` plus `http://localhost:5001/#/experiments` (the bare tracking URI from settings).
- `docs/ARCHITECTURE.md`: in the phase 3 diagram, the `──▶ MLflow artifact (phase 4)` arrow becomes "(built)"; the "Where each phase reads and writes" row for 4 reads "report, scores, prompt file / MLflow run (params, metrics, report, prompt)".

- [ ] **Step 4: Run the full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
git add src tests docs/ARCHITECTURE.md
git commit -m "Phase 4 task 2: evaluate logs one MLflow run (params, metrics, report, prompt)"
```

---

### Task 3: Owner: first two MLflow runs

- [ ] **Step 1: Hand the owner these commands and wait**

```
docker compose up -d
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v1
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v1
```

Pass/fail for the owner: open http://localhost:5001, experiment `elusion/cloud-concentration` shows two runs named `claude-haiku-5-5/cloud-concentration/v2` and `.../v1`, each with a `precision_point` column and a report artifact. The judge step costs about $1 (v1's flagged docs plus its own 200-discard draw, minus docs already labeled).

- [ ] **Step 2: Commit the v1 report**

```bash
git add docs/reports/cloud-concentration-claude-haiku-5-5-v1.md
git commit -m "Phase 4 task 3: v1 report, evaluated against the tightened criterion"
```

---

### Task 4: OpenAI provider and provider selection

**Files:**
- Create: `src/elusion/review/openai_provider.py`
- Modify: `src/elusion/review/providers.py` (`GENERATION_SETTINGS["openai"] = {}`, `make_provider`)
- Modify: `src/elusion/review/pricing.py`, `src/elusion/config.py`, `src/elusion/cli.py`, `src/elusion/flows/review.py`, `pyproject.toml`, `docs/DECISIONS.md`, `.env.example`
- Test: `tests/test_openai_provider.py`, `tests/test_cli.py`, `tests/test_pricing.py`

**Interfaces:**
- Produces:
  - `OpenAIProvider(model: str, client=None, api_key: str | None = None, base_url: str | None = None, temperature: float | None = None)`, `name = "openai"`, methods `review(system, paragraph) -> Review`, `label(system, paragraph) -> Judgement`, attribute `generation_settings: dict`.
  - `providers.make_provider(provider: str, model: str) -> Provider` with `provider in {"anthropic", "openai", "ollama"}`; imports `openai_provider` lazily; raises `ValueError` for anything else.
  - `review(..., provider_name: str = "anthropic", model: str | None = None)` flow parameters (the existing `provider: Provider | None` injection stays for tests and wins when given).
  - `settings.openai_model: str` from `ELUSION_OPENAI_MODEL` with the looked-up dated snapshot as default.

- [ ] **Step 1: Look up, do not recall**

Read the installed OpenAI SDK's Responses API for structured outputs (`client.responses.create(model=..., instructions=system, input=paragraph, max_output_tokens=MAX_TOKENS, text={"format": {"type": "json_schema", "name": "verdict", "schema": ..., "strict": True}})`), confirm the field names below against it, and look up the current small model's dated snapshot id and its input, cached-input and output prices. Put the id in `settings.openai_model` and the prices in `pricing.PRICES` with a `DECISIONS.md` line: `2026-MM-DD — OpenAI review model is <id> at $X/$Y/$Z per M tokens (input/cached input/output), source <url> — <why this tier>`.

- [ ] **Step 2: Write the failing tests**

`tests/test_openai_provider.py` with a `FakeResponses` whose `create(**kw)` records `kw` and returns a `types.SimpleNamespace` shaped like the SDK response (`status`, `incomplete_details`, `model`, `output_text`, `output` (list of items with `type` in {"message", "refusal"} as the SDK nests them; confirm in Step 1), `usage` with `input_tokens`, `output_tokens`, `input_tokens_details.cached_tokens`):

```python
def test_review_request_shape_and_cost(): # strict json_schema format, instructions == system, input == paragraph, max_output_tokens == 1024, no temperature key when None; cost == cost_usd(model, input - cached, output, 0, cached)
def test_cached_tokens_map_to_cache_read_bucket(): # Review.cache_read_input_tokens == cached, input_tokens == prompt - cached
def test_incomplete_max_output_tokens_raises_review_error_with_usage(): # stop_reason == "max_output_tokens"
def test_refusal_raises_review_error(): # stop_reason == "refusal"
def test_prose_answer_is_invalid_json(): # output_text "I think 3" -> ReviewError("invalid_json")
def test_label_returns_judgement(): # Label schema used, relevant bool parsed
def test_temperature_is_sent_only_when_set(): # OpenAIProvider(..., temperature=0.0) sends temperature=0.0; generation_settings == {"temperature": 0.0}
def test_make_provider_dispatches_and_rejects_unknown(): # "anthropic" -> AnthropicProvider, "openai" -> OpenAIProvider (monkeypatch api key), "nope" -> ValueError
```

`tests/test_review_flow.py`: `test_alias_model_echo_mismatch_stops_before_storing` — a fake provider whose `model` is `"gpt-x-mini"` but whose Review.model is `"gpt-x-mini-2026-01-01"`; the flow raises `RuntimeError` matching `pin the served snapshot id` and the fake db has zero score rows (this is Review Focus 2; the echo check exists, the test pins it for the new provider path, with `env_var` text now reading `--model / ELUSION_REVIEW_MODEL`).

`tests/test_cli.py`: `review --provider openai --model m` passes `provider_name="openai", model="m"`; default is `provider_name="anthropic", model=None`.

`tests/test_pricing.py`: the OpenAI row exists and `cost_usd` works with cache_write 0.

- [ ] **Step 3: Run, confirm failure**

Run: `uv run pytest tests/test_openai_provider.py tests/test_cli.py tests/test_pricing.py tests/test_review_flow.py -q`
Expected: ImportError / unknown option.

- [ ] **Step 4: Implement**

- `uv add openai` (moves it to `[project.dependencies]`; delete the `openai` optional extra). `DECISIONS.md` line already written by the spec; append the SDK version.
- `openai_provider.py`: docstring explaining the Responses API, strict JSON schema, automatic prompt caching and the `cached_tokens` mapping in plain words. One `_call(system, user, out, schema)` mirroring the Anthropic one: `status != "completed"` -> `ReviewError(incomplete_details.reason or status, ...)`; a refusal item -> `ReviewError("refusal")`; `out.model_validate(json.loads(resp.output_text))` else `invalid_json`. Usage: `cached = usage.input_tokens_details.cached_tokens or 0`; `input_tokens = usage.input_tokens - cached`; `cache_read_input_tokens = cached`; `cache_creation_input_tokens = 0`. Schema: `transform_schema` is Anthropic-specific; build the strict schema with `Verdict.model_json_schema()` plus `additionalProperties: False` (pydantic emits it for `extra="forbid"`; assert in a test).
- `providers.py`: `GENERATION_SETTINGS["openai"] = {}`; `make_provider`.
- `flows/review.py`: `provider = provider or make_provider(provider_name, model or settings.review_model)` (for `openai` default model is `settings.openai_model`; put that rule in `make_provider` when `model` is None). `check_model_echo`'s message: `pin the served snapshot id (--model or {env_var})`.
- `cli.py`: `--provider` (default `anthropic`), `--model` (default None).
- `.env.example`: `ELUSION_OPENAI_MODEL=<id>   # pinned OpenAI review model (phase 4)`.

- [ ] **Step 5: Full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
git add -A src tests pyproject.toml uv.lock docs/DECISIONS.md .env.example
git commit -m "Phase 4 task 4: OpenAI provider behind the same protocol; review --provider/--model"
```

---

### Task 5: Owner: OpenAI run

- [ ] **Step 1: Hand over and wait** (replace the model id with the one pinned in Task 4; the owner adds `OPENAI_API_KEY` to `.env` via 1Password first)

```
uv run elusion review --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000 --provider openai --model <pinned snapshot>
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
```

Pass: the review log shows `projected cost $… limit $10.00` and finishes; MLflow shows a third run. Record cost, wall-clock and the score distribution for the phase note.

- [ ] **Step 2: Commit the report**

```bash
git add docs/reports/
git commit -m "Phase 4 task 5: OpenAI v2 report"
```

---

### Task 6: Ollama provider

**Files:**
- Modify: `src/elusion/review/openai_provider.py` (add `OllamaProvider`), `src/elusion/review/providers.py` (`GENERATION_SETTINGS["ollama"] = {"temperature": 0.0}`, `make_provider`), `src/elusion/review/pricing.py` (`register_free`), `src/elusion/config.py` (`ollama_host`, `ollama_model`), `src/elusion/cli.py` (concurrency default note), `.env.example`
- Test: `tests/test_openai_provider.py`, `tests/test_pricing.py`

**Interfaces:**
- Produces: `OllamaProvider(model: str, host: str | None = None, client=None, temperature: float = 0.0)`, `name = "ollama"`, subclass of `OpenAIProvider` with `base_url = f"{host}/v1"`, `api_key = "ollama"`; calls `pricing.register_free(model)` in `__init__`. `pricing.register_free(model: str) -> None` adds an all-zero row. `settings.ollama_host` (env `OLLAMA_HOST`, default `http://localhost:11434`), `settings.ollama_model` (env `ELUSION_OLLAMA_MODEL`, default the model chosen in Step 1).

- [ ] **Step 1: Owner installs Ollama; implementer proposes the model**

The implementer checks the Ollama library for a current 8B to 14B instruct model that supports structured output and the OpenAI-compatible `/v1` endpoint, and proposes one `name:tag`. Owner runs:

```
brew install ollama
brew services start ollama
ollama pull <name:tag>
ollama show <name:tag>
```

The owner pastes the digest line back; it goes into `DECISIONS.md`: `2026-MM-DD — Ollama review model is <name:tag>, digest <sha>, run on the dev Mac — <why this model>`.

- [ ] **Step 2: Write the failing tests**

```python
def test_ollama_uses_compat_endpoint_and_zero_cost(): # client constructed with base_url host + "/v1"; Review.cost_usd == 0.0; cost_usd(model, 10, 10) == 0.0 after construction
def test_ollama_sends_temperature_zero_by_default(): # kw["temperature"] == 0.0; generation_settings == {"temperature": 0.0}
def test_ollama_prose_answer_is_counted_invalid_json(): # Review Focus 3: output_text "Score: 3 because…" -> ReviewError("invalid_json") with usage, no crash
def test_ollama_echo_name_tag(): # resp.model "m:8b" == provider.model passes; "m:latest" mismatch is left to the flow's echo check (assert Review.model carries the served name)
def test_register_free_adds_zero_row(): # pricing
def test_make_provider_ollama_defaults_to_settings_model(monkeypatch)
```

- [ ] **Step 3: Run, confirm failure; implement; run, pass**

Implementation notes: Ollama's OpenAI-compatible endpoint accepts `response_format` on chat completions and may not accept the Responses API; if `client.responses.create` is rejected by Ollama, `OllamaProvider` overrides `_call` to use `client.chat.completions.create(model, messages=[{"role": "system", ...}, {"role": "user", ...}], response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": True}}, temperature=...)` and maps `finish_reason == "length"` to `ReviewError("length")`. The implementer verifies against the running Ollama with a one-paragraph `uv run python -c` probe (free, local) and records which path was needed in the module docstring. If neither honors the schema, fall back to native `/api/chat` with `format=<schema>` over `httpx` (spec decision 5).

- [ ] **Step 4: Full suite, commit**

```bash
git add src tests docs/DECISIONS.md .env.example
git commit -m "Phase 4 task 6: Ollama provider through the OpenAI-compatible endpoint; free pricing row"
```

---

### Task 7: Owner: Ollama run

- [ ] **Step 1: Hand over and wait**

```
uv run elusion review --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000 --provider ollama --model <name:tag> --concurrency 2
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <name:tag>
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <name:tag>
```

If 20,000 is too slow for one sitting, stop with Ctrl-C and re-run later; the consumer group resumes. Record docs per minute and the final population.

- [ ] **Step 2: Commit the report**

```bash
git add docs/reports/
git commit -m "Phase 4 task 7: Ollama v2 report"
```

---

### Task 8: Prompt v3 and its run

**Files:**
- Create: `src/elusion/review/prompts/cloud-concentration/v3.md`
- Modify: `criteria/cloud-concentration.md` (only if a line moves), `docs/DECISIONS.md`
- Test: `tests/test_prompt.py`

- [ ] **Step 1: Failing test**: `load_prompt("cloud-concentration", "v3").version == "cloud-concentration/v3"` and the text contains each of the four gap phrases from `docs/notes/phase-3.md`: "SaaS", "ISP", "outsourc", "on-site".
- [ ] **Step 2: Write v3** from v2: same 0-4 scale and JSON fields; add one explicit rule per gap: third-party SaaS applications count only when the paragraph says the company depends on one or a few named or described providers to operate; ISPs count only when they also host or colocate; generic IT outsourcing to unnamed third parties does not count; a mixed on-site plus cloud setup counts only when the paragraph says the cloud part is a dependency on one or a few providers. v1 and v2 untouched. `DECISIONS.md`: one line on the v3 rules and why.
- [ ] **Step 3: Suite green, commit**: `git commit -m "Phase 4 task 8: prompt v3 closes the phase 3 gap list"`.
- [ ] **Step 4: Owner runs** (about $1.30 plus judge delta):

```
uv run elusion review --criterion criteria/cloud-concentration.md --prompt-version v3 --sample 20000
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v3
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v3
```

- [ ] **Step 5: Commit the report**: `git add docs/reports/ && git commit -m "Phase 4 task 8: haiku v3 report"`.

---

### Task 9: Grades store, grader prompt, grader call

**Files:**
- Create: `src/elusion/review/prompts/cloud-concentration/grade-v1.md`, `src/elusion/evaluate/grades.py`
- Modify: `src/elusion/review/providers.py` (`Grade`, `GRADE_SCHEMA`, `Grading`, `Grader` protocol, `AnthropicProvider.grade`, `grader_message`), `src/elusion/review/db.py` (`verdicts_for`), `tests/fakes.py` (`FakeGrader`)
- Test: `tests/test_grades.py`, `tests/test_providers.py`, `tests/test_review_db.py`, `tests/test_prompt.py`

**Interfaces:**
- Produces:
  - `Grade(BaseModel)`: `sound: bool`, `rationale: str` (extra forbid). `Grading` dataclass like `Judgement` with `grade: Grade`.
  - `Grader(Protocol)`: `name`, `model`, `grade(system: str, paragraph: str, verdict: Verdict) -> Grading`.
  - `providers.grader_message(paragraph: str, verdict: Verdict) -> str` = `f"PARAGRAPH:\n{paragraph}\n\nREVIEWER SCORE: {score}\nREVIEWER RATIONALE: {rationale}\nREVIEWER QUOTE: {quote}"`.
  - `db.verdicts_for(engine, model, prompt_version, min_score: int) -> dict[str, Verdict]`.
  - `grades.GradeRow` dataclass: `doc_id, review_model, prompt_version, score, grader, grader_prompt_version, sound (int 0/1), quote_verbatim (int 0/1), rationale, graded_at`; `grades_path(criterion, data_dir) -> data_dir / "grades" / f"{criterion}.csv"`; `read_rows`, `append_rows(path, rows) -> int` (dedupe on `(doc_id, review_model, prompt_version, grader)`), `graded_ids(path, review_model, prompt_version, grader) -> set[str]`, `rows_for(path, review_model, prompt_version, grader) -> list[GradeRow]`, `quote_is_verbatim(quote: str, paragraph: str) -> bool` (`quote == ""` counts as verbatim-missing: returns False; otherwise `quote in paragraph`).
  - `grade_runs_path` + `append_grade_run` + `grade_cost_usd`, same shape as the judge-runs ledger.

- [ ] **Step 1: Failing tests** (`tests/test_grades.py` mirrors `tests/test_gold.py`: header, dedupe key, torn-line append, `graded_ids`, `quote_is_verbatim` for exact, missing and paraphrased quotes). `tests/test_providers.py`: `test_grade_request_sends_paragraph_and_verdict_not_label` (the user content equals `grader_message(...)`, the schema is `GRADE_SCHEMA`), `test_grade_unusable_answers_raise_review_error`. `tests/test_review_db.py`: `verdicts_for` returns only rows with `score >= min_score` as `Verdict`s. `tests/test_prompt.py`: `grade-v1` loads with version `cloud-concentration/grade-v1`.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**. `grade-v1.md`: states the criterion in full (copy the v2 criterion block), then: "You are shown a paragraph and one reviewer's score, rationale and quote. Say whether the rationale is a faithful reading of this paragraph under the criterion: it cites what the paragraph says, does not invent a provider, dependence or risk the paragraph lacks, and applies the criterion's exclusions. Answer sound true or false with one sentence why. Do not re-score the paragraph." The grader never receives a label.
- [ ] **Step 4: Suite green, commit**: `git commit -m "Phase 4 task 9: grades store, grader prompt and call"`.

---

### Task 10: Grade flow, CLI, report section, MLflow metrics

**Files:**
- Create: `src/elusion/flows/grade.py`
- Modify: `src/elusion/flows/evaluate.py`, `src/elusion/evaluate/metrics.py`, `src/elusion/evaluate/report.py`, `src/elusion/evaluate/tracking.py`, `src/elusion/cli.py`
- Test: `tests/test_grade_flow.py`, `tests/test_metrics.py`, `tests/test_report.py`, `tests/test_tracking.py`, `tests/test_evaluate_flow.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `grade(criterion, prompt_version, review_model=None, threshold=3, max_run_usd=None, batch_size=50, concurrency=8, grader: Grader | None = None, verdicts: dict[str, Verdict] | None = None, grades_csv: Path | None = None) -> GradeSummary(graded, skipped, failed, cost_usd, cost_projected_usd)` as `@flow(name="grade")`. Grader prompt version constant `GRADE_PROMPT_VERSION = "grade-v1"`. Same probe/spend-guard/echo/settle structure as `judge` (extract a shared helper only if it stays readable; copying the loop is acceptable, spec "left to the builder").
  - `metrics.proportion(k: int, n: int) -> Estimate` (public; `_proportion` delegates to it).
  - `RunReport.rationale_sound: Estimate | None`, `RunReport.quote_verbatim: Estimate | None`, `RunReport.sound_by_label: dict[tuple[int, int], int] | None` keyed `(sound, label)`, `RunReport.grader_model: str | None`, `RunReport.grade_cost_usd: float`.
  - Report section `## Rationale quality` with lines `- rationale sound: p [l, h] (k/n = k/n)`, `- quote verbatim: …`, and a four-line 2x2 (`- sound and relevant: N`, `- sound and not relevant: N`, `- unsound and relevant: N`, `- unsound and not relevant: N`), or the sentence `No grades yet; run `elusion grade` to add them.`; cost section gains `- grade run: $…` and total includes it.
  - `tracking.run_metrics` adds `rationale_sound_{point,low,high,k,n}`, `quote_verbatim_{…}`, `grade_cost_usd` when present; `run_params` adds `grader_model`, `grader_prompt_version` when present.
  - CLI `elusion grade --criterion --prompt-version [--review-model] [--threshold 3] [--max-run-usd]`.

- [ ] **Step 1: Failing tests**. `tests/test_grade_flow.py` mirrors `tests/test_judge_flow.py` with `FakeGrader`: grades every flagged verdict and writes rows; re-run skips graded keys; failures write no row; echo mismatch writes nothing; budget exceeded after probe; the grader is sent `grader_message(paragraph, verdict)` and never a label string (assert `"label"` not in any call's user content and the gold CSV path is never opened: monkeypatch `elusion.evaluate.gold.read_rows` to raise). `tests/test_evaluate_flow.py`: with a grades CSV for the pair the report has the section with the right k/n and 2x2; without it, the "No grades yet" sentence and no `rationale_sound_point` metric. `tests/test_report.py` and `tests/test_tracking.py` extended accordingly. `tests/test_cli.py`: `grade` passes options through.
- [ ] **Step 2: Run, confirm failure; implement; suite green**
- [ ] **Step 3: Commit**: `git commit -m "Phase 4 task 10: grade flow; rationale quality in the report and MLflow"`.
- [ ] **Step 4: Owner grades and re-evaluates every pair** (under $1 per pair; five pairs):

```
uv run elusion grade --criterion criteria/cloud-concentration.md --prompt-version v1
uv run elusion grade --criterion criteria/cloud-concentration.md --prompt-version v2
uv run elusion grade --criterion criteria/cloud-concentration.md --prompt-version v3
uv run elusion grade --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <openai snapshot>
uv run elusion grade --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <ollama name:tag>
```

then the five matching `evaluate` commands. Pass: every MLflow run now has `rationale_sound_point`. Commit the reports.

---

### Task 11: Registry: pyfunc reviewer, promote, `--reviewer`

**Files:**
- Create: `src/elusion/review/registry.py`
- Modify: `src/elusion/evaluate/tracking.py` (log the model), `src/elusion/flows/review.py`, `src/elusion/cli.py`, `tests/fakes.py`
- Test: `tests/test_registry.py`, `tests/test_review_flow.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `registry.Manifest` dataclass: `provider: str, model: str, prompt_version: str` (full `"<criterion>/<v>"`), `criterion: str`, `threshold: int`, `generation_settings: dict`; `to_json()`/`from_json()`.
  - `registry.ReviewerModel(mlflow.pyfunc.PythonModel)`: `load_context(context)` reads `context.artifacts["manifest"]` and `context.artifacts["prompt"]`, builds the provider with `make_provider`; `predict(context, model_input)` accepts a list of strings or a DataFrame with a `text` column and returns a DataFrame with columns `score, rationale, quote, model, cost_usd`. A `provider` attribute may be injected for tests before `predict`.
  - `registry.log_reviewer(manifest: Manifest, prompt_path: Path) -> None` called inside an active MLflow run: `mlflow.pyfunc.log_model(name="reviewer", python_model=ReviewerModel(), artifacts={"manifest": <tmp json>, "prompt": str(prompt_path)}, pip_requirements=["elusion"])`.
  - `registry.promote(run_id: str, name: str = "elusion-reviewer", alias: str = "champion", tracking_uri: str | None = None) -> int` (the new version number): `mlflow.register_model(f"runs:/{run_id}/reviewer", name)` then `MlflowClient().set_registered_model_alias(name, alias, version)`.
  - `registry.resolve(reviewer: str, tracking_uri: str | None = None) -> Manifest` for `"<name>@<alias>"`: downloads `models:/<name>@<alias>` artifacts and reads `manifest`; raises `LookupError(f"no alias {alias!r} on registered model {name!r}")` when MLflow raises its not-found error.
  - `review(..., reviewer: str | None = None)`: when set, `manifest = resolve(reviewer)`; `provider_name, model, prompt_version` come from it; the flow raises `ValueError("--reviewer cannot be combined with --provider, --model or --prompt-version")` if any of those was passed explicitly (CLI passes `None`/sentinel for unset; use `prompt_version: str | None = None` with default `"v1"` applied inside the flow).
  - CLI `elusion promote --run <id> [--name elusion-reviewer] [--alias champion]` prints `elusion-reviewer version N aliased champion` and the registry URL `http://localhost:5001/#/models/elusion-reviewer`; `elusion review --reviewer name@alias`.

- [ ] **Step 1: Failing tests** (`tests/test_registry.py`, file-store tracking URI in tmp; `test_manifest_round_trip`; `test_log_promote_resolve_round_trip` (log a run with `log_reviewer`, `promote` returns 1, `resolve("elusion-reviewer@champion")` equals the manifest, promoting the same run again returns 2 and moves the alias); `test_resolve_missing_alias_raises_lookup_error` (Review Focus 4); `test_predict_uses_injected_provider_and_returns_frame`; `test_logged_model_artifacts_contain_no_gold_or_grades` (list the model's artifact paths; none under `gold`/`grades`)). `tests/test_review_flow.py`: `test_reviewer_resolves_manifest_and_refuses_overrides` (monkeypatch `resolve`). `tests/test_cli.py`: `promote` and `review --reviewer` pass through.
- [ ] **Step 2: Run, confirm failure; implement; suite green**. In `tracking.log_run` call `log_reviewer(Manifest(provider, report.review_model, report.prompt_version, report.criterion, report.threshold, GENERATION_SETTINGS.get(provider, {})), prompt_path)` inside the run; `tests/test_tracking.py` gains the assertion that `reviewer/MLmodel` is among the run's artifacts.
- [ ] **Step 3: Commit**: `git commit -m "Phase 4 task 11: pyfunc reviewer logged per run; promote sets the champion alias; review --reviewer"`.
- [ ] **Step 4: Owner promotes and proves it**

The implementer proposes the winner by the spec's rule (best precision among runs whose elusion upper bound is no worse than the others'; cost breaks ties) with the run id from the UI. Owner runs:

```
uv run elusion promote --run <run id>
uv run elusion review --criterion criteria/cloud-concentration.md --reviewer elusion-reviewer@champion --sample 50
```

Pass: the second command logs `batch 1: 0 to score, 50 skipped` and spends $0; http://localhost:5001/#/models/elusion-reviewer shows version 1 with alias `champion`.

---

### Task 12: Phase note, ticks, docs

**Files:**
- Create: `docs/notes/phase-4.md`
- Modify: `docs/PHASES.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`, `README.md`

- [ ] **Step 1: Write `docs/notes/phase-4.md`** with the four headings from `docs/notes/README.md`, about 800 words: the five runs' numbers in one list (precision, elusion, recall bound, rationale sound, cost, docs per minute); in the owner's words what an experiment, run, param, metric, artifact, registered model, version and alias are; what promotion changed (an alias pointer and a resolvable configuration) and did not (nothing re-scored, no tokens); where the grader and the judge disagreed, two examples; what a production platform does instead (model gateway with central pinning, registry promotion gated by an evaluation suite, per-matter isolation); the question for their team.
- [ ] **Step 2: Tick** every Phase 4 box in `docs/PHASES.md`; the temperature box gets "(model and prompt version varied; temperature logged, not swept: DECISIONS 2026-11-05)".
- [ ] **Step 3: Docs**: `CLAUDE.md` commands block gains `grade` and `promote`, drops the `--run <mlflow run id>` comment; `README.md` status says phase 4 done and "First result" gains a line pointing at the leaderboard experiment and the promoted reviewer; `docs/ARCHITECTURE.md` gains a phase 4 diagram in the style of phases 1 to 3 (`evaluate -> MLflow run {params, metrics, report, prompt, reviewer model} -> registry elusion-reviewer@champion -> review --reviewer`).
- [ ] **Step 4: Commit and open the PR** from the `phase-4-leaderboard` branch (all tasks run on it): `gh pr create --base main --title "Phase 4: leaderboard"` with a body that explains, in plain words, the three MLflow concepts and the discard-sample fix.
