#!/usr/bin/env bash
# Checks the Terraform module infra/terraform without Azure (phase 6, task 7). Run from the
# repo root:
#   infra/check-terraform.sh
# `terraform init` downloads the pinned provider (no sign-in needed); nothing is planned.
set -euo pipefail

dir=infra/terraform
fail() { echo "FAIL: $*" >&2; exit 1; }

terraform -chdir="$dir" fmt -check -recursive >/dev/null || fail "terraform fmt -check (run terraform -chdir=$dir fmt)"
echo "ok   terraform fmt"

terraform -chdir="$dir" init -input=false -backend=false >/dev/null || fail "terraform init"
terraform -chdir="$dir" validate -no-color >/dev/null || fail "terraform validate"
echo "ok   terraform validate"

grep -q 'version = "= 5.14.0"' "$dir/versions.tf" || fail "azurerm is not pinned to an exact version"
grep -q 'version     = "5.14.0"' "$dir/.terraform.lock.hcl" || fail ".terraform.lock.hcl does not record azurerm 5.14.0"
git ls-files --error-unmatch "$dir/.terraform.lock.hcl" >/dev/null 2>&1 \
  || echo "note .terraform.lock.hcl is not committed yet"
echo "ok   azurerm pinned to 5.14.0, in the lock file"

# Nothing identifying the subscription or the owner in the committed files: no GUIDs, no
# email addresses, no state.
tf_files=$(find "$dir" -maxdepth 1 -name '*.tf' -o -maxdepth 1 -name '*.tfvars')
! grep -qiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' $tf_files || fail "a GUID is in the Terraform files"
! grep -qE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' $tf_files || fail "an email address is in the Terraform files"
! ls "$dir"/*.tfvars >/dev/null 2>&1 || fail "a .tfvars file exists; variables come from the environment"
if git ls-files "$dir" | grep -qE '\.tfstate'; then fail "Terraform state is tracked by git"; fi
echo "ok   no GUID, email, .tfvars or state in the module"

echo "PASS $dir"
