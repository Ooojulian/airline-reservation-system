#!/usr/bin/env bash
# Ejecuta athena_tables.sql sentencia por sentencia (workgroup primary).
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
OUT=s3://airline-system-930581488240/athena-results/
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 - "$DIR/athena_tables.sql" <<'PY' | while IFS= read -r -d '' SQL; do
import sys
txt = "\n".join(l for l in open(sys.argv[1]).read().splitlines() if not l.startswith("--"))
for s in txt.split("\n;;\n"):
    s = s.strip().rstrip(";")
    if s:
        sys.stdout.write(s + "\0")
PY
  QID=$(aws athena start-query-execution --work-group primary --result-configuration OutputLocation=$OUT --query-string "$SQL" --query QueryExecutionId --output text)
  while :; do
    ST=$(aws athena get-query-execution --query-execution-id "$QID" --query QueryExecution.Status.State --output text)
    case "$ST" in SUCCEEDED) break;; FAILED|CANCELLED) aws athena get-query-execution --query-execution-id "$QID" --query QueryExecution.Status.StateChangeReason --output text; exit 1;; esac
    sleep 1
  done
  echo "OK: ${SQL%%$'\n'*}"
done
