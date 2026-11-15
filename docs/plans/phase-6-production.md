# Phase 6 spec: production in Azure

Status: designed 2026-11-15 and checked once by a fresh reader; not started, awaiting the
owner's approval. Read
`docs/ARCHITECTURE.md`, the Phase 6 checklist in `docs/PHASES.md` and `compose.yaml` first.
Hard rules in `CLAUDE.md` apply to every line below, in particular: no Azure resource is
created except by the owner running `terraform apply` themselves. The phase 4 spec
(`docs/plans/phase-4-leaderboard.md`) is the pattern this one follows.

## Intent

Phases 1 to 4 run from a laptop: four containers from `compose.yaml` and a CLI. Phase 6
runs the same review once on a rented Kubernetes cluster in Azure, described entirely in
files, and removes it the same day. The point is to learn what each layer is for by using
it, not to keep anything running.

The terms, once:

- **Kubernetes** runs containers on a group of machines (*nodes*). You describe what
  should exist in *manifests* (YAML), and it keeps reality matching them. A *pod* is one
  running instance of a container; a *Deployment* keeps N copies of a pod alive; a *Job*
  runs a pod to completion once; a *Service* gives pods a stable name on the cluster
  network; a *Secret* holds values such as API keys apart from the manifests that use them;
  a *PersistentVolumeClaim* asks for a disk that outlives a pod.
- **AKS** (Azure Kubernetes Service) is Azure's managed Kubernetes: Azure runs the control
  plane, you pay for the nodes. The AWS counterpart is EKS.
- **ACR** (Azure Container Registry) stores the container image the cluster pulls. The AWS
  counterpart is ECR.
- **Blob storage** is Azure's object store; a *container* in a *storage account* is roughly
  an S3 bucket.
- **Terraform** turns a description of cloud resources into API calls, and keeps a *state
  file* recording what it created so that `terraform destroy` can remove exactly that.
- **Helm** packages a set of Kubernetes manifests as a *chart*: templates plus a
  `values.yaml` of settings. One chart installs on a laptop cluster and on AKS with
  different values, which is what makes a rehearsal possible.
- A Prefect **work pool** is a named queue of flow runs waiting for somewhere to execute;
  a **worker** is a process that polls one pool and starts each run. With a Kubernetes work
  pool the worker starts every flow run as a Job. A Prefect **deployment** (not the
  Kubernetes kind) is a flow registered with the server, with its image and default
  parameters, so that it can be started by name. Until now the flows ran in the CLI's own
  process and only reported to the server.

Three facts shape the design:

- A fresh Postgres on the cluster has no scores. Repeating the local 20,000-document
  sample there would pay again for documents that already have a score for the same
  (model, prompt version), which the idempotency rule forbids in spirit even though the
  two databases cannot see each other.
- Everything on the cluster is destroyed the same day, so anything worth keeping (scores,
  the job rows) has to leave it first, and the local ledger has to learn about it.
- Azure time costs money and the destroy must happen the same day. So everything that
  can be built and proven without Azure is, on the Kubernetes that OrbStack provides on
  the dev Mac. The Azure day is then a short runbook, not a debugging session.

## Decisions already made

Owner decisions, 2026-11-15 (`docs/DECISIONS.md`):

- Phase 6 comes before phase 5 and the new phase 7.
- Postgres, Redpanda and the Prefect server run as pods in the cluster, from the chart,
  mirroring `compose.yaml`. The Parquet text goes in the blob container. No managed Azure
  database, no Event Hubs.
- The Terraform module includes a $10 resource-group budget with an email alert; the
  address is a variable supplied at apply time and never committed.
- The region is West US 2.
- The cluster's scores are evaluated as their own population, by filing year, not merged
  into the champion's existing report. The owner left this to the controller's
  recommendation. The year filter gets a design addendum before task 9.

Controller's calls, 2026-11-15, for the owner to confirm or overturn. Each confirmed call
becomes a `DECISIONS.md` line.

1. **The cluster run scores a filing year that has no local scores.** Task 1 finds which
   of 2018, 2019 and 2020 has no row in the local `scores` table for any model. The
   cluster collects that year and reviews its first 20,000 ids with the champion
   configuration. Nothing is paid for twice, and no scores need to be copied into the
   cluster before the run. Task 1 was run on 2026-11-15 and the year is 2020.
2. **The champion is named by its parts, not resolved from the registry.** The run passes
   provider `openai`, model `gpt-6-luna` and prompt version `v2`, the values in
   `elusion-reviewer@champion` (DECISIONS 2026-11-07). `--reviewer` would need the MLflow
   registry, which lives in the local MLflow and is not deployed to the cluster. MLflow
   stays local; the review flow does not touch it otherwise.
3. **Scores come home.** Before the destroy, `scores` and `review_jobs` are dumped from
   the cluster's Postgres to `data/exports/` (gitignored) and loaded into the local
   Postgres, so the local ledger knows every document that has been paid for. How those
   scores are then evaluated was the owner's question 3, answered above: by filing year.
4. **Where a review ran is recorded.** `review_jobs` gains a nullable `run_env` column,
   set from a new `ELUSION_RUN_ENV` variable (default `local`; on the cluster, the AKS
   cluster name). The report's cost section lists the job count per environment. This is
   how "report notes the cluster name" in the checklist is met, and where a decision was
   made is part of its audit trail. `create_all` does not alter an existing table
   (DECISIONS 2026-11-03), so `ensure_schema` also runs an idempotent
   `ALTER TABLE review_jobs ADD COLUMN IF NOT EXISTS run_env text`.
5. **Rehearse on local Kubernetes first, at zero model cost.** The image and chart are
   proven on OrbStack's Kubernetes with `collect --limit 5` and a 50-document review
   through the local Ollama model (`qwen2.5:14b`, $0). The rehearsal's Postgres is thrown
   away; its 50 free scores are not kept.
6. **Flow runs are Kubernetes Jobs started by a Prefect worker.** One Kubernetes work
   pool, one worker Deployment, and two Prefect deployments (`collect`, `review`) that use
   the elusion image. This is the "review service + Prefect worker" of the checklist: the
   review is a batch job, so its service form is a Job per run, not a long-lived server.
   The worker needs the `prefect-kubernetes` package, which the elusion image does not
   have. It runs from Prefect's own Kubernetes image at the same pinned version (3.9.1),
   a fifth pinned image beside the four in `compose.yaml`; `pyproject.toml` is unchanged.
   The builder confirms the tag exists.
7. **Nothing is exposed to the internet.** Every Service is cluster-internal. The owner
   reaches the Prefect UI and Postgres with `kubectl port-forward`. No ingress, no
   public load balancer for the application.
8. **Secrets.** The chart never creates a secret from a value. It references two Secrets
   by name: the OpenAI key and the Postgres password. The owner creates both with
   `kubectl`, the key fed from 1Password through `op run --env-file=.env.tpl`, the
   password generated on the spot. No secret value appears in the chart, a values file,
   a `.tfvars` file, shell history or this conversation. Only `OPENAI_API_KEY` goes to the
   cluster; the Anthropic key is not needed there.
9. **Terraform state is local and gitignored** (`.gitignore` already covers
   `infra/terraform/*.tfstate*`). The subscription id comes from the environment
   (`ARM_SUBSCRIPTION_ID`), not from a committed file. `.terraform.lock.hcl` is committed.
10. **Nodes are two `Standard_D2s_v5`** (2 vCPU, 8 GB each, x86-64), control plane on the
    free tier, region West US 2. The dev Mac is arm64, so the image is built for both
    `linux/arm64` (rehearsal) and `linux/amd64` (AKS) and the AKS values pin it by digest.
11. **The corpus volume is a mount, not new code.** `load_texts` and `write_parquet` read
    and write a directory. On AKS that directory is the blob container mounted through the
    cluster's Blob CSI driver; on the laptop cluster it is an ordinary volume. No storage
    library is added to `pyproject.toml`.
12. **Pulumi stays optional and last**, after the phase note, and gets its own short spec
    if the owner wants it.
13. **The blob mount is proven first on the day, with a fallback.** OrbStack cannot
    rehearse the Blob CSI mount, so its first use is on paid time. The Azure day starts
    with `collect` at `limit=5` as a smoke test before the full year. If the mount cannot
    be made to work within an hour, the corpus volume becomes a managed disk for the run
    and the Parquet file is uploaded to the blob container afterwards with `az`. The blob
    container still ends up holding the corpus; the phase note says which path was taken.
14. **A flow-run Job never retries on its own.** `sample` counts ids pulled from the
    consumer group's committed offset, so a second run with `sample=20000` scores 20,000
    further documents, not the remainder, and at about $2 the spend guard would not stop
    it. The Job's retry count is zero. After a failure the next run is started by hand
    with the remainder: 20,000 minus the group's committed offset.

The three questions that were the owner's are at the end, with the answers.

## Goal

On the Azure day, from the repo root, the owner runs (exact commands are produced by the
tasks below and collected in `infra/README.md`):

```
terraform -chdir=infra/terraform apply
az aks get-credentials ...
kubectl create secret ...                      # twice, values from 1Password / generated
helm install elusion infra/helm/elusion -f infra/helm/elusion/values-aks.yaml
prefect deployment run collect/collect --param year=2020
prefect deployment run review/review --param criterion=cloud-concentration --param sample=20000 --param provider_name=openai --param model=gpt-6-luna --param prompt_version=v2
# export scores and review_jobs, verify the row count
terraform -chdir=infra/terraform destroy
```

At the end: about 20,000 new scores are in the local Postgres, their job row naming the
cluster in `run_env`; a report covering them names the cluster; the Azure resource group
no longer exists; the bill is under $10.

## Inputs already in place

- `flows/review.py`: reads `DATABASE_URL`, `KAFKA_BOOTSTRAP`, `ELUSION_DATA_DIR` and
  `PREFECT_API_URL` from the environment through `config.py`. `--sample` counts ids
  pulled, not ids scored. The spend guard and the model echo check run unchanged.
- `flows/collect.py`: one year end to end (stream from HuggingFace, Parquet, Postgres
  `documents`, Kafka). It holds a whole year's paragraphs in memory, so its Job needs
  about 3 GB.
- `review/corpus.py`: `load_texts` reads every `year=*.parquet` under
  `$ELUSION_DATA_DIR/corpus` into memory; one year is roughly a third of the 1 GB that
  three years take.
- `review/prompt.py`: prompts are files inside the package (`review/prompts/`), so they
  must be present in the built image.
- `compose.yaml`: the pinned image tags and start commands for Postgres 16.15, Redpanda
  v26.2.6 and Prefect 3.9.1 that the chart mirrors.
- Tools on the dev Mac: `terraform` 1.17.1, `kubectl` 1.33.9, `az` 2.92.0, `docker`
  (OrbStack). `helm` is not installed. `az` is not signed in. There is no `infra/`
  directory and no Dockerfile.
- Local champion run: 17,936 documents for (gpt-6-luna, cloud-concentration/v2), $1.9587.

## Tasks, in order

Each code task is test-first, one commit, `uv run ruff check . && uv run pytest` green.
Tasks marked **owner** are run by the owner at the terminal because they install
software, need credentials, spend money or create cloud resources; the controller hands
over the exact command and waits. No test makes a live call.

### 0. Owner: prerequisites

- Approve and run `brew install helm` (a new tool on the dev Mac).
- Turn on Kubernetes in OrbStack.
- `az login`, then confirm three things with read-only `az` commands the controller
  gives: which subscription is the default; that it has a vCPU quota of at least 4 for
  the Dsv5 family in West US 2; and that the signed-in account holds Owner or User Access
  Administrator on it. Terraform's role assignments (registry pull, blob access by
  identity) need that role. With Contributor only, stop and bring it to the owner; do not
  fall back to registry admin credentials or storage account keys.

### 1. Find the year to run

Done 2026-11-15. With the local Postgres up, `scores` rows were counted by model, prompt
version and filing year (read-only). Every pair under prompts v2 and v3 has 15,875
documents from 2018 and 2,061 from 2019; (claude-haiku-5-5, v1) has 10,845 and 2,061. No
model has a score for any 2020 document (599,703 documents). The cluster run uses 2020.
The local populations therefore span two years already, so the year filter of task 9
must accept more than one year.

### 2. Record where a review ran

- `config.py`: `run_env` from `ELUSION_RUN_ENV`, default `local`. `.env.example` and
  `.env.tpl` gain the variable with a comment.
- `review/db.py`: `run_env` column on `review_jobs`; `ensure_schema` adds it to an
  existing table; `start_job` stores it.
- `flows/evaluate.py` and `evaluate/report.py`: the cost section lists review jobs per
  environment, for example "5 local, 1 on aks-elusion". Rows from before this change have
  a null `run_env` and are reported as `local`.
- MLflow: `run_envs` logged as a param (comma-separated, sorted).
- Tests: the column is added to a table created without it; a job row carries the value;
  the report line for one and for two environments.
- The five committed reports are not regenerated in this task: `evaluate` stamps today's
  date on the report and logs a new MLflow run each time, so a regeneration is never a
  one-line change. They pick the line up the next time each pair is evaluated.

### 3. Container image

- `Dockerfile`: Python 3.12 slim base, dependencies installed with `uv sync --frozen
  --no-dev` from `uv.lock`, the package installed, a non-root user, entrypoint `elusion`.
  `.dockerignore` excludes `data/`, `.env*`, `.git`, `docs/`, `tests/`, `.venv/`.
- Checks, run against the built image: `elusion version`; a prompt loads
  (`review/prompts/cloud-concentration/v2.md` is present); `criteria/` is present;
  nothing from `data/` and no `.env` is in the image (list the filesystem and assert).
- Anti-goal check: no gold file, grade file or label can reach the image, because `data/`
  is excluded.

### 4. Helm chart `infra/helm/elusion`

One chart, written by hand so that each manifest is understood; no dependency on other
charts. It contains:

- Postgres, Redpanda and the Prefect server, each a single-replica workload with a
  PersistentVolumeClaim and a cluster-internal Service, with the image tags and start
  commands of `compose.yaml`. Redpanda's advertised address is its Service name.
- The Prefect worker Deployment for one Kubernetes work pool, with a ServiceAccount and a
  Role limited to this namespace that lets it create and watch Jobs and pods.
- A ConfigMap with the non-secret settings the flow runs need (`KAFKA_BOOTSTRAP`,
  `PREFECT_API_URL`, `ELUSION_DATA_DIR`, `ELUSION_RUN_ENV`, `ELUSION_MAX_RUN_USD`, the
  pinned model ids).
- `DATABASE_URL` is not in the ConfigMap, because `config.py` reads it as one complete
  URL that includes the password. Each pod builds it from the password in the Secret
  (Kubernetes expands `$(VAR)` in a later environment entry), or the password is supplied
  as `PGPASSWORD` beside a URL without one. `config.py` is not changed. The generated
  password is hexadecimal so that it is safe inside a URL.
- References by name to the two Secrets of call 8; the chart fails to render with a clear
  message if a name is not given.
- The corpus volume, switched by a value: an ordinary PersistentVolumeClaim locally, the
  Blob CSI mount on AKS.
- Resource requests and limits on every container, sized from the figures under "Inputs".
- `values.yaml` (the laptop cluster) and `values-aks.yaml` (AKS: image from ACR by digest,
  blob mount, cluster name as `run_env`).

Checks: `helm lint`; `helm template` piped to `kubectl apply --dry-run=client` for both
values files; a grep of the rendered output for anything that looks like a key.

### 5. Prefect deployments on the work pool

- The work pool's job template makes every flow-run Job use the elusion image, load the
  ConfigMap and the Secrets as environment, mount the corpus volume, and never retry
  (call 14).
- Two deployments, `collect` and `review`, registered against the in-cluster Prefect
  server. Their parameters are plain values (year, limit; criterion name, sample, provider
  name, model, prompt version, concurrency). The `provider` and `ids` parameters of the
  review flow take live objects and are test seams; if Prefect cannot build a deployment
  schema with them, add a thin wrapper flow that takes only plain values and calls
  `review`, rather than changing `review`'s signature.
- This is the least certain task. If shaping the job template takes more than a couple of
  hours, stop and bring the options to the owner; do not quietly fall back to plain Jobs
  that bypass the worker.

### 6. Rehearsal on the laptop cluster

Install the chart on OrbStack's Kubernetes with `values.yaml`. Then:

- `collect` for the year from task 1 with `limit=5`: the Parquet file appears on the
  corpus volume, `documents` has rows, the topic has messages.
- `review` with `sample=50`, provider `ollama`, model `qwen2.5:14b`, prompt version `v2`,
  concurrency 2, reaching the Ollama server on the Mac from inside the cluster: 50 scores
  in the cluster's Postgres, a finished `review_jobs` row whose `run_env` is the laptop
  cluster's name, the flow run and its batches visible in the cluster's Prefect UI.
- Reset the consumer group to the start of the topic with Redpanda's `rpk`, then run the
  same review again: `0 to score, 50 skipped`. Without the reset the run would pull the
  next 50 ids and score them.
- Start a run with `sample=300` and delete its pod part-way. The Job does not restart.
  Read the group's committed offset, start a run for the remainder, and confirm that the
  two runs together scored each of the 300 documents exactly once.
- Practise the export of task 8 against this cluster and load it into a scratch database.
- `helm uninstall` and delete the volumes.

The rehearsal passes when all of the above hold. Its scores are discarded (call 5).

### 7. Terraform module `infra/terraform`

- Resources: resource group; AKS cluster (free tier control plane, one node pool of two
  `Standard_D2s_v5`, system-assigned identity, Blob CSI driver enabled); container
  registry (Basic) with pull permission for the cluster's kubelet identity; storage
  account and one private blob container with access for the cluster through identity,
  not an account key; a resource-group budget of $10 with an email alert (approved by the
  owner 2026-11-15).
- Variables for region, names and node count, with defaults; outputs for the cluster
  name, registry login server and storage names that the Helm values need.
- The builder reads the current `azurerm` provider documentation for every argument and
  does not recall them; the provider version is pinned.
- Checks without Azure: `terraform fmt -check`, `terraform validate`.
- **Owner**: `terraform plan` after `az login`; the controller reads the plan with the
  owner and the resource count matches the list above.

### 8. Owner: the Azure day

Done in one sitting, commands from `infra/README.md`, the controller alongside:

1. `terraform apply`; note the time.
2. `az aks get-credentials`; `kubectl get nodes` shows two ready nodes.
3. Create the two Secrets (call 8).
4. Push the image to the registry; put its digest in `values-aks.yaml`.
5. `helm install` with `values-aks.yaml`; every pod is ready.
6. Run `collect` for the year from task 1 with `limit=5` (the mount smoke test of call
   13), then for the whole year. The Parquet file is in the blob container.
7. Run `review` with `sample=20000` and the champion's parts (call 2). The spend guard's
   projection is printed before the bulk spend; expected about $2.20. If the run fails
   part-way, the next run takes the remainder (call 14), never 20,000 again.
8. Export `scores` and `review_jobs` to `data/exports/`; the exported row counts equal
   the counts in the cluster's Postgres; record the file's SHA-256 in the phase note.
9. Record for the note: node count and size, wall-clock of collect and review, documents
   per minute, the image digest, the cluster name.
10. `terraform destroy`; `az group show` reports the group as not found.
11. Next day: read the cost for the resource group in the Azure portal and record it.

If anything blocks for more than an hour, export what exists and destroy; the run is
resumable on a later day because the export is loaded back first.

### 9. Bring the scores home

- Load the export into the local Postgres (a new filing year, so no key collides; the
  loader refuses if any does).
- The new scores are evaluated as their own population: (gpt-6-luna, v2) restricted to
  filing year 2020. That needs a year filter on `judge`, `grade` and `evaluate` that does
  not exist today, accepting one or more years, so that the existing reports stay
  reproducible as years 2018 and 2019. It gets a short design addendum to this spec,
  approved by the owner, before it is built.
- **Owner**: `judge`, `grade` and `evaluate` for the 2020 population, one at a time. The
  judge labels a few hundred paragraphs for about $1. The resulting report names the
  cluster.

### 10. Owner: phase note, ticks, decisions, docs

`docs/notes/phase-6.md` with the four headings of `docs/notes/README.md`. It must say, in
the owner's words: what Terraform, Helm, a work pool and a worker each did; the
AWS-to-Azure mapping for every resource used; what the day cost against the estimate
below; what broke between the rehearsal and Azure. Tick the Phase 6 boxes. `DECISIONS.md`
lines for each confirmed call. `docs/ARCHITECTURE.md` gains a phase 6 section drawn like
the others. `CLAUDE.md` commands block and `README.md` status line updated.

## Non-goals and anti-goals

- No managed database, Event Hubs, ingress, TLS, autoscaling, monitoring stack or CI.
- No MLflow on the cluster; no `judge`, `grade` or `evaluate` on the cluster.
- No new Python dependency in `pyproject.toml` (the worker's package comes with Prefect's
  own image, call 6).
- No Spark or Delta (phase 5), nothing of phase 7.
- Anti-goal: any secret value in git, in a values file, in Terraform variables or in the
  conversation.
- Anti-goal: the gold set, grades or labels in the image, in blob storage or anywhere on
  the cluster.
- Anti-goal: a cluster document scored twice for the same (model, prompt version), on the
  cluster or at home.
- Anti-goal: an Azure resource alive at the end of the Azure day.

## Left to the builder

- Whether Postgres, Redpanda and Prefect are Deployments or StatefulSets.
- The Dockerfile's exact stages, and whether the amd64 image is built locally with
  `docker buildx` or in the registry with `az acr build`.
- How the work pool's job template is installed (a chart hook, or a documented command).
- How the Ollama server on the Mac is reached from the laptop cluster.
- Whether the export is `pg_dump --data-only` or two `COPY ... TO` files, as long as the
  row counts are verified and the file is hashed.
- Names of resources, the namespace, chart value names.

## Expected cost

Azure, at West US 2 list prices read from https://prices.azure.com/api/retail/prices on
2026-11-15, for an 8-hour day:

- nodes: 2 x `Standard_D2s_v5` at $0.096 an hour = $1.54
- AKS control plane, free tier: $0
- container registry, Basic: $0.17 a day
- outbound public IP: $0.005 an hour = $0.04
- outbound load balancer: about $0.20 (about $0.025 an hour; not returned by the price
  API on 2026-11-15, to be confirmed by the builder)
- disks and blob storage: cents

About $2 a day. A cluster left running costs about $5.50 a day, about $165 a month, which
is what the same-day destroy and the proposed budget alert guard against.

Models: review about $2.20 (20,000 documents at the local gpt-6-luna rate of $1.9587 for
17,936); judge about $1; grade about $0.20; rehearsal $0. Each run is guarded at
`ELUSION_MAX_RUN_USD`.

Total about $5.50, inside the $10 cloud ceiling and the $40 project ceiling.

## For the owner (answered 2026-11-15)

1. Budget alert: add a $10 resource-group budget with an email alert to the Terraform
   module? It is one resource beyond the checklist, and the email address would be a
   variable supplied at apply time, never committed. **Answer: yes.**
2. Region: West US 2 is assumed because the prices were read for it. Is another region
   preferred? **Answer: West US 2.**
3. How should the cluster's scores be evaluated once they are home? **Answer: B, on the
   controller's recommendation.** After the import, the champion's pair holds scores the
   judge has not labeled, so today's `evaluate` would refuse to run for it until they are
   judged, and would then overwrite the report the promotion rests on. A year filter is
   what keeps that report reproducible.
   - **A. Merged into the champion's pair.** No new code. The champion's report then
     covers about 37,936 documents across two filing years, while the other four pairs
     still cover the original population, so the leaderboard is no longer like for like.
     The champion stays as promoted, with a note saying so; the report that justified the
     promotion is overwritten in `docs/reports/` and survives only in its MLflow run.
   - **B. As their own population, by filing year (recommended).** The existing reports
     and the leaderboard are untouched, and a second report shows how the champion,
     chosen on one filing year, holds on a year it was not chosen on. That is a stronger
     statistical statement than a merged number. It costs a year filter on `judge`,
     `grade` and `evaluate`: new code in a phase that is otherwise about infrastructure.
   Re-judging costs about $1 under either.
