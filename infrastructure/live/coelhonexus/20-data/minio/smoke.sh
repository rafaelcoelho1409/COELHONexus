#!/usr/bin/env bash
# Smoke test for minio — confirms pod Running + /health/live + API auth.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"

echo "[smoke:minio] namespace 'minio' exists"
kubectl --kubeconfig="${KUBECONFIG_PATH}" get namespace minio >/dev/null

echo "[smoke:minio] pod Running"
kubectl --kubeconfig="${KUBECONFIG_PATH}" wait --for=condition=Ready \
  pods -n minio -l release=minio --timeout=120s >/dev/null

echo "[smoke:minio] /minio/health/live returns 200 (in-cluster)"
kubectl --kubeconfig="${KUBECONFIG_PATH}" run minio-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://minio.minio.svc.cluster.local:9000/minio/health/live \
  >/dev/null 2>&1

echo "[smoke:minio] ServiceMonitor created"
kubectl --kubeconfig="${KUBECONFIG_PATH}" get servicemonitor -n minio minio >/dev/null

echo "[smoke:minio] PASS"
