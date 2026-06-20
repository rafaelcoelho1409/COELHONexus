#!/usr/bin/env bash
# Smoke test for grafana — pod Ready + /api/health + sidecar-discovered datasources.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:grafana] namespace 'grafana' exists"
kubectl ${KC} get namespace grafana >/dev/null

echo "[smoke:grafana] pod Ready (grafana image ~600 MB — first pull is slow)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n grafana -l app.kubernetes.io/name=grafana --timeout=300s >/dev/null

echo "[smoke:grafana] /api/health returns 200 (in-cluster)"
kubectl ${KC} run grafana-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://grafana.grafana.svc.cluster.local/api/health \
  >/dev/null 2>&1

echo "[smoke:grafana] admin secret exists"
kubectl ${KC} get secret -n grafana grafana-admin >/dev/null

echo "[smoke:grafana] sidecar discovered datasources from loki + tempo + mimir"
sleep 15  # give the sidecar a moment to scan + import
PASSWORD=$(kubectl ${KC} get secret -n grafana grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)
DATASOURCES=$(kubectl ${KC} run grafana-ds-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf -u "admin:${PASSWORD}" \
    http://grafana.grafana.svc.cluster.local/api/datasources 2>/dev/null)
for src in Loki Tempo Mimir; do
  echo "${DATASOURCES}" | grep -qi "${src}" || { echo "  ✗ ${src} missing"; exit 1; }
  echo "  ✓ ${src}"
done

echo "[smoke:grafana] PASS"
echo
echo "  Admin password:"
echo "  kubectl --kubeconfig=${KUBECONFIG_PATH} -n grafana get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d"
echo "  (port-forward → http://localhost:23005)"
