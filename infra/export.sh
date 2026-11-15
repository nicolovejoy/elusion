#!/usr/bin/env bash
# Export `scores` and `review_jobs` from the cluster's Postgres to data/exports/ (phase 6,
# task 8 step 8; rehearsed in task 6). Run from the repo root, with kubectl pointed at the
# cluster:
#   infra/export.sh
# Writes data/exports/STAMP/{scores,review_jobs}.csv (CSV with a header row), counts.txt
# and SHA256SUMS, and fails unless each file holds exactly the rows the cluster's table
# holds, counted before and after the copy. Run it when no review is in progress.
# data/ is gitignored; nothing here leaves the laptop.
set -euo pipefail

ns=${NS:-elusion}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out=data/exports/$stamp
fail() { echo "FAIL: $*" >&2; exit 1; }

# psql inside the Postgres pod, which authenticates the local socket without a password.
psql_pod() { kubectl -n "$ns" exec -i postgres-0 -- psql -U elusion -d elusion -v ON_ERROR_STOP=1 -tA "$@"; }
count() { psql_pod -c "select count(*) from $1"; }
# Rows in a CSV file, parsed (a rationale may hold a newline inside quotes), minus the header.
csv_rows() { uv run --quiet python -c 'import csv, sys; print(sum(1 for _ in csv.reader(open(sys.argv[1], newline="", encoding="utf-8"))) - 1)' "$1"; }

mkdir -p "$out"
for t in scores review_jobs; do
  before=$(count "$t")
  # A stable order, so two exports of the same table are byte-identical.
  case $t in
    scores) order="doc_id, model, prompt_version" ;;
    review_jobs) order="started_at, job_id" ;;
  esac
  psql_pod -c "copy (select * from $t order by $order) to stdout with (format csv, header)" >"$out/$t.csv"
  after=$(count "$t")
  rows=$(csv_rows "$out/$t.csv")
  [ "$before" = "$after" ] || fail "$t changed during the export ($before then $after rows): is a review running?"
  [ "$rows" = "$before" ] || fail "$t.csv has $rows rows, the cluster's table $before"
  echo "$t $rows" >>"$out/counts.txt"
  echo "ok   $t: $rows rows, equal to the cluster's count"
done

(cd "$out" && shasum -a 256 scores.csv review_jobs.csv >SHA256SUMS)
sed 's/^/     /' "$out/SHA256SUMS"
echo "PASS exported to $out"
