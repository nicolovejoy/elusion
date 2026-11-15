#!/usr/bin/env bash
# Checks the Helm chart infra/helm/elusion (phase 6, task 4). Run from the repo root:
#   infra/check-chart.sh
# Needs helm and kubectl; `kubectl apply --dry-run=client` validates against the current
# context's API schema (OrbStack's cluster) without creating anything.
set -euo pipefail

chart=infra/helm/elusion
# The AKS identifiers come from `terraform output` on the day and are passed with --set,
# never committed; fake ones here are enough to render and validate the manifests.
aks_values=(
  image.registry=example.azurecr.io
  image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
  corpus.blob.resourceGroup=rg-example
  corpus.blob.storageAccount=stexample
  corpus.blob.identityClientId=00000000-0000-0000-0000-000000000000
)
# --set flags for every AKS value except the one named in $1 (if any).
aks_sets() { for kv in "${aks_values[@]}"; do [[ "$kv" == "${1:-}="* ]] || printf -- '--set\n%s\n' "$kv"; done; }
aks_ids=()
while IFS= read -r line; do aks_ids+=("$line"); done < <(aks_sets)
fail() { echo "FAIL: $*" >&2; exit 1; }

render() { helm template elusion "$chart" --namespace elusion "$@"; }

# 1. Lint, both environments.
helm lint "$chart" >/dev/null || fail "helm lint (laptop values)"
helm lint "$chart" -f "$chart/values-aks.yaml" "${aks_ids[@]}" >/dev/null || fail "helm lint (aks)"
echo "ok   helm lint, laptop and aks"

# 2. Render and validate against the Kubernetes API schema, both environments.
local_yaml=$(render) || fail "render (laptop)"
aks_yaml=$(render -f "$chart/values-aks.yaml" "${aks_ids[@]}") || fail "render (aks)"
for env in local aks; do
  yaml_var="${env}_yaml"
  printf '%s\n' "${!yaml_var}" | kubectl apply --dry-run=client --validate=strict -f - >/dev/null \
    || fail "kubectl dry-run rejected the $env manifests"
done
echo "ok   kubectl apply --dry-run=client, laptop and aks"

# 3. The work pool's job template and the deployments (task 5), for each environment.
#    The AKS flow runs use the registry image by digest; the laptop's use elusion:dev.
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
printf '%s\n' "$local_yaml" >"$tmp/local.yaml"
printf '%s\n' "$aks_yaml" >"$tmp/aks.yaml"
uv run --quiet python infra/check_job_template.py "$tmp/local.yaml" elusion:dev \
  | sed 's/^ok   /ok   laptop: /' || fail "job template (laptop)"
uv run --quiet python infra/check_job_template.py "$tmp/aks.yaml" \
  "example.azurecr.io/elusion@sha256:0000000000000000000000000000000000000000000000000000000000000000" \
  | sed 's/^ok   /ok   aks: /' || fail "job template (aks)"

# 3b. Prefect itself turns the template into a Job manifest, which the API schema accepts.
#     Uses the worker image, the code that will do it on the cluster.
for env in local aks; do
  uv run --quiet python -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "prefect-config")
print(cm["data"]["base-job-template.json"])' "$tmp/$env.yaml" >"$tmp/$env-template.json"
  # Prefect starts a throwaway local server for this; its chatter goes to a log file.
  docker run --rm -i -e PREFECT_SERVER_ANALYTICS_ENABLED=false --entrypoint python "$(helm show values "$chart" | awk '/workerImage:/ {print $2}')" -c '
import asyncio, json, sys
from prefect_kubernetes.worker import KubernetesWorkerJobConfiguration
tpl = json.load(sys.stdin)
cfg = asyncio.run(KubernetesWorkerJobConfiguration.from_template_and_values(
    tpl, {"memory_request": "3Gi", "memory_limit": "4Gi"}))
m = cfg.job_manifest
m["metadata"]["name"] = "check"
m["spec"]["template"]["spec"]["containers"][0]["args"] = ["prefect", "flow-run", "execute"]
print(json.dumps(m))' <"$tmp/$env-template.json" >"$tmp/$env-job.json" 2>"$tmp/$env-prefect.log" \
    || { cat "$tmp/$env-prefect.log" >&2; fail "Prefect could not build a Job from the $env template"; }
  kubectl apply --dry-run=client --validate=strict -f "$tmp/$env-job.json" >/dev/null \
    || fail "the Job Prefect builds from the $env template is rejected by the API schema"
done
echo "ok   Prefect builds a valid Job from both templates (kubectl strict dry-run)"

grep -q 'blob.csi.azure.com' <<<"$aks_yaml" || fail "aks corpus is not the blob mount"
! grep -q 'blob.csi.azure.com' <<<"$local_yaml" || fail "laptop render uses the blob mount"
echo "ok   aks corpus is the blob mount; the laptop's is not"

# 4. Without the AKS identifiers the AKS render refuses, naming what is missing.
if out=$(render -f "$chart/values-aks.yaml" 2>&1); then fail "aks render without ids succeeded"; fi
grep -q 'is required on AKS' <<<"$out" || fail "missing-identifier message unclear: $out"
# Each identifier on its own, so every one of them is enforced, not only the first.
for kv in "${aks_values[@]}"; do
  v="${kv%%=*}"
  sets=()
  while IFS= read -r line; do sets+=("$line"); done < <(aks_sets "$v")
  if out=$(render -f "$chart/values-aks.yaml" "${sets[@]}" 2>&1); then
    fail "aks render without $v succeeded"
  fi
  grep -q "$v is required on AKS" <<<"$out" || fail "missing $v message unclear: $out"
done
echo "ok   aks render refuses without each of the five identifiers, naming it"

# 5. Without a Secret name the render refuses, naming the value.
for s in postgresPassword openaiApiKey; do
  if out=$(render --set "secrets.$s=" 2>&1); then fail "render without secrets.$s succeeded"; fi
  grep -q "secrets.$s" <<<"$out" || fail "missing secrets.$s message unclear: $out"
done
echo "ok   render without either Secret name fails, naming it"

# 6. Nothing that looks like a secret value in either render: Secrets are referenced by
#    name only (secretKeyRef), never defined, and no key-like string appears.
for env in local aks; do
  yaml_var="${env}_yaml"
  y="${!yaml_var}"
  ! grep -qE '^kind: Secret' <<<"$y" || fail "$env render defines a Secret"
  ! grep -qiE 'sk-[a-z0-9_-]{16,}|AccountKey=|password: |POSTGRES_PASSWORD: ' <<<"$y" \
    || fail "$env render contains something that looks like a secret value"
done
echo "ok   no Secret objects and no secret-looking values in either render"

echo "PASS $chart"
