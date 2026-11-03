# Decisions

One line each, newest first. Format: `YYYY-MM-DD — decision — why`.

- 2026-11-03 — A refused or truncated document is not retried under the same consumer group; its batch offsets still commit — one bad paragraph must not wedge a 1.6 M-document run; whether to re-ask (other model, longer limit) or send it to a human queue is a phase 3 question.
- 2026-11-03 — Provider calls `messages.create` with `output_config.format` and reads `stop_reason` itself, not `messages.parse`; max_tokens 1024 — a truncated or refused answer becomes a per-document ReviewError instead of crashing the flow (run 2 died on a half-written JSON quote at 512).
- 2026-11-03 — Spend guard projects from the first 20 successful scores, takes the mean as an upper bound, and refuses above ELUSION_MAX_RUN_USD — a cheap probe before the bulk spend; the projection ($1.38) was above the actual rate.
- 2026-11-03 — Kafka consumer group name encodes criterion, model and prompt_version — a new prompt version starts from the beginning of the topic with no manual offset reset.
- 2026-11-03 — Scores are keyed on (doc_id, model, prompt_version) with ON CONFLICT DO NOTHING, and Kafka offsets commit only after the Postgres insert — a crash can only cause a re-pull, which the key absorbs at zero cost; it can never lose a score.
- 2026-11-03 — Review model is claude-haiku-5-5; judge is claude-opus-5-5 — Haiku 5.5 is $0.10/$0.50 per MTok, so 20k paragraphs cost well under $1; the API no longer publishes dated snapshot aliases, so the pinned id is the bare string and the `model` field the API returns is stored with every score as the actual snapshot.
- 2026-11-03 — doc_id = sha256(cik, year, text)[:16], not a paragraph index — re-splitting never renumbers existing documents, so idempotency survives splitter changes; identical paragraphs repeated in one filing collapse to one document.
- 2026-11-03 — Raw EDGAR-CORPUS is streamed, never stored; only Item 1A paragraphs are kept in Parquet — 1.8 GB/year raw vs a few hundred MB of paragraphs; re-download is the price of a re-collect.
- 2026-11-03 — Filing years 2018, 2019, 2020 — recent, Item 1A mandatory throughout, matches the CLAUDE.md example.
- 2026-11-03 — Compose image tags pinned (postgres 16.15, redpanda v26.2.6, mlflow v3.17.2, prefect 3.9.1) — reproducible stack; MLflow and Prefect server versions must track the client versions in uv.lock.
- 2026-11-03 — Host ports 5433 (Postgres) and 5001 (MLflow) in compose — 5432 and 5000 are taken on the dev Mac by Homebrew postgresql@17 and AirPlay Receiver; container ports unchanged.
- 2026-11-03 — Corpus is SEC 10-K Item 1A paragraphs (EDGAR-CORPUS), not Enron email — cleaner data, bigger, framed as a regulatory sweep; paragraphs keep the review shape and low richness.
- 2026-11-03 — Python only; no .NET component — simplicity; .NET is read, not written, for interview prep.
- 2026-11-03 — Prefect over Airflow — Python-native, runs in one container locally; Airflow understood from docs.
- 2026-11-03 — Spark (phase 5) and Pulumi (phase 6) are optional — cut first if time runs short.
- 2026-11-03 — Azure only in phase 6, torn down same day — cost ceiling ~$10 for cloud, ~$40 total.
- 2026-11-03 — Repo is public; secrets only in `.env` — learning artifact meant to be shown.
