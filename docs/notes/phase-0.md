# Phase 0: Scaffolding

## What I built

A `uv` project with a Typer CLI (`elusion`), ruff and pytest wired up, the package skeleton
from CLAUDE.md, and a pinned four-service compose stack: Postgres 16.15, Redpanda v26.2.6,
MLflow 3.17.2 and Prefect 3.9.1, with the MLflow and Prefect servers matched to the client
versions in `uv.lock`. Demo: `docker compose up -d && uv run pytest && open
http://localhost:5001 http://localhost:4200`.

## What the tool is actually for

- uv: one binary that owns the interpreter, the virtualenv and the lockfile, so `uv run`
  is reproducible on any machine without activating anything.
- Typer: turns annotated functions into subcommands; the CLI is a thin shell over library code.
- Redpanda: a Kafka-protocol broker in one process, so the `documents.ingested` topic can
  exist locally without a JVM or ZooKeeper.
- MLflow server: a shared ledger of runs (params, metrics, artifacts) behind HTTP, so
  comparisons survive the shell session that produced them.
- Prefect server: the state store and UI for flows; the flows themselves run wherever
  Python runs and report back.

## What a production e-discovery platform does differently, and why

Each of these four would be a managed service or a cluster, not a container: Postgres with
backups and read replicas, a multi-broker Kafka with replication factor 3, MLflow backed by
object storage and a real database, Prefect Cloud or a server with workers on Kubernetes
(phase 6 gets there). Images are pinned by digest, not tag, and nothing listens on a
developer's laptop ports. The reason is defensibility: a review's lineage has to survive
machine loss and be auditable years later.

## The question I would ask their team

Do they run one orchestration and tracking plane per matter (isolation for privilege and
conflicts), or one shared plane with tenancy in the data layer, and what pushed them there?
