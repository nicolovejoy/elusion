# CLAUDE.md — elusion

Elusion is a learning project: a miniature document-review pipeline over public SEC 10-K
risk-factor paragraphs, built to learn Prefect, MLflow, Spark, Azure (AKS) and Pulumi by
using each for the job it is actually for. The owner is preparing for Staff AI Engineer
conversations at an e-discovery company; the point is understanding, not features.

Read `docs/PROJECT.md` (goals, the matter, done criteria) and `docs/PHASES.md` (the
checklist) before starting any work. `docs/ARCHITECTURE.md` shows how data flows between
the stores and why each store exists. `docs/plans/phase-N-*.md` is the spec for the next phase. Work one phase at a time, in order.

## How to work in this repo

1. **Explain as you go.** The owner is an experienced systems engineer new to the ML
   tooling. When you introduce a tool or concept (a Prefect task, an MLflow run, MinHash),
   add two or three sentences of plain explanation in the PR description or the phase note.
   Define terms the first time they appear.
2. **Ask before adding scope.** No new features, services or dependencies beyond the current
   phase without asking. If something seems necessary, say so and wait.
3. **Local first.** Everything runs from `compose.yaml` until phase 6. Never create Azure
   resources before phase 6, and never without the owner running `terraform apply` themselves.
4. **Phase notes are part of done.** A phase is finished when `docs/notes/phase-N.md` exists
   with the four headings in `docs/notes/README.md`.
5. **Decisions go in `docs/DECISIONS.md`**, one dated line each, as they are made.

## Hard rules

- Every LLM call carries a pinned model snapshot ID and a prompt version string, and both
  are stored with the result. No call without them.
- Idempotent by document id: re-running any flow must never re-spend tokens on a document
  that already has a score for that (model, prompt version) pair.
- The gold set (`data/gold/`) is never included in any prompt, few-shot example, or
  retrieval context. It exists only for evaluation.
- Public repo: no secrets in git. Configuration comes from `.env` (gitignored);
  `.env.example` lists every variable with a comment.
- Spend guard: the review flow refuses to start a run projected to cost more than
  `ELUSION_MAX_RUN_USD` (default 10).

## Stack (fixed unless a decision says otherwise)

- Python 3.12, `uv` for environments and dependencies, `ruff` for lint/format, `pytest`.
- Prefect 3 for orchestration; MLflow for tracking and registry; Postgres for documents,
  scores and the job table; Parquet for the corpus; Kafka (Redpanda image) for the
  `documents.ingested` topic.
- Anthropic SDK for the bulk review and judge passes; OpenAI and Ollama optional, behind
  one provider interface in `src/elusion/review/providers.py`.
- Optional: PySpark + Delta Lake (phase 5); Terraform + Helm on AKS (phase 6); Pulumi
  rewrite (phase 6, optional).

## Layout

```
src/elusion/collect/    EDGAR-CORPUS download, paragraph split, Parquet, Postgres load, Kafka publish
src/elusion/review/     provider interface, pinned models, versioned prompts, scorer, job table
src/elusion/evaluate/   labeling CLI, metrics (Clopper-Pearson), report generator
src/elusion/flows/      Prefect flows: collect, review, evaluate, dedupe
src/elusion/spark/      optional MinHash near-duplicate job
infra/terraform/  infra/helm/elusion/  infra/pulumi/
docs/  data/ (gitignored)  tests/
```

## Commands (fill in as they exist)

```
docker compose up -d          # postgres, redpanda, mlflow, prefect server
uv sync                       # install
uv run pytest                 # tests
uv run elusion collect --year 2019
uv run elusion review --criterion criteria/cloud-concentration.md --sample 20000
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2   # the --run <mlflow run id> form is phase 4
```

## Metrics vocabulary (use these words exactly)

- **recall**: fraction of truly relevant paragraphs the system flagged.
- **precision**: fraction of flagged paragraphs that are truly relevant.
- **elusion**: relevant rate inside the discard pile, measured by sampling it.
- **richness**: fraction of the whole corpus that is relevant.
- All four are reported with Clopper-Pearson 95% intervals and the sample sizes behind them.

<!-- SHARED-CONVENTIONS:BEGIN v=3308b16045f0 — auto-managed, do not edit here; source: prompt-lab/workflow/claude-md-shared.md (edit + re-sync) -->
## Shared conventions

<!-- These are Nico's cross-repo output rules. They're materialized into each repo's
CLAUDE.md so every agent (local, cloud, headless) sees them as plain text. Source of
truth: prompt-lab/workflow/claude-md-shared.md — edit there and re-sync, never here. -->

- **Clickable URLs.** When pointing at any web destination (dashboard, repo, PR, deploy, settings, docs, localhost), print the full bare URL — `https://example.com` or `http://localhost:8080` — on its own, never just the page's name and never a markdown `[label](url)` link. Nico's terminal auto-linkifies raw `https://` text, so a bare URL is one-click and stays copyable.

- **Number your questions.** Any time you ask Nico more than one question, present them as a numbered list (1., 2., 3.) so he can answer by number with no ambiguity. A single standalone question needs no number.

- **Never ask Nico to recall a past moment.** No "think of the last time X happened", "walk me through the last time…", or any question he can only answer by remembering an event. Look the history up yourself (the prompt-history DB, git, logs, the data), then propose something concrete for him to react to, or ask directly what he wants. This overrides the brainstorming skill's "walk me through the last time" question style (Nico, 2026-11-03).

- **Self-contained smoke-test instructions.** When you ask Nico to manually test or verify an app or website, assume zero carried-over context — he should never scroll back or recall a URL/path/credential from earlier. Always include: the exact URL (full `https://…` or `http://localhost:…`, restated even if mentioned above), the precise steps in order, and what a pass vs. fail looks like. Repetition here is a feature, not clutter.

- **UTC at rest, Pacific on display.** Timestamps are stored in UTC, always. A *calendar day* shown to a human is `America/Los_Angeles` — Nico's day, and the clock the work actually happened on. The two rules that follow are the ones that get broken: never form a date bucket with `new Date(…).toISOString().slice(0,10)` (that is UTC, so every chart axis and "today" silently rolls over at 5pm Pacific — it put a phantom tomorrow bar on the Prompt Lab dashboard), and never bucket UTC-stamped rows with a bare `date(col)` in SQL. Use `Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles' })` in JS and an explicit zone in SQL/Python. Storage in local time is also wrong — it can't be migrated across a DST boundary without loss.

- **No marker before a copy-paste command block.** Nico's terminal renders markdown bullets (`-`, `*`, `•`) as `●`, which breaks paste into zsh. The line directly above a fenced command block must be a plain-text label ending in a colon — never a bullet, dash, asterisk, or number. For loud copy targets, lead the label with `📋` + bold `COPY THE BELOW`, then a colon, then the block. Bracket anything Nico will paste elsewhere (a prompt for another agent, a multi-line command) with a ruler line of `================` above the label and below the closing fence. Rulers go outside the fence so they aren't copied, each with a blank line before it (a `===` line directly under text renders as a heading).

- **No bare backslash in a copy-paste command block.** A `\` is load-bearing shell syntax that renders invisibly and gets silently dropped somewhere between the markdown render, the clipboard, and zsh. `find … -exec test -e {} \; -delete` arrived in the terminal as `… {} ; -delete`, which zsh split into two commands and reported as `find: -exec: no terminating ";"` plus `command not found: -delete` (2026-09-20). Quote it instead — `';'` is exactly equivalent to `\;` and survives any copy path. For the same reason never break a command across lines with a trailing `\`: write one long line, however wide it wraps.

- **Reader-facing text lives in content or copy files, not inline in components.** Some repos ship French alongside English (selected-projects mirrors the site under `/fr`; ibuild4you has French public pages). There, every new string goes in the repo's strings/copy module or content folder, with its translation, and an English copy edit means updating the French twin too. Elsewhere, keeping text out of components is what makes `/localize` cheap later. Terms and register: `prompt-lab/workflow/localization/fr-glossary.md`.

- **A review another agent must act on goes on the PR.** A review that another agent must act on, or that must outlive the session, is posted as a PR comment (`gh pr comment`), where the next session or agent finds it. Live, in-session reviews between Nico and the agent stay in chat. There is no devlog.md: the history DB is the one session record.

- **Browser testing runs headless.** A headed browser takes focus on Nico's Mac. Run Playwright headless, with Edge (`channel: 'msedge'`) for desktop runs wherever a config picks a browser. Never use `headless: false`, `--headed`, `--debug`, `--ui` or `PWDEBUG` unless Nico asks for it in this session. For a visual check, take a screenshot or record a trace (`trace: 'on-first-retry'` or `'on'`) instead of opening a window. The Claude-in-Chrome extension is the one thing that drives Nico's real Chrome, on purpose.
<!-- SHARED-CONVENTIONS:END -->
