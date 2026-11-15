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

# 3. The AKS render pins the image by digest and mounts the blob container; the laptop
#    render does neither.
# (The elusion image reaches the flow-run Jobs through the ConfigMap, task 5.)
grep -q 'ELUSION_IMAGE: "example.azurecr.io/elusion@sha256:' <<<"$aks_yaml" \
  || fail "aks image not by digest"
grep -q 'ELUSION_IMAGE: "elusion:dev"' <<<"$local_yaml" || fail "laptop image is not elusion:dev"
grep -q 'blob.csi.azure.com' <<<"$aks_yaml" || fail "aks corpus is not the blob mount"
! grep -q 'blob.csi.azure.com' <<<"$local_yaml" || fail "laptop render uses the blob mount"
echo "ok   aks: image by digest, blob corpus; laptop: neither"

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
