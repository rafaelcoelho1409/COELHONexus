#!/usr/bin/env bash
# Smoke test for postgresql — confirms pod Ready + SELECT 1 + ServiceMonitor + backup CronJob.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:postgresql] namespace 'postgresql' exists"
kubectl ${KC} get namespace postgresql >/dev/null

echo "[smoke:postgresql] primary pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n postgresql -l app.kubernetes.io/component=primary --timeout=180s >/dev/null

echo "[smoke:postgresql] retrieving password from secret"
PG_PASSWORD=$(kubectl ${KC} get secret -n postgresql postgresql \
  -o jsonpath='{.data.postgres-password}' | base64 -d)
[ -n "${PG_PASSWORD}" ]

echo "[smoke:postgresql] SELECT 1 succeeds"
RESULT=$(kubectl ${KC} exec -n postgresql postgresql-0 -- env PGPASSWORD="${PG_PASSWORD}" \
  psql -U postgres -tAc "SELECT 1")
[ "${RESULT}" = "1" ]

echo "[smoke:postgresql] ServiceMonitor created"
kubectl ${KC} get servicemonitor -n postgresql >/dev/null

echo "[smoke:postgresql] backup CronJob scheduled (daily 02:00 UTC)"
kubectl ${KC} get cronjob -n postgresql postgresql-backup >/dev/null

echo "[smoke:postgresql] PASS"
