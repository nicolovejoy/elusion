# elusion

A small document-review pipeline over public SEC 10-K risk-factor paragraphs. It finds the
paragraphs that match a written criterion and reports how much it found and how sure it is:
recall, precision and elusion, each with a confidence interval. It was built to learn
production ML tooling (Prefect, MLflow, Spark, AKS, Terraform/Pulumi) through one realistic
job instead of a tour of the tools.

**Elusion** is the e-discovery metric for what a review missed. It is the rate of relevant
documents inside the pile the system discarded, measured by sampling that pile.

## Status

Phases 0 to 3 are done: collection, first-pass LLM review, validation. Phase 4 (MLflow
leaderboard) is in progress: OpenAI and local Ollama providers, prompt v3 and rationale
grading are built, and four of the five planned runs are in. See `docs/PHASES.md`.

## First result

Criterion: *discloses reliance on a single cloud, hosting or data-center provider, or on a
small number of such providers, as a material risk* (`criteria/cloud-concentration.md`,
prompt v2). claude-haiku-5-5 scored 17,936 paragraphs, taken in ingest order rather than
drawn at random, so the figures describe those paragraphs, not the whole corpus.
claude-opus-5-5 labeled the gold set; the owner audited 20 of the judge's labels blind.
Clopper-Pearson 95% intervals:

- precision 0.333 [0.229, 0.452], 25 of 75 flagged paragraphs relevant
- elusion 0.000 [0.000, 0.018], 0 of 200 sampled discards relevant
- recall: **not yet established.** The interval is [0.05, 1.00]. Zero hits in 200 discards
  still allows up to about 320 missed paragraphs, against 25 found. Narrowing that bound
  to under 25 misses (a recall of at least about 50%) needs about 2,500 discard labels.
  That sample has not been run. The report prints a recall point estimate of 1.000; that is
  an artefact of zero hits, and only the interval carries information.
- judge/owner agreement 0.80 [0.56, 0.94], 16 of 20

Full report: `docs/reports/cloud-concentration-claude-haiku-5-5-v2.md`. The reasoning,
including where the judge and the owner disagreed: `docs/notes/phase-3.md`.

## What the pipeline guarantees

- Every LLM call carries a pinned model snapshot and a prompt version, stored with the score.
- Idempotent by document id: a re-run never pays again for a (document, model, prompt
  version) that already has a score.
- Resumable: a run killed partway through picks up where it stopped. This was tested by
  killing a run, in `docs/notes/phase-2.md`.
- Cost-bounded: a run whose projected cost exceeds `ELUSION_MAX_RUN_USD` is refused before
  any call is made.
- The gold set is never put in a prompt, an example or a retrieval context.

`docs/DEFENSIBLE-REVIEW.md` explains on one page why each guarantee matters.

## Run it

```
cp .env.example .env     # add your Anthropic key and an SEC contact user agent
docker compose up -d     # postgres (:5433), redpanda (:9092), mlflow (:5001), prefect (:4200)
uv sync
uv run elusion collect --year 2018 --year 2019 --year 2020
uv run elusion review --criterion criteria/cloud-concentration.md --prompt-version v2 --sample 20000
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v2
uv run elusion label --criterion criteria/cloud-concentration.md --prompt-version v2 --n 20
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2
uv run pytest
```

The run behind the result above cost $2.05 in API spend: $1.34 for the review, $0.71 for
the judge.

## Where to read

- `docs/PROJECT.md`: goals, the matter, what done means
- `docs/ARCHITECTURE.md`, `docs/SYSTEMS.md`: data flow and the role of each store
- `docs/notes/phase-N.md`: what each phase built and taught
- `docs/DECISIONS.md`: one dated line per decision
- `CLAUDE.md`: layout and working rules

## Data

[EDGAR-CORPUS](https://huggingface.co/datasets/eloukas/edgar-corpus) on HuggingFace holds
10-K filings from 1993 to 2020 with each item pre-extracted. This project uses Item 1A (Risk
Factors) from 2018, 2019 and 2020, split into 1,604,015 paragraphs. The data is public and
needs no account.

## License

MIT.
