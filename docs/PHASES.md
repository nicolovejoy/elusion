# Phases

Work in order. A phase is done when every box is ticked and `docs/notes/phase-N.md` exists.

## Phase 0: Scaffolding

- [x] `uv init`, `pyproject.toml` with `elusion` CLI entry point, `ruff`, `pytest`
- [x] `compose.yaml` brings up Postgres, Redpanda, MLflow, Prefect server; all four UIs reachable
- [x] `.env.example`, `.gitignore`, `data/` ignored
- [x] `src/elusion/` package skeleton with empty modules per `CLAUDE.md` layout
- [x] One passing test

## Phase 1: Collection (days 1–2)

- [x] Download EDGAR-CORPUS for three filing years via HuggingFace `datasets`
- [x] Split each Item 1A into paragraphs; drop fragments under ~30 words
- [x] Write Parquet: `doc_id, cik, company, year, paragraph_index, text`
- [x] Load metadata into Postgres `documents` table
- [x] Publish `doc_id`s to Kafka topic `documents.ingested`
- [x] Prefect flow `collect` with retries; visible in the Prefect UI
- [x] Note: paragraph counts per year, time to ingest, what Parquet bought us

## Phase 2: First-pass review (days 3–5)

- [x] Provider interface with Anthropic implementation; model snapshot ID pinned in config
- [x] Versioned prompts under `src/elusion/review/prompts/<criterion>/v1.md`
- [x] Structured output: `score` 0–4, `rationale`, `quote`
- [x] Postgres `scores` table keyed by `(doc_id, model, prompt_version)`; job table for resumability
- [x] Batching, concurrency limit, 429 backoff, token and cost counters
- [x] Cost guard: refuse runs projected over `ELUSION_MAX_RUN_USD`
- [x] Prefect flow `review --sample 20000`; kill it mid-run and prove it resumes without re-spending
- [x] Note: what partial failure looked like; what Prefect gave us vs a plain loop

## Phase 3: Validation (days 6–7)

- [x] Labeling CLI: shows a paragraph, takes y/n/skip, writes `data/gold/<criterion>.csv`
- [x] Label ~300 paragraphs: stratified so the gold set has enough positives (275 by the judge, 20 by the owner)
- [x] Metrics: recall, precision, elusion, richness; Clopper-Pearson 95% intervals (scipy)
- [x] Elusion measured by sampling the discard pile, not derived
- [x] Report generator: one page per run (criterion, model, prompt version, n, metrics with intervals, cost, wall-clock, companies found)
- [x] Note: the metric definitions in your own words; what a skeptical reader would ask

## Phase 4: Leaderboard (days 8–9)

- [x] Every review+evaluate run logged to MLflow: params, metrics, report as artifact
- [x] Second provider (OpenAI) and local model (Ollama) behind the same interface
- [x] At least five runs varying model and prompt version (not temperature: DECISIONS 2026-11-05)
- [x] Register the best as `elusion-reviewer` in the registry; promote one version
- [x] LLM-as-judge: a stronger model grades rationales against gold labels; agreement reported
- [x] Note: what the registry is for; what "promotion" means; where judge and human disagreed

## Phase 5: Dedupe with Spark (optional, days 10–11)

- [ ] PySpark local: MinHash LSH over all paragraphs, near-duplicate clusters
- [ ] Write clusters as a Delta table
- [ ] Review flow scores one representative per cluster and copies the score to members
- [ ] Prefect triggers the Spark job
- [ ] Note: where Spark beat a loop and where it did not; cluster-size distribution

## Phase 6: Production in Azure (days 12–14)

- [x] Terraform: resource group, AKS (1–2 small nodes), container registry, blob container
- [x] Helm chart: review service + Prefect worker; secrets from Kubernetes secrets, not the chart
- [x] One real review run on the cluster against the 20k sample; report notes the cluster name
- [x] `terraform destroy` the same day; confirm the bill ($0.93, confirmed 2026-11-20)
- [ ] Optional: Pulumi (Python) rewrite of the Terraform module; note the differences
- [x] Note: AWS-to-Azure mapping; what Helm did for us; what it cost

## Phase 7: The literature matter (after phase 6)

A second corpus, about the project's own subject: recent research on AI agents making
decisions that must be auditable. Added 2026-11-15 (see `docs/DECISIONS.md`). This list is
a sketch; it is refined when the phase 7 spec is written, after phase 6 closes.

- [x] Collector for OpenAlex abstracts from a recorded list of broad search terms; each abstract stored with its OpenAlex work id, the harvest query, the harvest date and a content hash (the chain-of-custody record)
- [x] Harvest recall estimated on its own: a list of known on-topic papers checked against what was harvested. Elusion measures the screen, not the harvest; a paper the query never returned is not in the null set
- [x] A screening criterion and prompt v1; review, judge and evaluate run unchanged
- [x] Report: the four metrics with intervals, plus a PRISMA-style count of records found, screened, excluded and kept
- [x] `ask` command: answers built only from quoted passages with their document ids; question, retrieved passages, model and prompt version logged
- [x] A gold question set, kept out of every prompt and retrieval context; recall@k and groundedness with Clopper-Pearson intervals (drafted and cut by models, not by the owner; see `docs/DECISIONS.md`, 2026-11-26)
- [ ] Note: what screening kept and missed; how far an answer can be trusted; the field's vocabulary in your own words
