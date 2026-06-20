#!/usr/bin/env bash
# Smoke test for qdrant — confirms pod Ready + /healthz returns 200.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:qdrant] namespace 'qdrant' exists"
kubectl ${KC} get namespace qdrant >/dev/null

echo "[smoke:qdrant] pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n qdrant -l app.kubernetes.io/name=qdrant --timeout=120s >/dev/null

echo "[smoke:qdrant] /healthz returns 200 (no auth)"
kubectl ${KC} run qdrant-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://qdrant.qdrant.svc.cluster.local:6333/healthz \
  >/dev/null 2>&1

echo "[smoke:qdrant] retrieving deterministic api key from secret 'qdrant-api-key'"
APIKEY=$(kubectl ${KC} get secret -n qdrant qdrant-api-key \
  -o jsonpath='{.data.api-key}' | base64 -d)
[ -n "${APIKEY}" ]

echo "[smoke:qdrant] /collections returns 200 with api-key header"
kubectl ${KC} run qdrant-collections-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf -H "api-key: ${APIKEY}" \
    http://qdrant.qdrant.svc.cluster.local:6333/collections \
  >/dev/null 2>&1

echo "[smoke:qdrant] ServiceMonitor created"
kubectl ${KC} get servicemonitor -n qdrant >/dev/null

echo "[smoke:qdrant] PASS"
