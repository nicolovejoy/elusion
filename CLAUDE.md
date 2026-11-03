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
uv run elusion evaluate --run <mlflow run id>
```

## Metrics vocabulary (use these words exactly)

- **recall**: fraction of truly relevant paragraphs the system flagged.
- **precision**: fraction of flagged paragraphs that are truly relevant.
- **elusion**: relevant rate inside the discard pile, measured by sampling it.
- **richness**: fraction of the whole corpus that is relevant.
- All four are reported with Clopper-Pearson 95% intervals and the sample sizes behind them.
