#!/usr/bin/env bash
# Smoke test for alloy — pod Ready + /-/healthy + Service ports include 4317/4318.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:alloy] namespace 'alloy' exists"
kubectl ${KC} get namespace alloy >/dev/null

echo "[smoke:alloy] pod Ready (~300 MB image — tight on cold pull)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n alloy -l app.kubernetes.io/name=alloy --timeout=240s >/dev/null

echo "[smoke:alloy] /-/healthy returns 200 (in-cluster)"
kubectl ${KC} run alloy-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://alloy.alloy.svc.cluster.local:12345/-/healthy \
  >/dev/null 2>&1

echo "[smoke:alloy] OTLP ports 4317 (gRPC) + 4318 (HTTP) exposed"
kubectl ${KC} get svc -n alloy alloy -o jsonpath='{.spec.ports[*].port}' | grep -q '4317'
kubectl ${KC} get svc -n alloy alloy -o jsonpath='{.spec.ports[*].port}' | grep -q '4318'

echo "[smoke:alloy] ServiceMonitor present"
kubectl ${KC} get servicemonitor -n alloy >/dev/null

echo "[smoke:alloy] PASS"
