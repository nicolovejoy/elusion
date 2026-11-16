# Data architecture

How data moves through elusion, what each store is for, and why. Phases 0 to 4 and 6 are
built; phase 5 is not, and appears only as a planned row in the reads-and-writes table.

## One line

A paragraph of a 10-K risk-factor section is the document. Collect turns ~20,000 filings
into 1.6 million such documents; review scores each one against a written criterion with
an LLM; evaluate measures how well that went and says so with confidence intervals.

## The stores and their jobs

| Store | What lives there | Shape | Why this store |
|---|---|---|---|
| Parquet files, `data/corpus/year=YYYY.parquet` | the full text of every document, plus id, cik, company, year, position | columnar, write-once per year, ~120 MB/year | large, cold, scanned in bulk by analytics and Spark; read one column without touching the rest |
| Postgres, `documents` table | one row of metadata per document, no text | row store, indexed by doc_id, cik, year | small, hot, joined against scores and job state |
| Postgres, `scores` and job tables (phase 2) | the LLM's verdict per (document, model, prompt version); which batches have run | row store, keyed by (doc_id, model, prompt_version) | the idempotency ledger: "has this been paid for already?" |
| Kafka topic `documents.ingested` (Redpanda) | one message per document id | append-only log, keyed by doc_id, 1 partition | a durable to-do list that decouples collect from review |
| `data/gold/<criterion>.csv` and `.judge-runs.jsonl` (phase 3) | one yes/no label per (document, labeler): the judge model's or the owner's; one line per judge run with counts and cost | CSV and JSON lines, appended, gitignored | the answer key for evaluation; nothing that builds a prompt ever reads it |
| MLflow (phase 4) | one run per review+evaluate: params, metrics, cost, report | experiment tracking | compare runs across models and prompts; promote a winner |
| Prefect server | state of every flow and task run, retries, logs | bookkeeping | see what ran, what failed, what retried |

Rule of thumb: Parquet holds what is large and rarely changes; Postgres holds what is
small and changes as work happens; Kafka holds what is waiting to be done; MLflow holds
what we learned from each attempt.

## Phase 1: collect (built)

```
HuggingFace Hub                      laptop process (Prefect flow `collect`)
┌──────────────────────┐
│ eloukas/edgar-corpus │   stream    ┌──────────────────┐
│ 2019/train.jsonl     │ ──────────▶ │ iter_filings()   │  one JSON line = one 10-K,
│ 2019/test.jsonl      │  HTTPS,     │ keep section_1A  │  all 20 sections as strings;
│ 2019/validate.jsonl  │  line by    └────────┬─────────┘  only Item 1A is kept
└──────────────────────┘  line                │
   ~1.8 GB/year, never                        ▼
   written to disk                   ┌──────────────────┐
                                     │ split_paragraphs │  split on newline,
                                     │ min 30 words     │  drop headings/bullets
                                     └────────┬─────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │ doc_id =         │  stable across re-runs
                                     │ sha256(cik,year, │  and splitter changes;
                                     │        text)[:16]│  repeats inside one
                                     └────────┬─────────┘  filing collapse to one
                                              │
                 ┌────────────────────────────┼────────────────────────────┐
                 ▼                            ▼                            ▼
   ┌─────────────────────────┐  ┌──────────────────────────┐  ┌────────────────────────┐
   │ Parquet                 │  │ Postgres `documents`     │  │ Kafka                  │
   │ year=2019.parquet       │  │ doc_id PK, cik, company, │  │ documents.ingested     │
   │ doc_id, cik, company,   │  │ year, paragraph_index,   │  │ key=doc_id             │
   │ year, paragraph_index,  │  │ word_count, ingested_at  │  │ value={doc_id, year}   │
   │ text                    │  │ ON CONFLICT DO NOTHING   │  │ acks=all, flush()      │
   │ rewritten each run      │  │ (re-run inserts 0)       │  │ (re-run re-publishes)  │
   └─────────────────────────┘  └──────────────────────────┘  └────────────────────────┘
          the corpus                     the index                   the to-do list
```

What the three years produced:

| | 2018 | 2019 | 2020 |
|---|---|---|---|
| filings | 6,885 | 6,693 | 6,851 |
| filings with no Item 1A | 1,624 | 1,535 | 1,475 |
| documents (paragraphs) | 491,046 | 513,266 | 599,703 |
| Parquet size | 111 MB | 117 MB | 135 MB |
| wall-clock | ~5 min | ~5 min | ~6 min |

Filings without an Item 1A are mostly smaller reporting companies, which the SEC exempts
from the risk-factor requirement.

### Keys

- `cik`: the SEC's permanent numeric id for a filer. The company key. Names are
  decoration and come from a separate SEC lookup, only when `ELUSION_SEC_USER_AGENT` is set.
- `doc_id`: 16 hex chars of `sha256(cik, year, text)`. The join key between every store.
  Hashing the text, not the position, means fixing the splitter later does not renumber
  documents that already have scores. The position is kept as `paragraph_index` for
  humans.
- `(doc_id, model, prompt_version)`: the key of a score (phase 2). Same document, new
  model or new prompt, new score; same triple, never re-spent.

### Idempotency, per store

- Parquet: the year's file is replaced whole. Re-running produces the same file.
- Postgres: insert with `ON CONFLICT DO NOTHING` on `doc_id`, with `RETURNING` so the
  count of genuinely new rows is honest.
- Kafka: messages are keyed by `doc_id`. A re-run appends the same keys again; the log
  does not dedupe on its own. Consumers must be idempotent (they check the scores table)
  or the topic must be compacted. Review (phase 2) does the former.

## Phase 2: review

```
Kafka documents.ingested ──▶ consumer, batches of N ids
                                      │
                                      ▼
                         Postgres: which of these ids lack a score for
                         (model, prompt_version)?  ──▶ skip the rest   ← idempotency
                                      │
                                      ▼
                         Parquet: fetch text for the remaining ids
                                      │
                                      ▼
                         LLM call (pinned model snapshot + prompt version)
                         rate limited, 429 backoff, token + cost counters,
                         refuses to start if projected cost > ELUSION_MAX_RUN_USD
                                      │
                                      ▼
                         Postgres `scores`: doc_id, model, prompt_version,
                         score 0-4, rationale, quote, tokens, cost, ts
```

### Why Kafka at all, when the Postgres query already finds unscored ids

The query is the correctness mechanism: it is what guarantees no double spend. The
topic is the operational mechanism, and it buys four things the query does not:

1. Decoupling in time. Collect finished at 12:45 today; review does not exist yet. The
   ids wait on the log. Collect never needed to know who consumes them or when.
2. Progress without a scan. A consumer group remembers its offset. "Where was I" is a
   number, not a query over 1.6 million rows every batch. Resume after a crash is
   automatic.
3. Fan-out. A second consumer (dedupe in phase 5, an audit trail, a metrics counter)
   reads the same log independently without touching the database or the first
   consumer.
4. Back-pressure and batching. The consumer pulls at the rate the LLM provider allows;
   the producer is never blocked by it.

In a 20,000-document toy you could skip it. In a production platform where collectors,
reviewers and analytics are separate services owned by separate teams, the log is the
contract between them. That is what the exercise is practising.

## Phase 3: judge, label, evaluate (built)

```
Postgres `scores` for one (review model, prompt version) pair
        │  select_gold: every score >= 3 (flagged), plus 200 drawn
        │  with a seeded RNG from score < 3 (discard)
        ▼
Prefect flow `judge` ──▶ LLM call (pinned judge model + judge prompt version,
        │                effort low; paragraph text only: no score, rationale,
        │                quote, company or existing label); spend guard as in review
        ├──▶ data/gold/<criterion>.csv               one row per (doc_id, labeler),
        │                                            labeler = judge model id
        └──▶ data/gold/<criterion>.judge-runs.jsonl  one line per judge run: counts, cost

elusion label (owner, blind, 10 flagged + 10 discard) ──▶ same CSV, labeler = human
        the gold set never enters any prompt; evaluation only
        ▼
Prefect flow `evaluate`: piles recomputed from `scores`, joined to the judge rows
  flagged pile, every member judged     ──▶ precision          (census)
  discard pile, seeded sample judged    ──▶ elusion            (measured, not derived)
  both piles weighted by pile size      ──▶ richness, recall   (derived, >= ~90% coverage)
  human rows ⋈ judge rows               ──▶ judge agreement with the owner
  review_jobs, judge-runs ledger        ──▶ cost, wall-clock
  `documents`, flagged judged relevant  ──▶ companies found
  grades for the pair (if any)          ──▶ rationale sound, quote verbatim, sound x label
        Clopper-Pearson 95% intervals and the k/n behind them
        ▼
docs/reports/<criterion>-<model>-<version>-<filing years>.md (committed)
        ▼
MLflow run in experiment elusion/<criterion>, named <review model>/<prompt version>:
  params, metrics (point/low/high/k/n per metric, costs), artifacts (report, review
  prompt, criterion file); never a gold row, a grade row or a label
```

Rationale grading (phase 4) sits beside the judge and feeds evaluate:

```
Postgres `scores`, the pair's flagged docs that have a rationale
        ▼
Prefect flow `grade` ──▶ LLM call (judge model + grade-v1: paragraph plus the reviewer's
        │                score, rationale and quote; never a label); spend guard as in review
        ├──▶ data/grades/<criterion>.csv               one row per (doc, review model,
        │                                              prompt version, grader); quote_verbatim
        │                                              checked in Python, not by the model
        └──▶ data/grades/<criterion>.grade-runs.jsonl  one line per grade run, per pair: counts, cost
```

The registry (phase 4) turns one evaluated configuration into the one the review flow runs:

```
evaluate's MLflow run ──▶ logged model `reviewer` (pyfunc): reviewer.json manifest (provider,
        │                 pinned model id, prompt version, criterion, threshold, generation
        │                 settings) + the review prompt file; never gold or grades
        ▼
elusion promote --run <id> ──▶ registered model `elusion-reviewer`, new version N,
                                alias `champion` moved to N
        ▼
elusion review --reviewer elusion-reviewer@champion
        resolves the manifest from the registry, then runs the ordinary review flow with
        that provider, model and prompt version: same (model, prompt version) key in
        `scores`, same Kafka consumer group, so already-scored documents cost nothing
```

Promotion changes which configuration `--reviewer` resolves to. It does not change a
score, the `scores` key, or the model behind a provider's id.

## Phase 6: the same flows on AKS (built)

Phase 6 runs the phase 1 and 2 flows, the same code, on a rented Kubernetes cluster, once,
and removes the cluster the same day. The data model gains one nullable column,
`review_jobs.run_env`, which records where a review ran (`local` by default, the cluster's
name on AKS). Otherwise what is new is where the pieces run and how a run is started. A
few terms first. AKS (Azure Kubernetes Service) is Azure's managed Kubernetes, the
counterpart of AWS EKS; Azure runs the control plane and we rent the worker machines
(nodes). A pod is one or more containers that Kubernetes runs together on one node.
Terraform declares Azure resources in files and creates or destroys them from those
files. A Helm chart is a template of Kubernetes manifests (the YAML that describes pods
and volumes) plus a values file; `helm install` renders it and applies the result. A
Kubernetes Job is a pod that runs once to completion.

What exists, and who made it:

```
Terraform (infra/terraform), run by the owner: 10 resources in Azure, West US 2
  resource group rg-elusion ─ AKS cluster aks-elusion (2 x Standard_D2as_v7, free control plane)
  │                            └─ AKS creates a second group, MC_rg-elusion_aks-elusion_westus2,
  │                               for the node machines, disks and networking
  ├─ container registry ........... holds the image (AWS: ECR)
  ├─ storage account + container `corpus` ... holds the Parquet (AWS: S3 bucket)
  ├─ 3 role assignments ........... the nodes may pull the image and use the container;
  │                                 the owner may use it too; no account key is used
  └─ 2 budgets of $10 ............. one on each resource group, with an email alert

Helm chart (infra/helm/elusion), `helm install` by the owner: the cluster's contents
  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐ ┌─────────────────────┐
  │ postgres-0   │ │ redpanda-0   │ │ prefect-server-0 │ │ prefect-worker      │
  │ documents,   │ │ topic        │ │ flow-run state,  │ │ polls the work pool │
  │ scores,      │ │ documents.   │ │ the work pool,   │ │ `elusion`, starts a │
  │ review_jobs  │ │ ingested     │ │ two deployments  │ │ Job per flow run    │
  └──────────────┘ └──────────────┘ └──────────────────┘ └─────────────────────┘
  + the volume `corpus` (the blob container, mounted), the settings ConfigMap, and a
    hook Job that registers the work pool and the two deployments after every
    install and upgrade
  Two Secrets are named by the chart and created by hand: the OpenAI key, the Postgres
  password. Nothing is exposed to the internet; the owner reaches the cluster with
  `kubectl port-forward`.
```

How one flow run happens:

```
owner's laptop                               AKS cluster
prefect deployment run review/review ──▶ prefect-server-0: a flow run is created and
  (through port-forward to :4200)            put on the work pool `elusion`
                                                         │ polled by
                                                         ▼
                                          prefect-worker: fills the pool's job template
                                                         │ creates
                                                         ▼
                                          Kubernetes Job (backoffLimit 0, never retried)
                                          └─ pod from the elusion image, same code as the laptop
                                             ├─ /data/corpus  ◀── corpus volume (blob container)
                                             ├─ Kafka, Postgres ◀─ the cluster's own services
                                             └─ OpenAI call: gpt-6-luna, prompt v2, pinned
                                                         │
                                                         ▼
                                          cluster Postgres `scores`, `review_jobs`
                                          (`run_env` = aks-elusion on every job row)
```

The work pool is the server-side queue the worker watches. The worker is a small
long-running process; it never runs the flow itself. For each flow run it creates a Job
from the pool's job template, which is how a batch review becomes a service on
Kubernetes: a Job per run, not a server waiting for requests. `backoffLimit: 0` and
Prefect's `crash` behaviour on SIGTERM keep a killed pod from being retried by Kubernetes
or by Prefect, because a retry of `sample=20000` would score 20,000 more documents, not the
remainder (DECISIONS 2026-11-15).

The corpus is a directory, as it is on the laptop. On AKS that directory is the blob
container, mounted into each pod by the cluster's Blob CSI driver (CSI is Kubernetes's
plugin interface for storage), signed in with the nodes' managed identity (an identity
Azure gives a resource so it can be granted roles without a key or password, the
counterpart of an instance's IAM role). `collect` wrote the 2020 Parquet file (599,703
paragraphs, 135,306,583 bytes) into it on the cluster in 5 minutes 28 seconds, and
`review` read it back.

How the scores came home:

```
cluster Postgres ── infra/export.sh ──▶ data/exports/STAMP/{scores,review_jobs}.csv
  COPY ... TO STDOUT; row counts checked against the table before and after; SHA-256 sums
        │
        ▼ terraform destroy: 10 destroyed, the same day; the files on the laptop remain
        │
        ▼ COPY ... FROM (header match), one transaction, at home
local Postgres `scores` (+20,000 rows), `review_jobs` (+3)      ← the local ledger now knows
        │                                                          every paragraph paid for
        ▼ judge, grade, evaluate --year 2020 --review-model gpt-6-luna
docs/reports/cloud-concentration-gpt-6-luna-v2-2020.md   its own population, by filing year
```

The cluster scored the first 20,000 paragraphs of filing year 2020 in ingestion order, so
the report describes those 20,000, not a random draw from the year. It reads: precision
0.485 [0.360, 0.611] (32/66 flagged, Clopper-Pearson 95%), elusion 0.000 [0.000, 0.018]
(0/200 sampled discards), recall not established ([0.061, 1.000], derived from the two
pile intervals). The 2018 and 2019 reports are untouched; `--year` is what keeps the two
populations apart.

### Why a cluster, and why it is built this way

The laptop already ran the whole pipeline, so a cluster adds no capability. The point is
to run the same code where production runs: images in a registry, state in services
rather than in the compose file, secrets outside the repo, and a bill. Three choices
follow from running it for one day. Postgres, Redpanda and the Prefect server are pods
from the chart, as in `compose.yaml`, because managed databases and Event Hubs would add
resources to create and destroy, and pods from a chart are the most Helm and Kubernetes
practice (DECISIONS 2026-11-15). The state that matters leaves the
cluster before it is destroyed (the export), because everything in it dies with it.
Everything in Azure and in the cluster is declared in a committed file, because a
resource made by hand is one that `terraform destroy` does not know about. The two
Secrets are the one exception, since their values cannot be in a file.

## Where each phase reads and writes

| Phase | Reads | Writes |
|---|---|---|
| 1 collect | HuggingFace JSONL | Parquet, `documents`, Kafka |
| 2 review | Kafka, `documents`, `scores`, Parquet text | `scores`, job table |
| 3 judge, label, evaluate | `scores`, `review_jobs`, `documents`, Parquet text, `data/gold/` | `data/gold/<criterion>.csv`, `.judge-runs.jsonl`, `docs/reports/` |
| 4 leaderboard | report, scores, prompt file, `data/grades/`, MLflow registry | MLflow run (params, metrics, report, prompt, `reviewer` model); registry versions and alias; `data/grades/<criterion>.csv`, `.grade-runs.jsonl` |
| 5 dedupe (planned) | Parquet | Delta table of clusters; `scores` for members |
| 6 production | same as 1 and 2, in the cluster: HuggingFace JSONL, the cluster's Kafka, `documents`, `scores`, Parquet text on the blob mount | the cluster's Postgres, Kafka and blob container; then `scores` and `review_jobs` copied to `data/exports/` and into the local Postgres |

## Local services

| Service | Host port | Container port | Used by |
|---|---|---|---|
| Postgres 16 | 5433 | 5432 | collect, review, evaluate |
| Redpanda (Kafka) | 9092 | 9092 external, 29092 internal | collect (produce), review (consume) |
| MLflow 3.17 | 5001 | 5000 | leaderboard |
| Prefect 3.9 | 4200 | 4200 | every flow |
| Ollama 0.40 (Homebrew service, not compose) | 11434 | — | review with a local model (phase 4) |

Host ports 5432 and 5000 are taken on the dev Mac by Homebrew Postgres and AirPlay.
