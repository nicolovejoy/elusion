# The systems in elusion, by phase

Plain-language map of every moving part, when it enters, and what it is for.
`docs/ARCHITECTURE.md` has the data-flow detail; this page is the mental model.

## Local (phases 0 to 5): one laptop, two layers

```
                 YOUR LAPTOP
 ┌───────────────────────────────────────────────────────────────────┐
 │  Python layer (runs natively, `uv run elusion ...`)                │
 │                                                                    │
 │   collect ──▶ review ──▶ judge ──▶ label ──▶ evaluate              │
 │   (phase 1)   (phase 2)  (phase 3) (phase 3)  (phase 3)            │
 │                                                                    │
 │   Prefect flows wrap each step: runs, retries, task states.        │
 │   Anthropic SDK: Haiku 5.5 scores (review), Opus 5.5 labels (judge)│
 └──────────────┬──────────────┬──────────────┬───────────────────────┘
                │              │              │
     localhost:5433     localhost:9092    localhost:4200   localhost:5001
                │              │              │                 │
 ┌──────────────▼──────────────▼──────────────▼─────────────────▼───┐
 │  Container layer (OrbStack runs Docker; compose.yaml starts 4)    │
 │                                                                    │
 │   Postgres        Redpanda          Prefect server    MLflow       │
 │   documents,      topic of doc ids  records every     phase 4:     │
 │   scores, jobs    + one bookmark    flow run and      runs, params │
 │                   per consumer      task state        metrics,     │
 │                   group                               model registry│
 └────────────────────────────────────────────────────────────────────┘
          ▲
          │  Parquet files in data/ hold the paragraph text (1.6 M rows)
          │  data/gold/<criterion>.csv holds labels (never in a prompt)
```

Quit OrbStack and the whole container layer powers off. Volumes keep the data.

## How a review run moves through it

```
 Redpanda topic  ──take 50 ids──▶  review flow  ──texts──▶  Parquet dict
      ▲                                │
      │ commit offset                  │ Haiku: score 0-4 + quote
      │ (only after insert)            ▼
      └──────────────────────── Postgres scores  (PK doc_id, model, prompt_version)
```

Kill it anywhere: the bookmark only moves after the insert, and the insert
skips ids already scored for that (model, prompt version). No token is spent twice.

## Phase 3 adds the gold set

```
 scores (v2) ──▶ two piles ──▶  flagged (score ≥ 2): judge labels all
                            └▶  discard (score < 2): judge labels 200 at random
                                        │
                   Opus 5.5 judge ──────┘      you: 20 blind labels (audit)
                                        │
                                        ▼
                          data/gold/cloud-concentration.csv
                                        │
                                        ▼
            evaluate ──▶ precision, elusion (measured) ──▶ recall, richness (derived)
                         each with a Clopper-Pearson 95 % interval ──▶ docs/reports/*.md
```

## Phase 4: the leaderboard

MLflow starts earning its keep. Every review + evaluate run logs its parameters,
metrics and the report file. A second provider (OpenAI) and a local model (Ollama)
sit behind the same `Provider` interface. The best run is registered and promoted.

## Phase 5 (optional): Spark

A PySpark MinHash job finds near-duplicate boilerplate across companies and years
and writes clusters to a Delta table, so review scores one representative per cluster.

## Phase 6: the same thing, in Azure, for one afternoon

```
   Terraform ──creates──▶  AKS cluster (Azure-hosted Kubernetes)
                                │
   Helm chart ──installs──▶     │  Postgres, Redpanda, Prefect server,
   (compose.yaml, translated)   │  and N review workers sharing one
                                │  consumer group
                                ▼
                     one real review run, then `terraform destroy`
```

- **Terraform** turns cloud resources into code: plan, apply, destroy. You run it.
- **Kubernetes** keeps a declared set of containers running across machines,
  restarting and rescheduling as needed. AKS is Azure's managed version.
- **Helm** packages the Kubernetes YAML as templates plus a values file, so
  install, upgrade and rollback are one command each.
- **Pulumi** (optional) is Terraform's job written in Python.

## Why a broker and not a query

With several workers in one consumer group, Redpanda splits the topic's partitions
among them: each id goes to exactly one worker, and a dead worker's share moves to
the others. Three workers running `select ids not yet scored` would grab the same
rows. Redpanda rather than Kafka because it is one binary with the consensus built
in (Kafka historically needed a separate ZooKeeper cluster for that), so it fits in
one small container. The protocol is identical.
