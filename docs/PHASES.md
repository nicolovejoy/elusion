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

- [ ] Provider interface with Anthropic implementation; model snapshot ID pinned in config
- [ ] Versioned prompts under `src/elusion/review/prompts/<criterion>/v1.md`
- [ ] Structured output: `score` 0–4, `rationale`, `quote`
- [ ] Postgres `scores` table keyed by `(doc_id, model, prompt_version)`; job table for resumability
- [ ] Batching, concurrency limit, 429 backoff, token and cost counters
- [ ] Cost guard: refuse runs projected over `ELUSION_MAX_RUN_USD`
- [ ] Prefect flow `review --sample 20000`; kill it mid-run and prove it resumes without re-spending
- [ ] Note: what partial failure looked like; what Prefect gave us vs a plain loop

## Phase 3: Validation (days 6–7)

- [ ] Labeling CLI: shows a paragraph, takes y/n/skip, writes `data/gold/<criterion>.csv`
- [ ] Label ~300 paragraphs: stratified so the gold set has enough positives
- [ ] Metrics: recall, precision, elusion, richness; Clopper-Pearson 95% intervals (scipy)
- [ ] Elusion measured by sampling the discard pile, not derived
- [ ] Report generator: one page per run (criterion, model, prompt version, n, metrics with intervals, cost, wall-clock, companies found)
- [ ] Note: the metric definitions in your own words; what a skeptical reader would ask

## Phase 4: Leaderboard (days 8–9)

- [ ] Every review+evaluate run logged to MLflow: params, metrics, report as artifact
- [ ] Second provider (OpenAI) and local model (Ollama) behind the same interface
- [ ] At least five runs varying model, prompt version, temperature
- [ ] Register the best as `elusion-reviewer` in the registry; promote one version
- [ ] LLM-as-judge: a stronger model grades rationales against gold labels; agreement reported
- [ ] Note: what the registry is for; what "promotion" means; where judge and human disagreed

## Phase 5: Dedupe with Spark (optional, days 10–11)

- [ ] PySpark local: MinHash LSH over all paragraphs, near-duplicate clusters
- [ ] Write clusters as a Delta table
- [ ] Review flow scores one representative per cluster and copies the score to members
- [ ] Prefect triggers the Spark job
- [ ] Note: where Spark beat a loop and where it did not; cluster-size distribution

## Phase 6: Production in Azure (days 12–14)

- [ ] Terraform: resource group, AKS (1–2 small nodes), container registry, blob container
- [ ] Helm chart: review service + Prefect worker; secrets from Kubernetes secrets, not the chart
- [ ] One real review run on the cluster against the 20k sample; report notes the cluster name
- [ ] `terraform destroy` the same day; confirm the bill
- [ ] Optional: Pulumi (Python) rewrite of the Terraform module; note the differences
- [ ] Note: AWS-to-Azure mapping; what Helm did for us; what it cost
