#!/usr/bin/env bash
# Smoke test for loki — confirms pod Ready + /ready returns 200.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:loki] namespace 'loki' exists"
kubectl ${KC} get namespace loki >/dev/null

echo "[smoke:loki] single-binary pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n loki -l app.kubernetes.io/component=single-binary --timeout=180s >/dev/null

echo "[smoke:loki] /ready returns 200 (in-cluster)"
kubectl ${KC} run loki-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://loki.loki.svc.cluster.local:3100/ready \
  >/dev/null 2>&1

echo "[smoke:loki] grafana_datasource ConfigMap created"
kubectl ${KC} get configmap -n loki -l grafana_datasource=1 -o name | grep -q .

echo "[smoke:loki] MinIO buckets bootstrap Job Complete"
kubectl ${KC} get job -n loki loki-create-buckets >/dev/null
kubectl ${KC} get job -n loki loki-create-buckets -o jsonpath='{.status.succeeded}' | grep -q 1

echo "[smoke:loki] ServiceMonitor present"
kubectl ${KC} get servicemonitor -n loki >/dev/null

echo "[smoke:loki] PASS"
