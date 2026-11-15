#!/usr/bin/env bash
# Checks the elusion container image (phase 6, task 3). Run from the repo root:
#   infra/check-image.sh elusion:dev
#   PLATFORM=linux/amd64 infra/check-image.sh elusion:dev-amd64
# Exits non-zero on the first failed check. Makes no network or model calls.
set -euo pipefail

image="${1:?usage: infra/check-image.sh IMAGE}"
# Never pull: the check is about the image just built. (The array is never empty, because
# macOS's bash 3.2 treats an empty array as unset under `set -u`.)
platform=(--pull never)
[[ -z "${PLATFORM:-}" ]] || platform+=(--platform "$PLATFORM")

# Run a command inside a throwaway container from the image, bypassing the entrypoint.
in_image() { docker run --rm "${platform[@]}" --entrypoint "$1" "$image" "${@:2}"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# 1. The entrypoint is the CLI.
v=$(docker run --rm "${platform[@]}" "$image" version) || fail "elusion version did not run"
echo "ok   elusion version: $v"

# 2. Not root.
uid=$(in_image id -u)
[[ "$uid" != 0 ]] || fail "the image runs as root"
echo "ok   runs as uid $uid"

# 3. Prompts ship inside the installed package.
in_image python -c '
from elusion.review.prompt import load_prompt
p = load_prompt("cloud-concentration", "v2")
assert p.text.strip(), "empty prompt"
print(f"ok   prompt {p.version} loads ({len(p.text)} chars)")
' || fail "prompt cloud-concentration/v2 does not load"

# 4. The criteria the flows read by relative path are in the working directory.
in_image test -f criteria/cloud-concentration.md || fail "criteria/cloud-concentration.md missing"
echo "ok   criteria/cloud-concentration.md present"

# 5. The working directory holds only the virtualenv and the criteria: no data/, docs/,
#    tests/, .git, .env or source tree came in with the build context.
top=$(in_image sh -c 'ls -A /app | sort | tr "\n" " "')
[[ "$top" == ".venv criteria " ]] || fail "unexpected entries in /app: $top"
echo "ok   /app holds only: $top"

# 6. Anywhere in the filesystem: no .env file, no gold, grades or label file, no Parquet
#    outside the virtualenv (pyarrow's own test data, if any, is not ours).
leaks=$(in_image sh -c '
find / -xdev \( -path /proc -o -path /sys \) -prune -o \( \
  -name ".env" -o -name ".env.*" -o -path "*/data/gold*" -o -name "*grades*.csv" \
  -o -name "cloud-concentration.csv" -o \( -name "*.parquet" -not -path "/app/.venv/*" \) \
\) -print 2>/dev/null; true')  # find exits 1 on unreadable root-only dirs; not a leak
[[ -z "$leaks" ]] || fail "files that must not be in the image: $leaks"
echo "ok   no .env, gold, grades or Parquet files"

# 7. The two deployment entrypoints (infra/helm/elusion/files/prefect.yaml) load as flows,
#    the way Prefect loads them inside a flow-run Job.
in_image python -c '
from prefect.flows import load_flow_from_entrypoint
for ep in ("elusion.flows.collect.collect", "elusion.flows.review.review"):
    f = load_flow_from_entrypoint(ep, use_placeholder_flow=False)
    print(f"ok   entrypoint {ep} loads flow {f.name!r}")
' || fail "a deployment entrypoint does not load"

echo "PASS $image${PLATFORM:+ ($PLATFORM)}"
