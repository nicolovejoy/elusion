# Decisions

One line each, newest first. Format: `YYYY-MM-DD — decision — why`.

- 2026-11-03 — Corpus is SEC 10-K Item 1A paragraphs (EDGAR-CORPUS), not Enron email — cleaner data, bigger, framed as a regulatory sweep; paragraphs keep the review shape and low richness.
- 2026-11-03 — Python only; no .NET component — simplicity; .NET is read, not written, for interview prep.
- 2026-11-03 — Prefect over Airflow — Python-native, runs in one container locally; Airflow understood from docs.
- 2026-11-03 — Spark (phase 5) and Pulumi (phase 6) are optional — cut first if time runs short.
- 2026-11-03 — Azure only in phase 6, torn down same day — cost ceiling ~$10 for cloud, ~$40 total.
- 2026-11-03 — Repo is public; secrets only in `.env` — learning artifact meant to be shown.
