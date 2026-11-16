# infra: the Azure day

Phase 6 runs the champion review once on a rented AKS cluster (Azure Kubernetes Service)
and removes it the same day. This file is the runbook for that day: every command, in
order, ready to paste. The spec behind it is `docs/plans/phase-6-production.md` (task 8);
the reasons for each choice are there and in `docs/DECISIONS.md`.

Only the owner runs the commands that create, change or delete anything in Azure.
Expected cost: about $2 of Azure time for the day, about $2.20 for the review.

## What is in `infra/`

- `terraform/`: the Azure resources (resource group, AKS cluster, container registry,
  storage account and its `corpus` container, three role assignments, two $10 budgets).
  State is local and gitignored.
- `helm/elusion/`: the chart. It holds Postgres, Redpanda, the Prefect server and worker,
  the work pool's job template and the two Prefect deployments (`collect`, `review`).
  `values.yaml` is the laptop cluster; `values-aks.yaml` layers the AKS settings on top.
- `cluster.env.tpl`: the 1Password reference for the cluster's own OpenAI key.
- `export.sh`: copies `scores` and `review_jobs` out of the cluster's Postgres, with row
  counts checked and SHA-256 sums.
- Checks that need no Azure: `check-image.sh`, `check-chart.sh` (with
  `check_job_template.py`), `check-terraform.sh`.

The commands below run from the repo root, in one terminal ("tab 1") unless they say
"tab 2". Comments are kept out of the command blocks, because zsh treats a pasted `#`
line as a command.

## Before paid time (laptop only, nothing in Azure)

Run the three checks:

```
cd ~/src/elusion
infra/check-terraform.sh
infra/check-chart.sh
```

Build the amd64 image (the AKS nodes are x86-64; the Mac is arm64) and check it:

```
docker buildx build --platform linux/amd64 -t elusion:dev-amd64 --load .
PLATFORM=linux/amd64 infra/check-image.sh elusion:dev-amd64
```

Each prints `PASS`.

Start the local stack for the champion check of step 8. Then stop its Prefect server: the
cluster's Prefect UI expects port 4200 on the laptop, and with the local one stopped, a
command aimed at the wrong server fails loudly instead of landing in the local Prefect.

```
docker compose up -d
docker compose stop prefect
```

Confirm the 1Password item for the cluster's key exists. This lists item titles only:

```
op item list --vault dev-secrets | grep "OpenAI elusion aks"
```

## The day

### 1. Sign in and set the shell up

`az account show` works offline. If the first line below fails, sign in again with the
second (it forces the multi-factor prompt in the browser):

```
az account show --query state -o tsv
az login --tenant "$(az account show --query tenantId -o tsv)"
```

Then set the shell up. The email is typed at the prompt and kept out of every file:

```
cd ~/src/elusion
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
read "TF_VAR_budget_email?Budget alert email: "; export TF_VAR_budget_email
export PREFECT_API_URL=http://localhost:4200/api
```

### 2. Create the Azure resources

```
date
terraform -chdir=infra/terraform apply
```

Terraform prints the same plan as before, `Plan: 10 to add, 0 to change, 0 to destroy`,
and asks for `yes`. The cluster takes about 5 to 10 minutes. Note the time `date`
printed: billing starts here.

If the apply fails on the node size (`Standard_D2as_v7`, open item 1 of the spec), stop
there. Run `terraform -chdir=infra/terraform destroy` to remove whatever was created, and
bring the error to the controller. The fallback is a quota request for 4 DSv5 vCPUs.

Read the outputs into the shell. They hold no secret, and the next steps use them:

```
export RG=$(terraform -chdir=infra/terraform output -raw resource_group)
export CLUSTER=$(terraform -chdir=infra/terraform output -raw cluster_name)
export NODE_RG=$(terraform -chdir=infra/terraform output -raw node_resource_group)
export ACR=$(terraform -chdir=infra/terraform output -raw acr_name)
export ACR_SERVER=$(terraform -chdir=infra/terraform output -raw acr_login_server)
export SA=$(terraform -chdir=infra/terraform output -raw storage_account)
export KUBELET_ID=$(terraform -chdir=infra/terraform output -raw kubelet_client_id)
```

### 3. Point kubectl at the cluster

```
az aks get-credentials --resource-group "$RG" --name "$CLUSTER" --overwrite-existing
kubectl config current-context
kubectl get nodes -o wide
```

The context is `aks-elusion`. From here on, every `kubectl` and `helm` command goes to
Azure, not to OrbStack. Pass: two nodes, `Ready`, OS image Ubuntu 24.04. (`kubectl` 1.33
is three minor versions behind the cluster's 1.36; it worked against OrbStack's 1.36.
If it misbehaves, stop and ask for a `brew upgrade` command.)

### 4. Namespace and the two Secrets

The chart only names the Secrets; their values never touch a file. The Postgres password
is generated here and never shown. The OpenAI key goes from 1Password straight into
`kubectl` through a pipe, so it is not in the command line or the shell history.

```
kubectl create namespace elusion
openssl rand -hex 24 | tr -d '\n' | kubectl -n elusion create secret generic elusion-postgres --from-file=POSTGRES_PASSWORD=/dev/stdin
op run --env-file=infra/cluster.env.tpl -- sh -c 'printf %s "$OPENAI_API_KEY" | kubectl -n elusion create secret generic elusion-openai --from-file=OPENAI_API_KEY=/dev/stdin'
kubectl -n elusion get secret elusion-openai -o jsonpath='{.data.OPENAI_API_KEY}' | wc -c
```

Pass: both `secret/... created`, and the last line prints a number well above 0 (the
length of the encoded key, not the key).

### 5. Push the image

The image is the one built and checked before paid time. `az acr login` signs Docker in
with your Azure identity; the registry has no admin password.

```
az acr login --name "$ACR"
docker tag elusion:dev-amd64 "$ACR_SERVER/elusion:phase6"
docker push "$ACR_SERVER/elusion:phase6"
export DIGEST=$(az acr repository show --name "$ACR" --image elusion:phase6 --query digest -o tsv)
echo "$DIGEST"
```

Pass: the digest starts with `sha256:`. The cluster pulls the image by this digest, so a
later push to the same tag cannot change what runs.

### 6. Install the chart

```
helm install elusion infra/helm/elusion --namespace elusion -f infra/helm/elusion/values-aks.yaml --set image.registry="$ACR_SERVER" --set image.digest="$DIGEST" --set corpus.blob.resourceGroup="$RG" --set corpus.blob.storageAccount="$SA" --set corpus.blob.identityClientId="$KUBELET_ID" --timeout 10m
kubectl -n elusion get pods,pvc
```

`helm install` waits for its hook Job, which registers the work pool and both
deployments. Pass: `STATUS: deployed`; `postgres-0`, `redpanda-0`, `prefect-server-0` and
`prefect-worker-...` are `Running` and ready (`1/1`); the `corpus` claim is `Bound`. If a
pod is still starting, run the `get pods` line again after a minute.

### 7. The Prefect UI (tab 2)

Open a second terminal tab and leave this running in it all day:

```
kubectl -n elusion port-forward svc/prefect-server 4200:4200
```

Then open http://localhost:4200 in the browser. This is the cluster's Prefect, not the
local one. Every flow run, its logs and its task runs show here.

If the Mac sleeps or the tab closes, the forward drops. The runs on the cluster carry on;
run the line again in tab 2.

### 8. Collect: smoke test, then the whole year

First a 5-filing collect. It is the blob mount's first use (call 13): OrbStack could not
rehearse it. `--watch` follows the run until it finishes. Ctrl-C only stops the watching,
not the run.

```
uv run prefect deployment run collect/collect --param year=2020 --param limit=5 --watch
az storage blob list --account-name "$SA" --container-name corpus --auth-mode login --query "[].{name:name, bytes:properties.contentLength}" -o table
kubectl -n elusion exec postgres-0 -- psql -U elusion -d elusion -tAc "select count(*) from documents"
kubectl -n elusion exec redpanda-0 -- rpk topic describe documents.ingested --print-partitions
```

Pass:

- The run ends `Completed`.
- The blob list shows `corpus/year=2020.parquet`.
- `documents` holds about 264 rows (the rehearsal's count for `limit=5`).
- The topic's high watermark is the same number.

A just-made role assignment can take a few minutes to reach Azure Storage. A `403` from
the blob list means waiting a few minutes and running it again. So does a collect pod
stuck in `ContainerCreating` with a mount error: check its events with
`kubectl -n elusion describe pods`. If the mount still fails after an hour, take the
fallback at the end of this file.

Then delete the topic. `collect` publishes every paragraph it reads, so without this the
smoke test's paragraphs would be in the topic twice. Then collect the whole year:

```
kubectl -n elusion exec redpanda-0 -- rpk topic delete documents.ingested
uv run prefect deployment run collect/collect --param year=2020 --watch
kubectl -n elusion exec postgres-0 -- psql -U elusion -d elusion -tAc "select count(*) from documents"
kubectl -n elusion exec redpanda-0 -- rpk topic describe documents.ingested --print-partitions
```

Pass: `Completed`; about 599,703 documents (the local count for 2020); the topic's high
watermark equals the document count.

### 9. Check the champion, then review

The cluster has no MLflow, so the review names the champion by its parts (call 2). First
confirm, against the local MLflow, that the registry still calls those parts champion:

```
uv run python -c "from elusion.config import settings; from elusion.review.registry import resolve; m = resolve('elusion-reviewer@champion', settings.mlflow_tracking_uri); print(m.provider, m.model, m.prompt_version)"
```

Pass: the last line is `openai gpt-6-luna cloud-concentration/v2`. Anything else: stop,
do not start the review.

Then the review, 20,000 ids of 2020 with the champion's parts:

```
date
uv run prefect deployment run review/review --param criterion=cloud-concentration --param sample=20000 --param provider_name=openai --param model=gpt-6-luna --param prompt_version=v2 --watch
```

Follow it in the UI at http://localhost:4200 (Runs, then the `review` run, then Logs).
Early on, the spend guard logs a line
`projected cost $... for 20000 docs (...); limit $10.00`. Expected about $2.20. Above the
limit, the run stops itself before the bulk spend.

Pass: the run ends `Completed` and the last log line is `job ... finished: ...`.

### 10. If the review fails part-way

The flow-run Job never retries on its own (call 14). Recovery is to rewind the consumer
group to the start of the topic and start the same run again. It pulls the same 20,000
ids, skips every one that already has a score, and pays only for the rest. Never start it
again without the rewind: it would score 20,000 more documents instead.

Rewinding needs the group to be `Empty`. A killed consumer stays a member for about 45
seconds. Check the state first:

```
export GROUP=review.cloud-concentration.gpt-6-luna.cloud-concentration.v2
kubectl -n elusion exec redpanda-0 -- rpk group describe "$GROUP"
```

When `STATE` says `Empty`:

```
kubectl -n elusion exec redpanda-0 -- rpk group seek "$GROUP" --to start
uv run prefect deployment run review/review --param criterion=cloud-concentration --param sample=20000 --param provider_name=openai --param model=gpt-6-luna --param prompt_version=v2 --watch
```

A run stopped by the spend guard (`BudgetExceeded`) is not a failure to retry. Bring it to
the controller.

### 11. Record the figures for the phase note

```
( kubectl get nodes -o wide; kubectl -n elusion get jobs; kubectl -n elusion exec postgres-0 -- psql -U elusion -d elusion -c "select status, docs_scored, cost_usd, run_env, started_at, finished_at, round(docs_scored / nullif(extract(epoch from finished_at - started_at), 0) * 60) as docs_per_minute from review_jobs order by started_at"; echo "image $ACR_SERVER/elusion@$DIGEST"; echo "cluster $CLUSTER" ) 2>&1 | tee data/azure-day.txt
```

`get jobs` shows how long each collect and review Job ran. The file `data/azure-day.txt`
is gitignored.

### 12. Export the scores

With no review running:

```
infra/export.sh
```

Pass: `ok` for `scores` and `review_jobs`, each equal to the cluster's count, then
`PASS exported to data/exports/...`. The `SHA256SUMS` file in that folder goes into the
phase note. Loading the export into the local Postgres is task 9, after the day.

### 13. Destroy

Stop the port-forward in tab 2 with Ctrl-C. Then, in tab 1:

```
terraform -chdir=infra/terraform destroy
```

Terraform lists 10 resources to destroy and asks for `yes`. Pass:
`Destroy complete! Resources: 10 destroyed.` Then confirm nothing is left:

```
date
az group exists --name "$RG"
az group exists --name "$NODE_RG"
az resource list --tag project=elusion -o table
```

Pass: `false`, `false`, and an empty list. Note the time: billing stops here.

### 14. Tidy the laptop and revoke the key

Point kubectl back at OrbStack, forget the deleted cluster, sign Docker out of the
deleted registry, and start the local Prefect again:

```
kubectl config use-context orbstack
kubectl config delete-context aks-elusion
kubectl config delete-cluster aks-elusion
kubectl config delete-user clusterUser_rg-elusion_aks-elusion
docker logout "$ACR_SERVER"
docker compose start prefect
```

Revoke the cluster's OpenAI key (call 8). On https://platform.openai.com/api-keys, find
the key made for this run and delete it. Then archive its 1Password item:

```
op item delete "OpenAI elusion aks" --vault dev-secrets --archive
```

### 15. The next day: the bill

Azure's cost data lags by several hours. Open https://portal.azure.com, search for "Cost
analysis", set the scope to the subscription and the range to the day, and group by
resource group. Record the totals for `rg-elusion` and for the `MC_...` group in the
phase note.

## If the blob mount fails (call 13 fallback)

After an hour without a working mount, the corpus becomes a managed disk for the run, and
the Parquet file goes to the blob container afterwards with `az`.

Switch the volume. The finished Jobs' pods hold the claim, so they go first:

```
kubectl -n elusion delete jobs --all
kubectl -n elusion delete pvc corpus
helm upgrade elusion infra/helm/elusion --namespace elusion -f infra/helm/elusion/values-aks.yaml --set image.registry="$ACR_SERVER" --set image.digest="$DIGEST" --set corpus.blob.resourceGroup="$RG" --set corpus.blob.storageAccount="$SA" --set corpus.blob.identityClientId="$KUBELET_ID" --set corpus.mode=pvc --set corpus.storageClass=managed-csi --timeout 10m
```

Then continue from step 8: the smoke test (minus the blob listing), the topic delete, the
whole year and the review. After the review, before the export, copy the Parquet file out
through a short-lived pod that mounts the disk, and upload it:

```
kubectl -n elusion run corpus-copy --image=python:3.12.13-slim --restart=Never --overrides='{"spec":{"containers":[{"name":"corpus-copy","image":"python:3.12.13-slim","command":["sleep","3600"],"volumeMounts":[{"name":"corpus","mountPath":"/data"}]}],"volumes":[{"name":"corpus","persistentVolumeClaim":{"claimName":"corpus"}}]}}'
kubectl -n elusion wait --for=condition=Ready pod/corpus-copy --timeout=5m
kubectl -n elusion cp corpus-copy:/data/corpus/year=2020.parquet data/exports/year=2020.parquet
az storage blob upload --account-name "$SA" --container-name corpus --name corpus/year=2020.parquet --file data/exports/year=2020.parquet --auth-mode login
kubectl -n elusion delete pod corpus-copy
```

The phase note says which path was taken.

## If anything blocks for more than an hour

Export what exists (step 12) and destroy (steps 13 and 14). The run can resume on a later
day, because the export is loaded into the local Postgres first.

## A new terminal mid-day

Shell variables live only in the terminal that set them. In a new one, run step 1's
setup block, the `export` block from step 2, and this:

```
export DIGEST=$(az acr repository show --name "$ACR" --image elusion:phase6 --query digest -o tsv)
```
