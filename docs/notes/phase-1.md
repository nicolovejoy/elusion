# Phase 1: Collection

## What I built

A Prefect flow `collect` that streams one filing year of EDGAR-CORPUS from the HuggingFace
Hub, keeps Item 1A, splits it into paragraphs of 30+ words, and writes them to Parquet,
Postgres and a Kafka topic, idempotently by a text-hash document id.
Demo: `uv run elusion collect --year 2019 --limit 50`, then open http://localhost:4200.

Numbers for 2018, 2019, 2020 (one run, 15 minutes wall-clock, three Prefect flow runs):

- filings: 6,885 / 6,693 / 6,851; without an Item 1A: 1,624 / 1,535 / 1,475
- paragraphs: 491,046 / 513,266 / 599,703; total 1,604,015 rows in Postgres
- Parquet: 111 / 117 / 135 MB, zstd; `data/` totals 347 MB against 5.4 GB of raw JSONL
- per year: ~3.5 min streaming and splitting, ~20 s Parquet, ~45 s Postgres, ~20 s Kafka
- duplicates dropped (same paragraph repeated inside one filing): 290 / 327 / 2,429

## What the tool is actually for

- Prefect: a function decorator that gives each step state, retries and a UI row, while
  the code keeps running in your own process. It is bookkeeping for a pipeline, not a
  scheduler you have to deploy into.
- Parquet: columnar, typed, compressed storage that any engine (pandas, DuckDB, Spark)
  reads directly. It bought a 15x size reduction over the source and the ability to read
  one column without touching the others.
- Kafka (Redpanda): a durable log of keyed messages that decouples the producer from
  consumers that have not been written yet. The 1.6 M ids sit on the topic until phase 2
  reads them.

## What a production e-discovery platform does differently, and why

Ingestion is a service with a queue in front of it, not a CLI run from a laptop: documents
arrive continuously from collectors, get a content hash and a chain-of-custody record on
the way in, and land in object storage with the index in a database that is backed up.
Deduplication is by content hash across the whole matter, not within one filing. Parquet
would be partitioned by matter and date, and Kafka would have replication and retention
policies, because losing an ingested document is a defensibility problem, not a bug.

## The question I would ask their team

How do they version the splitter or chunker? When the paragraph boundaries change, what
happens to the review decisions already attached to the old boundaries?
