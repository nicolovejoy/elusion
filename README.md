# elusion

Elusion is a project I cooked up with Claude to get up to speed for work I may be doing at
Relativity with my friend Pierre. I want to understand the kind of systems being envisioned
for what we might call "agentic discovery": review done by models, with the paperwork a
court would ask for. We are doing it by building a miniature of those systems with the tools
Relativity's engineering postings name: Parquet, Kafka, Spark, Delta Lake, Kubernetes and
Azure, plus Postgres. Two stand-ins: Prefect for orchestration, where Relativity uses
Temporal, and MLflow for the experiment record, which no posting names. The product posting
whose vocabulary I use throughout is Director of Product Management, Data Intelligence
Foundation, req 26-0360 (URL to come). Its five data primitives: Files/Natives,
Knowledge/Metadata, Query Plane, Ontology/Relationship, Data Capabilities.

Instead of a real matter, the corpus is public: the Risk Factors section (Item 1A) of every
10-K annual filing for 2018, 2019 and 2020, from the EDGAR-CORPUS dataset on HuggingFace.
That is about 20,000 filings and 1.6 million paragraphs. Each paragraph is treated as one
"risk factor" document. The review so far covered the first 17,936 of them, in ingest order.
The criterion for our first question is "does this company say it depends on one or a few
cloud providers", which is the kind of yes/no question a review team gets handed.

**Elusion**, the name, is the e-discovery metric for what a review missed: the rate of
relevant documents inside the pile the system discarded, measured by sampling that pile.

## The phases

**Phase 1, collection.** The full text of each risk factor was stored once in Parquet,
because it is columnar and compressed (347 MB against 5.4 GB of raw JSON) and Spark, DuckDB
and pandas read it directly. One row per paragraph went into Postgres, with company, year
and position, because that is what gets filtered, joined and later updated with scores.
Every document id was published to a Kafka topic, so a reviewer written weeks later could
consume the list and pick up where it stopped without re-reading the files. In the posting's
terms, Parquet is Files/Natives, Postgres is Knowledge/Metadata, and Kafka is the ingestion
side of the Query Plane.

**Phase 2, first-pass review.** A cheap model, claude-haiku-5-5, read every paragraph and
scored it 0 to 4 against the criterion, with a one-sentence rationale and a quote. Every
score records which model snapshot and which prompt version produced it, and a re-run never
pays for a document already scored under that pair. A run killed partway through resumes.

**Phase 3, second opinion.** A stronger model, claude-opus-5-5, checked every flagged
paragraph and a random 200 from the discard pile. I blind-labeled 20 of those myself to
audit the checker (I am new to this task, so the audit is small). That gave the four
numbers a court likely asks about: precision, recall, elusion and richness, each with a
Clopper-Pearson 95% interval. This is the Data Capabilities pillar: the result comes with
its own evidence.

**Phase 4, comparison.** Five reviewers ran over the same paragraphs: Haiku with three
prompt versions, OpenAI's small model gpt-6-luna, and a free local model, qwen2.5:14b, on my
Mac. Each was measured against the same second opinion. Every evaluation was logged to
MLflow, which here plays two roles: a lab notebook, where each attempt sits beside the
others with its parameters, metrics and report, and a shelf, the model registry, where the
best configuration is stored as `elusion-reviewer` with a movable label, `champion`, that a
review run can be pointed at by name. A new check asked whether each reviewer's stated
reason matched the paragraph it quoted. Finding: all five precision intervals overlap, so
the winner (gpt-6-luna, 0.51) is a choice, not a proof, and the local model invented quotes
in four of its 56 flags. Details in `docs/notes/phase-4.md` and `docs/reports/`.

**Next.** Phase 5 clusters near-duplicate paragraphs with Spark (MinHash), so one review
covers a whole cluster; risk-factor boilerplate repeats across companies and years, and
dedupe before review is what every e-discovery platform does. It runs locally and costs
nothing but time. In the posting's terms, it is the first Ontology relationship: "this
paragraph is a near-copy of that one". Phase 6 is one real run on Azure Kubernetes, built
and torn down the same day, roughly a dollar an hour, under 20 dollars. Both are optional.

## Status

Phases 0 to 3 are done. Phase 4 has its code, its five runs and its reports in; the phase
note is still to come. See `docs/PHASES.md`.

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
