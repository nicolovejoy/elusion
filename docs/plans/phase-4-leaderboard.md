# Phase 4 spec: leaderboard

Status: designed 2026-11-05, not started. Read `docs/ARCHITECTURE.md` (MLflow lines), the
Phase 4 checklist in `docs/PHASES.md` and `docs/notes/phase-3.md` first. Hard rules in
`CLAUDE.md` apply to every line below. The phase 3 spec (`docs/plans/phase-3-validation.md`)
is the pattern this one follows.

## Intent

Phase 3 produced one validated run. Phase 4 produces several and compares them in the
tool built for that comparison. MLflow is an *experiment tracker*: each attempt at a problem
is a "run" with parameters (what you set), metrics (what you measured) and artifacts (files
you kept), and the UI lays runs side by side. Its *model registry* is a named, versioned
shelf for the thing you would deploy, with an *alias* (such as `champion`) that points at the
version in use. Here the thing on the shelf is not a weights file but a reviewer
configuration: provider, pinned model id, prompt version, threshold, generation settings.

Three facts shape the design:

- A gold label belongs to a document, not to a run. The judge's labels from phase 3 are
  reusable by every run; a new run only pays to label the flagged docs it adds and its
  own discard sample.
- The discard sample must be a uniform draw from *that run's* discard pile. Today
  `evaluate` takes "every judge-labeled doc scoring below the threshold under the pair" as
  the sample. With one run that is the seeded draw. With a second run, docs the first run
  flagged (judge-labeled, enriched with relevant ones) would land in the second run's
  discard sample and bias its elusion upward. Task 1 fixes this before any second run.
- The judge is the ground truth, so the judge model never appears as a reviewer: it would
  be grading itself.

## Decisions already made

Owner decisions, 2026-11-03 (phase 3 spec): v1 scores stay in the table as the phase 4
comparison; the judge model is pulled forward to phase 3 and phase 4's judge item is the
different use of grading rationales.

Controller's calls, 2026-11-05, for the owner to confirm or overturn:

1. **One MLflow run per `evaluate`.** The checklist says "every review+evaluate run
   logged". The review flow already records itself in `review_jobs` and Prefect; the
   evaluate flow is where the numbers exist, so it logs one MLflow run carrying the
   review's parameters, cost and wall-clock alongside the metrics and the report. Re-running
   evaluate makes a new MLflow run; the latest run for a pair is the current one. No
   dedupe, because evaluate spends no tokens and an MLflow run is a record of an attempt.
2. **Five runs vary model and prompt version, not temperature.** Haiku 5.5 uses adaptive
   thinking and the review passes neither `thinking` nor `temperature`; the OpenAI
   small models reject `temperature`. Varying it would also need a generation-settings
   column in the `scores` key, because (document, model, prompt version) would no longer
   identify a result. So the runs are: (haiku, v1), (haiku, v2), (haiku, v3), (OpenAI
   small model, v2), (Ollama local model, v2). Generation settings (`effort` for
   Anthropic, `temperature` where set) are logged as MLflow params so the axis is visible
   even though it is not varied. The checklist line is ticked with that note.
3. **Gold set is per criterion and shared across runs**, as phase 3 built it. Each new
   run's `judge` call draws its own 200-discard sample with the same seed from its own
   discard pile; docs already labeled by the judge are skipped and cost nothing.
4. **The registered model is a pyfunc wrapper around the configuration.** Registering
   plain JSON would teach the registry's bookkeeping but not what it is for. A small
   `mlflow.pyfunc.PythonModel` whose `predict` takes paragraph texts and returns scores
   by calling the provider makes a registry version something that can actually review.
   Promotion sets the alias `champion` (MLflow 3 aliases; stages are deprecated).
   `elusion review --reviewer elusion-reviewer@champion` resolves the configuration from
   the registry, which is what promotion is for.
5. **Ollama through its OpenAI-compatible endpoint** using the `openai` SDK (already an
   optional extra), so one client library serves both new providers. If the compatible
   endpoint does not honor a JSON schema, the builder uses Ollama's native `/api/chat`
   with `format` over `httpx` (already a transitive dependency) rather than adding the
   `ollama` package. Either way, no new top-level dependency.
6. **Rationale grading is a separate flow and file.** The grader sees the paragraph and
   the reviewer's score, rationale and quote, never a gold label. Grades are not gold
   and live in `data/grades/`, keyed by (doc_id, review model, prompt version, grader).

## Goal

```
uv run elusion review   --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000 --provider openai --model <pinned snapshot>
uv run elusion judge    --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
uv run elusion grade    --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
uv run elusion promote  --run <mlflow run id>
uv run elusion review   --criterion criteria/cloud-concentration.md --reviewer elusion-reviewer@champion --sample 50
```

At the end, http://localhost:5001 shows at least five runs in one experiment, each with
precision, elusion, recall and richness bounds, cost, wall-clock and rationale soundness,
and the registry holds `elusion-reviewer` with one version aliased `champion`.

## Inputs already in place

- `providers.py`: `Provider` and `Judge` protocols, `AnthropicProvider` with structured
  output, cached system prompt, echo check, `ReviewError` with usage.
- `flows/review.py`: probe, spend guard, batch settle, `check_model_echo`.
- `flows/evaluate.py`: `judge` and `evaluate`; `evaluate/gold.py` with `select_gold`
  (seeded, deterministic), `labeled_ids`, `judge_rows`, `human_rows`.
- `evaluate/report.py`: `RunReport` and `render`.
- `pricing.py` with Haiku 5.5 and Opus 5.5 rows; `cost_usd` raises for an unpriced model.
- `mlflow` is already a dependency; the compose service runs MLflow 3.17.2 on :5001;
  `MLFLOW_TRACKING_URI` is in `.env.example` but not in `config.py`.
- `openai` is an optional extra in `pyproject.toml`; `OPENAI_API_KEY` and `OLLAMA_HOST`
  are in `.env.example`. Ollama is not installed on the dev Mac (Apple M5 Pro, 48 GB).
- v1 scores for 12,906 docs under (claude-haiku-5-5, cloud-concentration/v1).

## Tasks, in order

Each code task is test-first, one commit, `uv run ruff check . && uv run pytest` green.
Tasks marked **owner** are run by the owner at the terminal because they spend money,
need `.env`, or install software; the controller hands over the exact command and waits.
No test makes a live call; providers are faked as in phase 3.

### 1. Make the pair explicit and the discard sample exact

- `judge`, `evaluate` and the CLI gain `--review-model` (default `settings.review_model`).
  Every place that read `settings.review_model` for the pair uses the option.
- `evaluate` recomputes the discard sample as `select_gold` would draw it: the same
  seeded uniform draw of `discard_n` ids from the pair's current discard pile (new option
  `--discard-n`, default 200, echoed in the report). The sample = that draw intersected
  with the judge's labels; if any drawn id lacks a label, evaluate refuses and names the
  count, same as it does for flagged docs. The report's "discard sample labeled by the
  judge" line keeps its meaning, and a new line states "drawn with seed S from the
  pair's discard pile".
- `docs/reports/` for the existing v2 run is regenerated; the numbers must not change
  (the single-run case was already the seeded draw). If they do, stop and report why.
- Tests: two fake pairs sharing a CSV; the second pair's sample excludes docs the first
  flagged unless its own draw picked them; determinism across two evaluate calls.

### 2. MLflow logging in `evaluate`

- `config.py`: `mlflow_tracking_uri` from `MLFLOW_TRACKING_URI`, default
  `http://localhost:5001`.
- After the report is written, `evaluate` opens one MLflow run in experiment
  `elusion/<criterion>` with run name `<review model>/<prompt version>` and logs:
  - params: criterion, provider, review_model, prompt_version, judge_model,
    judge_prompt_version, threshold, seed, discard_n, effort or temperature (whichever
    the provider used), review_jobs (count), report_path.
  - metrics: for each of precision, elusion, richness, recall and agreement: `point`,
    `low`, `high`, `k`, `n` as `<metric>_point` etc. (MLflow metrics are floats; k and n
    go in as floats too). Plus population, n_flagged, review_cost_usd, judge_cost_usd,
    total_cost_usd, cost_per_1000_docs_usd, review_wall_clock_s, docs_per_minute. When
    task 7 exists: rationale_sound_point/low/high/k/n.
  - artifacts: the report Markdown, the review prompt file (`prompts/<criterion>/<v>.md`)
    and the criterion file. Never the gold CSV, the grades CSV or any label.
  - tags: `phase=4`, `elusion.pair=<model>|<version>`.
- MLflow's client is slow to import; import it inside the function so `elusion --help`
  stays fast.
- Tests: `mlflow.set_tracking_uri` to a temp directory; after a fake evaluate, one run
  exists with the expected params, metrics and artifact names; no gold file among the
  artifacts.
- `docs/ARCHITECTURE.md` phase 3/4 lines: the report now also flows to MLflow.

### 3. Owner: first two MLflow runs

```
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2
uv run elusion judge    --criterion criteria/cloud-concentration.md --prompt-version v1
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v1
```

The v1 judge call labels v1's flagged docs (score >= 3 under v1) not yet labeled plus
its own discard draw: a few hundred Opus labels, about $1. The v1 report evaluates the
loose prompt's scores against the tightened criterion; the phase note says so. Open
http://localhost:5001 and confirm two runs sit side by side in `elusion/cloud-concentration`.

### 4. OpenAI provider and provider selection

- `providers.py`: `OpenAIProvider(model, client=None, base_url=None, api_key=None)` with
  `name = "openai"`, implementing `review` and `label` through one `_call`, using
  structured output with a strict JSON schema (the builder reads the installed SDK's
  docs for the current structured-output surface and does not recall it). Echo check:
  the response's `model` must equal the pinned id, so the pinned id is the dated snapshot
  form. Usage maps onto the four buckets: `cached_tokens` -> `cache_read_input_tokens`,
  the rest of prompt tokens -> `input_tokens`, `cache_write` = 0. Refusals, truncation
  (`finish_reason`/`incomplete`) and invalid JSON raise `ReviewError` with usage, as the
  Anthropic path does. `temperature` is passed only when the provider is constructed
  with one; recorded on the provider as `generation_settings: dict` for MLflow.
- `AnthropicProvider` gains the same `generation_settings` attribute (`{"effort": "low"}`).
  `Provider` protocol gains `generation_settings`.
- `pricing.py`: a row for the chosen OpenAI model. The builder looks prices up, does not
  recall them, and adds a `DECISIONS.md` line with model id, source and date. Cached
  input price goes in `cache_read`; `cache_write` is 0 for OpenAI.
- `scores.provider` already exists and is written from `provider.name`; `review_jobs`
  gets no new column (the model id identifies the provider's row in `pricing.py`;
  evaluate derives provider from the scores rows).
- CLI `review` gains `--provider anthropic|openai|ollama` (default anthropic) and
  `--model` (default `settings.review_model`); `judge` keeps using the Anthropic judge.
  The consumer group id already includes the model, so a new model starts the topic from
  the beginning and covers the same first ids.
- `pyproject.toml`: `openai` moves from an optional extra to a dependency (it is now
  used by two providers; the owner confirmed phase 4 scope). Record in `DECISIONS.md`.
- Tests: a fake OpenAI client returning canned responses: happy path, refusal, truncated,
  invalid JSON, cached tokens mapped, echo mismatch raised by the flow.

### 5. Owner: OpenAI run

```
uv run elusion review   --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000 --provider openai --model <pinned snapshot>
uv run elusion judge    --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <pinned snapshot>
```

The spend guard applies. Record cost and wall-clock in the phase note.

### 6. Ollama provider

- **Owner** installs Ollama (`brew install ollama`), starts it, pulls one model the
  controller proposes after checking the current Ollama library (an 8B-to-14B instruct
  model that supports structured output; the M5 Pro with 48 GB runs that comfortably).
  The pinned id is `name:tag`; the builder records the model digest from `ollama show`
  as an MLflow param and in `DECISIONS.md`, because `name:tag` can move.
- `OllamaProvider`: `name = "ollama"`, decision 5 above, `base_url` from `OLLAMA_HOST`
  (`config.py` gains `ollama_host`, default `http://localhost:11434`). Pricing row with
  all four rates 0 so `cost_usd` returns 0 rather than raising. Usage is whatever the
  endpoint reports; cache buckets 0. Echo check compares the returned model name to the
  pinned `name:tag`. `temperature=0` by default for this provider, recorded in
  `generation_settings`.
- Concurrency: the review CLI's `--concurrency` default of 8 is for a hosted API; the
  phase note records what the local machine sustained. The builder may lower the default
  when `--provider ollama`.
- Tests: fake client; schema honored; zero cost; echo check.

### 7. Owner: Ollama run

```
uv run elusion review   --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000 --provider ollama --model <name:tag> --concurrency 2
uv run elusion judge    --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <name:tag>
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2 --review-model <name:tag>
```

If 20,000 docs is too slow for one evening, the owner may stop at a smaller `--sample`
and re-run later: the consumer group resumes. The report says what the population is,
and the phase note says whether the populations coincide.

### 8. Prompt v3

- `src/elusion/review/prompts/cloud-concentration/v3.md` from the gap list in
  `docs/notes/phase-3.md`: SaaS vendors, ISPs (count only if they also host or
  colocate), generic IT outsourcing, mixed on-site plus cloud setups, each given an
  explicit side of the line. The 0-4 scale and JSON fields are unchanged. The criterion
  file `criteria/cloud-concentration.md` is updated in the same commit if the line
  moves, with a `DECISIONS.md` entry. v1 and v2 are never edited.
- **Owner**: review, judge, evaluate under (haiku, v3), the three commands as in task 5
  without `--provider`. About $1.30 review plus the judge delta.

### 9. Rationale grading

- `src/elusion/review/prompts/cloud-concentration/grade-v1.md`: the grader's system
  prompt. States the criterion in full and asks, for a paragraph plus a reviewer's
  score, rationale and quote: is the rationale a faithful reading of the paragraph under
  this criterion (`sound: bool`), and one sentence why. No gold label, no other model's
  answer. The verbatim-quote check (`quote in paragraph`) is done in Python, not by the
  grader, and reported separately.
- `providers.py`: `Grade(BaseModel)` with `sound` and `rationale`; `grade()` on
  `AnthropicProvider` through the same `_call`; a `Grader` protocol.
- `src/elusion/evaluate/grades.py`: CSV at `data/grades/<criterion>.csv`, columns
  `doc_id, review_model, prompt_version, score, grader, grader_prompt_version, sound,
  quote_verbatim, rationale, graded_at`; a key appears at most once.
- `flows/evaluate.py`, `@flow(name="grade")`: for a pair, every flagged doc (score >=
  threshold) whose scores row has a rationale; skip keys already graded; probe and spend
  guard as the judge flow; echo check; append per batch. The grader is
  `settings.judge_model` under `grade-v1`.
- `evaluate` reads grades for the pair if present: `rationale_sound` as an `Estimate`
  over flagged docs graded, `quote_verbatim` as an `Estimate`, and a 2x2 of sound
  against the judge label (sound and relevant, sound and not relevant, unsound and
  relevant, unsound and not relevant). New report section "Rationale quality"; MLflow
  metrics from task 2. When no grades exist the section says so and no metric is logged.
- CLI `elusion grade --criterion --prompt-version --review-model [--threshold]
  [--max-run-usd]`.
- Tests: fake grader; idempotency by key; quote check; report section; evaluate with and
  without grades.
- **Owner**: `grade` then `evaluate` for each of the five pairs. Roughly 75 to 150 Opus
  calls per pair with a longer user message than the judge's; under $1 per pair.

### 10. Registry and promotion

- `src/elusion/review/registry.py`: `ReviewerModel(mlflow.pyfunc.PythonModel)`. Its
  artifacts are a `reviewer.json` manifest (provider name, pinned model id, prompt
  version string, threshold, generation settings) and the prompt file. `load_context`
  builds the provider from the manifest; `predict(texts)` returns one row per text with
  score, rationale, quote, model and cost, calling the provider. No gold, no scores
  table, no database access inside the model.
- `evaluate` logs the model to its run with `mlflow.pyfunc.log_model` (artifact name
  `reviewer`) so every run is registrable.
- CLI `elusion promote --run <mlflow run id> [--name elusion-reviewer] [--alias champion]`:
  `mlflow.register_model("runs:/<id>/reviewer", name)` creates a new version; then sets
  the alias on that version. Prints the version number and the UI URL. Re-running on the
  same run creates another version (MLflow semantics; the note says so).
- `elusion review --reviewer <name>@<alias>`: loads the manifest from the registry
  (`models:/<name>@<alias>`) and builds provider, model and prompt version from it;
  `--provider`, `--model` and `--prompt-version` are refused alongside it. Everything
  else in the flow is unchanged, so idempotency still keys on (doc, model, prompt
  version) and a promoted reviewer re-run never re-spends.
- Tests: log, register and alias against a temp tracking store; manifest round trip;
  `predict` with a fake provider; the CLI refusal of conflicting options.
- **Owner**: pick the winner (the note says by what rule: precision at an elusion bound
  no worse than the others, cost second), `promote`, then run
  `elusion review --criterion ... --reviewer elusion-reviewer@champion --sample 50` and
  confirm `0 to score, 50 skipped`.

### 11. Owner: phase note, ticks, decisions, docs

`docs/notes/phase-4.md` with the four headings. It must say: what a tracking run, an
experiment, a registered model version and an alias are, in the owner's words; what
"promotion" changed and what it did not; the five runs' numbers in one list; where the
rationale grader and the judge disagreed, with examples; what a production platform does
instead (model gateway, central registry, promotion gated by an evaluation suite). Tick
all Phase 4 boxes, the temperature one with its note. `DECISIONS.md` lines for each
numbered call above once confirmed. `CLAUDE.md` commands block gains `grade` and
`promote` and drops the `--run <mlflow run id>` comment on evaluate. `README.md` status
line and "First result" gain a pointer to the leaderboard. `docs/ARCHITECTURE.md` phase 4
section drawn like the others.

## Non-goals and anti-goals

- No Spark, no Delta, no Azure (phases 5 and 6).
- No new top-level dependency beyond moving `openai` from extra to dependency.
- No web UI of our own: MLflow's UI is the leaderboard.
- No temperature sweep (decision 2).
- Anti-goal: the judge model as a reviewer.
- Anti-goal: any label, grade or gold row reaching an MLflow artifact, a prompt, or the
  registered model's artifacts.
- Anti-goal: a registry version that cannot be traced to a run with a report.

## Left to the builder

- The exact OpenAI structured-output call shape and the Ollama path (decision 5).
- Metric naming details in MLflow, as long as point/low/high/k/n are all present.
- Whether `grade` shares `run_batch`/probe code with `judge` by extraction or by copy.
- The manifest's exact fields beyond those listed.

## Expected cost

Judge for v1: about $1. OpenAI review: $1 to $3 depending on the model. Haiku v3: about
$1.30. Judge deltas for three new pairs: about $1.50. Grading five pairs: under $5.
Ollama: $0. Total under $12, spread over several runs each guarded at $10.
