# Phase 6 spec: production in Azure

Status: designed 2026-11-15 and checked once by a fresh reader; the owner confirmed the
controller's calls the same day, with the amendments marked below. Build in progress:
tasks 0 to 7 are done and `infra/README.md`, the runbook, is written; the owner's Azure day (task 8) is next. See "Progress and open
items" directly below. Read
`docs/ARCHITECTURE.md`, the Phase 6 checklist in `docs/PHASES.md` and `compose.yaml` first.
Hard rules in `CLAUDE.md` apply to every line below, in particular: no Azure resource is
created except by the owner running `terraform apply` themselves. The phase 4 spec
(`docs/plans/phase-4-leaderboard.md`) is the pattern this one follows.

## Progress and open items

As of 2026-11-15, on branch `phase-6-production` (pushed, no pull request yet):

- Done: task 0 (prerequisites; results under the task), task 1 (the year is 2020), task 2
  (`run_env`, commit `2ac889e`, 254 tests pass), task 3 (`Dockerfile`, `.dockerignore`,
  `infra/check-image.sh`; both architectures pass, 1.64 GB), task 4 (`infra/helm/elusion`,
  `infra/check-chart.sh`; also installed once on OrbStack's cluster in a scratch namespace:
  Postgres, Redpanda, the Prefect server and the worker all ready, then removed). Builder's
  choices in task 4: Postgres, Redpanda and Prefect are StatefulSets; the AKS identifiers
  (registry, image digest, resource group, storage account, kubelet identity client id)
  are passed to `helm install` with `--set` from `terraform output` and the push, not
  written into `values-aks.yaml`, which keeps Azure ids out of git (open item 5) and amends
  step 4 of task 8; `helm install` runs without `--wait`, because the laptop's storage
  class binds the corpus claim only when a flow-run Job first mounts it.
- Task 5 done: `files/base-job-template.json` and `files/prefect.yaml` in the chart,
  registered by a Helm hook Job on every install and upgrade; `infra/check_job_template.py`
  and a step of `infra/check-chart.sh` check the template, and Prefect's own code builds a
  Job from it that the API schema accepts. In a scratch namespace on OrbStack the hook
  registered the pool and both deployments, and a `review` run with `sample=1` on an empty
  topic became a Job (`backoffLimit` 0, uid 10001, no API token), reached Postgres, Kafka
  and the Prefect server, wrote a `review_jobs` row with `run_env` `orbstack`, and
  completed at $0. No wrapper flow was needed: Prefect builds a parameter schema for
  `review` as it is (`provider` and `ids` become "any, default null").
- Task 6 done, the rehearsal on OrbStack's Kubernetes (namespace `elusion`, released and
  deleted after). Everything in the task held, with these findings:
  - **Prefect would have retried a killed run by itself.** With `backoffLimit` 0,
    prefect-kubernetes sets `PREFECT_FLOW_RUN_EXECUTE_SIGTERM_BEHAVIOR=reschedule` in the
    flow-run pod unless the work pool's `env` variable already names it. The first kill
    test showed it: the run went to `AwaitingRetry` and the worker started a second Job,
    which pulled from the committed offset onward and scored 20 new documents before it
    was cancelled. On AKS that is the unattended second spend call 14 forbids. Fixed: the
    template's `env` variable defaults to `crash`; `check_job_template.py` checks the
    default and that no deployment overrides `env`, and `check-chart.sh` runs the worker's
    `prepare_for_flow_run` and asserts the prepared Job carries `crash` once (a copy
    without the default fails with `['reschedule']`). Rerun after `helm upgrade`: the
    killed Job stayed `Failed`, no new Job, the flow run `Crashed`.
  - A killed run's `review_jobs` row stays `running` with the count it reached, because
    the process ends on SIGTERM before the flow can close the row. The counts are right
    (`docs_scored` is added batch by batch), so the check held: 100 + 200 = 300 rows in
    `scores`. The report's cost comes from `scores.cost_usd` (task 9 addendum), so a stale
    `running` row does not change it.
  - `rpk group seek` needs the group to be empty: after a kill, the dead consumer stays a
    member until its session times out, about 45 seconds.
  - `collect` with `limit=5` gives 264 paragraphs for 2020, fewer than the 300 the kill
    test needs; the rehearsal deleted the topic and ran `collect` with `limit=10`
    (869 paragraphs). `collect` publishes every paragraph it reads, also those already in
    Postgres, so a second collect duplicates the first one's messages in the topic.
    Approved by the owner for the Azure day: the topic is deleted between the `limit=5`
    smoke test and the full-year collect (task 8 step 6).
  - `helm uninstall` leaves the flow-run Jobs (the worker made them, not Helm), and their
    finished pods keep the corpus claim from being deleted; the teardown deletes the Jobs
    first.
  - The export is `infra/export.sh`: `COPY ... TO STDOUT` CSV files for `scores` and
    `review_jobs` in `data/exports/STAMP/`, row counts read before and after and compared
    with the parsed files, `SHA256SUMS`. Loaded into a scratch database given the local
    tables' schema with `header match`: 300 and 2 rows. Then dropped.
  - Ollama on the Mac is reached at `http://host.orb.internal:11434`; `qwen2.5:14b` at
    concurrency 2 scored about 50 documents in 1 minute 50 seconds.
- Task 7 built, up to `terraform validate` (`infra/terraform`, `infra/check-terraform.sh`).
  azurerm is 5.14.0, a major version past the drafting; every argument was read from the
  v5.14.0 docs and checked against `terraform providers schema`. Builder's choices and
  findings:
  - Kubernetes `1.36` (AKS picks the patch): West US 2 offers 1.34 to 1.37 on 2026-11-15,
    and 1.36 is what the rehearsal ran on. `node_os_upgrade_channel = "None"`, no
    automatic upgrade. `node_provisioning_profile` is a required block in 5.x; `Manual`.
    Azure CNI overlay networking, stated explicitly (an omitted block means kubenet).
  - The blob mount switches to `protocol: fuse2` (blobfuse2). `fuse` is blobfuse v1, whose
    support ended in September 2026. The driver and blobfuse2 sources show the
    managed-identity path: no account-key lookup with `AzureStorageAuthType: MSI`, `MSI`
    parsed case-insensitively, `-o allow_other`, `uid=`, `gid=` accepted.
    `check-chart.sh` asserts `fuse2`.
  - The kubelet identity gets `AcrPull` on the registry and Storage Blob Data
    Contributor on the container. The account key stays enabled but unused: disabling it
    would move Terraform's own storage calls to Entra ID sign-in, untested and on paid
    time. Added beyond the list: Storage Blob Data Contributor on the container for the
    signed-in owner, so step 6 of task 8 and the fallback of call 13 use
    `az storage blob ... --auth-mode login`, not the key.
  - Registry and storage account names carry six characters of the subscription id's
    SHA-1 (globally unique names, no id in git). Microsoft.ManagedIdentity was not
    registered; the provider registers it with the five of task 0 and Consumption. That
    registration happens when the provider starts, so `terraform plan` performs it.
  - The budget email is `TF_VAR_budget_email`, marked sensitive, in no file. Budget start
    is the first of the current month, ignored after creation.
  - The owner's `terraform plan`, 2026-11-15: `Plan: 10 to add, 0 to change, 0 to
    destroy`, the ten above, read through with the controller. Every stated choice shows
    as written (Kubernetes 1.36, Free tier, two `Standard_D2as_v7` on Ubuntu 24.04, Azure
    CNI overlay, blob driver on, registry admin off, storage private and TLS 1.2). Defaults
    worth knowing: a Standard load balancer with an outbound public IP (in the cost
    estimate), the disk, file and snapshot drivers on, registry role mode
    `LegacyRegistryPermissions` (the mode `AcrPull` works in). The plan output is in
    `data/` (gitignored); it carries the owner's ids.
- `infra/README.md` written, the runbook for task 8. Builder's choices: the amd64 image
  is built and checked before paid time and pushed as tag `phase6`, its digest read back
  from the registry; the OpenAI key reaches `kubectl` through a pipe (`--from-file
  =/dev/stdin`, tried in a dry run), not the command line; the local Prefect server is
  stopped for the day, because the cluster's UI is built for `localhost:4200` and a
  command aimed at the wrong server then fails instead of landing locally; the champion
  check is one `resolve` call against the local MLflow (run 2026-11-15: `openai
  gpt-6-luna cloud-concentration/v2`). The call 13 fallback is written out: the managed
  disk render (`corpus.mode=pvc`, `managed-csi`) passes the strict dry run, and the
  copy-out pod plus `kubectl cp` were tried on OrbStack (same SHA-256 out as in).
- Next: the owner's Azure day (task 8), from `infra/README.md`.
- Then the owner: `judge`,
  `grade` and `evaluate` for 2020 (task 9), the phase note (task 10).

Open items, each with the task that must close it:

1. **Does an AKS node pool accept `Standard_D2as_v7`?** The size attaches disks through
   NVMe only. The AKS limits page, the size's own page and the AKS FAQ, read 2026-11-15,
   do not say. Close it from the AKS documentation or release notes before task 7 is
   closed; the fallback is a quota request for 4 DSv5 vCPUs. Read again 2026-11-15 (AKS VM
   sizes page, AKS Generation 2 page, the Dasv7 page, Azure's NVMe page, AKS release notes
   April to October 2026): no page names the size or NVMe-only sizes for AKS. What they do
   say: AKS gives a Generation-2-only size the Generation 2 Linux node image by default; the
   Dasv7 series needs an NVMe-capable OS image, and Azure lists Ubuntu 22.04, Ubuntu 24.04
   and Azure Linux 3.0 as such (not Ubuntu 26.04, which AKS makes the `Ubuntu` default from
   Kubernetes 1.38); Dasv7 has no ephemeral OS disk, so the OS disk is managed; 2 vCPU and
   8 GB meets the system-pool minimum. Confirmed by the owner 2026-11-15: since no document
   settles it, the `apply` itself is the test; Terraform pins the node OS image to
   `Ubuntu2404` and the OS disk to managed, and a refusal falls back to the quota request.
   The `standardDASv5Family` quota is also 0 in West US 2, so the AMD v5 size is no
   fallback. Closed for planning; the `apply` closes it in fact.
2. **Year filter for `judge`, `grade` and `evaluate`.** Approved by the owner 2026-11-15;
   the design is the addendum under task 9.
3. **A dedicated OpenAI key for the cluster run** (call 8). It needs its own 1Password
   item and `op://` reference; hand the owner an `op item create` command with it, and
   the revoke step is in task 8.
4. **`kubectl` 1.33.9 against OrbStack's Kubernetes 1.36.5.** Three minor versions apart,
   outside the supported gap of one. It has answered correctly so far; if it misbehaves
   in tasks 4 to 6, bring the owner a `brew upgrade` command to approve.
5. **Nothing that identifies the Azure subscription goes into git** (its name, its id,
   the directory id). Terraform reads the id from `ARM_SUBSCRIPTION_ID`. To sign in, plain
   `az login` fails on the multi-factor check; `az login --tenant` with the directory id
   works.
6. Outside this phase: the merged remote branch `origin/phase-4-leaderboard` can be
   deleted on the owner's word; whether to adopt "responsive" and "null set" repo-wide is
   undecided and belongs to the phase 7 spec.

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
  address is a variable supplied at apply time and never committed. Amended the same day:
  a second $10 budget covers the cluster's *node resource group*. AKS creates that group
  itself (named `MC_...` by default) and puts the node machines, their disks and the
  networking in it, so a budget on the main group alone would see only the registry and
  the storage account and would stay silent about a forgotten cluster.
- The region is West US 2.
- The cluster's scores are evaluated as their own population, by filing year, not merged
  into the champion's existing report. The owner left this to the controller's
  recommendation. The year filter gets a design addendum before task 9.

- Infrastructure as code throughout (the owner's steer during task 0). Everything that
  exists in Azure or in the cluster is declared in a committed file: Terraform for the
  Azure resources and the resource-provider registrations, the Helm chart for the
  manifests, a committed file for the work pool's job template and the two Prefect
  deployments. What is typed by hand is an action on those files (`apply`, `install`,
  image push, `deployment run`, the export, `destroy`), each one written in
  `infra/README.md`. The one deliberate exception is the two Secrets of call 8: their
  values must not be in any file, so they are created by a command in the runbook. This
  boundary is the controller's reading of the steer.

Controller's calls, 2026-11-15. The owner confirmed all fourteen the same day: nine as
written, four with an addition (1, 2, 8, 10) and one with its recovery procedure changed
(14). Each is a `DECISIONS.md` line.

1. **The cluster run scores a filing year that has no local scores.** Task 1 finds which
   of 2018, 2019 and 2020 has no row in the local `scores` table for any model. The
   cluster collects that year and reviews its first 20,000 ids with the champion
   configuration. Nothing is paid for twice, and no scores need to be copied into the
   cluster before the run. Task 1 was run on 2026-11-15 and the year is 2020. Addition:
   the report states its population as the first 20,000 paragraphs of 2020 in ingestion
   order. That is not a random draw from the year, and the metrics describe that
   population only.
2. **The champion is named by its parts, not resolved from the registry.** The run passes
   provider `openai`, model `gpt-6-luna` and prompt version `v2`, the values in
   `elusion-reviewer@champion` (DECISIONS 2026-11-07). `--reviewer` would need the MLflow
   registry, which lives in the local MLflow and is not deployed to the cluster. MLflow
   stays local; the review flow does not touch it otherwise. Addition: on the Azure day,
   before the review is started, `elusion-reviewer@champion` is resolved locally and
   compared with the three values about to be passed, so the cluster cannot run a
   configuration the registry no longer calls champion.
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
   cluster; the Anthropic key is not needed there. Addition: it is a key made for this
   run, with its own 1Password item, and the owner revokes it after the destroy.
9. **Terraform state is local and gitignored** (`.gitignore` already covers
   `infra/terraform/*.tfstate*`). The subscription id comes from the environment
   (`ARM_SUBSCRIPTION_ID`), not from a committed file. `.terraform.lock.hcl` is committed.
10. **Nodes are two `Standard_D2as_v7`** (2 vCPU, 8 GB each, x86-64; first drafted as
    `Standard_D2s_v5`, changed after task 0 found the new subscription has no quota for
    that family), control plane on the
    free tier, region West US 2. The dev Mac is arm64, so the image is built for both
    `linux/arm64` (rehearsal) and `linux/amd64` (AKS) and the AKS values pin it by digest.
    Addition: task 0 checks that the subscription is not restricted from this size in
    West US 2, and the node size is a Terraform variable, so that a capacity refusal on
    the day is a one-line change.
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
    it. The Job's retry count is zero. Recovery, changed on confirmation: after a failure
    the consumer group is reset to the start of the topic with `rpk` and the same
    `sample=20000` run is started again by hand. It pulls the same 20,000 ids, skips every
    one that has a score and pays only for the rest, so there is no remainder to work out
    on paid time. A document that was refused or truncated the first time has no score and
    is attempted once more.

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
- `az login`, then confirm four things with read-only `az` commands the controller
  gives: which subscription is the default; that it has a vCPU quota of at least 4 for
  the Dsv5 family in West US 2; that `Standard_D2s_v5` carries no restriction for it
  there; and that the signed-in account holds Owner or User Access
  Administrator on it. Terraform's role assignments (registry pull, blob access by
  identity) need that role. With Contributor only, stop and bring it to the owner; do not
  fall back to registry admin credentials or storage account keys.

Run 2026-11-15, on a new pay-as-you-go subscription:

- `helm` 4.3.2 is installed; OrbStack's Kubernetes is on (one node, v1.36.5).
- The signed-in account holds Owner on the default subscription.
- The five resource providers the phase uses (Compute, ContainerService,
  ContainerRegistry, Storage, Network) were not registered on the new subscription and
  were registered by hand so that quota could be read. Terraform declares them too.
- Quota in West US 2: 10 regional vCPUs, but **0 for the DSv5 family**, and
  `Standard_D2s_v5` is restricted in all three availability zones. The size of call 10
  cannot be used without a quota request. The owner approved the switch to
  `Standard_D2as_v7` (2 vCPU, 8 GB, x86-64, family quota 10, no restriction, $0.0908 an
  hour against $0.096). The v7 sizes attach disks through NVMe only; the builder confirms
  in the AKS documentation that a node pool accepts the size before task 7 is closed. If
  it does not, the fallback is a quota request for 4 DSv5 vCPUs.

### 1. Find the year to run

Done 2026-11-15. With the local Postgres up, `scores` rows were counted by model, prompt
version and filing year (read-only). Every pair under prompts v2 and v3 has 15,875
documents from 2018 and 2,061 from 2019; (claude-haiku-5-5, v1) has 10,845 and 2,061. No
model has a score for any 2020 document (599,703 documents). The cluster run uses 2020.
The local populations therefore span two years already, so the year filter of task 9
must accept more than one year.

### 2. Record where a review ran

Done 2026-11-15, commit `2ac889e`. The local `review_jobs` table has the column; its 15
earlier rows hold no value and are reported as `local`.

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
  Reset the group to the start of the topic and start the same `sample=300` run again
  (call 14). Confirm that the second run skipped what the first had scored, that `scores`
  holds 300 rows, and that the two job rows' `docs_scored` add up to 300.
- Practise the export of task 8 against this cluster and load it into a scratch database.
- `helm uninstall` and delete the volumes.

The rehearsal passes when all of the above hold. Its scores are discarded (call 5).

### 7. Terraform module `infra/terraform`

- Resources: resource group; AKS cluster (free tier control plane, one node pool of two
  `Standard_D2as_v7`, no availability zones requested, system-assigned identity, Blob CSI
  driver enabled); container
  registry (Basic) with pull permission for the cluster's kubelet identity; storage
  account and one private blob container with access for the cluster through identity,
  not an account key; two budgets of $10 with an email alert, one on the resource group
  and one on the cluster's node resource group (approved by the owner 2026-11-15).
- Variables for region, names, node count and node size, with defaults; outputs for the cluster
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
   13), then for the whole year. The Parquet file is in the blob container. Between the
   two, delete the topic with `rpk topic delete documents.ingested` (amended 2026-11-15
   after the rehearsal, approved by the owner): `collect` publishes every paragraph it
   reads, so without it the smoke test's paragraphs would be in the topic twice.
7. Resolve `elusion-reviewer@champion` locally and check it against the parts (call 2).
   Run `review` with `sample=20000` and those parts. The spend guard's projection is
   printed before the bulk spend; expected about $2.20. If the run fails part-way, reset
   the consumer group and start the same run again (call 14); never start it again
   without the reset.
8. Export `scores` and `review_jobs` to `data/exports/`; the exported row counts equal
   the counts in the cluster's Postgres; record the file's SHA-256 in the phase note.
9. Record for the note: node count and size, wall-clock of collect and review, documents
   per minute, the image digest, the cluster name.
10. `terraform destroy`; `az group show` reports the group as not found, and so does the
    node resource group. Revoke the run's OpenAI key (call 8).
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
- **Addendum: the year filter** (approved by the owner 2026-11-15).
  - `--year` on `judge`, `grade` and `evaluate`, repeatable, **required**. Every step reads
    only the pair's scores for paragraphs filed in those years (a join on
    `documents.year`): the flagged pile, the seeded discard draw and the metrics. The
    draw depends on the whole score set, so without the filter the 2020 import would
    change the existing reports' discard sample and `judge` would pay for a new one; with
    `--year 2018 --year 2019` the old set, draw and report are reproduced exactly.
    Required, not optional, so no run can mix years by accident.
  - The report file name carries the years (`cloud-concentration-gpt-6-luna-v2-2020.md`).
    The five committed reports are renamed to `...-2018-2019.md` in the same commit, with
    their contents unchanged.
  - The report computes its population line, e.g. "17,936 of N paragraphs filed in
    2018-2019, in ingestion order, not a random draw" (call 1's addition, for every
    report).
  - Review cost for a population is the sum of its scores' own `cost_usd`. Review jobs
    carry no year, so the jobs, wall-clock and `run_env` lines list the jobs that scored at
    least one paragraph of the population. Billed calls that left no score are in a job's
    total but not in this sum; the report says so.
  - MLflow logs a `years` param.
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
- How the work pool's job template and the two Prefect deployments are applied, as long
  as each is a committed file and not typed by hand (a chart hook, `prefect.yaml`, or one
  documented command that reads the file).
- How the Ollama server on the Mac is reached from the laptop cluster.
- Whether the export is `pg_dump --data-only` or two `COPY ... TO` files, as long as the
  row counts are verified and the file is hashed.
- Names of resources, the namespace, chart value names.

## Expected cost

Azure, at West US 2 list prices read from https://prices.azure.com/api/retail/prices on
2026-11-15, for an 8-hour day:

- nodes: 2 x `Standard_D2as_v7` at $0.0908 an hour = $1.45
- AKS control plane, free tier: $0
- container registry, Basic: $0.17 a day
- outbound public IP: $0.005 an hour = $0.04
- outbound load balancer: about $0.20 (about $0.025 an hour; not returned by the price
  API on 2026-11-15, to be confirmed by the builder)
- disks and blob storage: cents

About $2 a day. A cluster left running costs about $5.25 a day, about $158 a month, which
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
