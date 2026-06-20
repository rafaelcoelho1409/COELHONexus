#!/usr/bin/env bash
# Smoke test for langfuse — web + worker + clickhouse Ready + /api/public/health.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:langfuse] namespace 'langfuse' exists"
kubectl ${KC} get namespace langfuse >/dev/null

echo "[smoke:langfuse] clickhouse pod Ready (chart-bundled, ~600 MB image)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n langfuse -l app.kubernetes.io/name=clickhouse --timeout=360s >/dev/null

echo "[smoke:langfuse] web pod Ready (~500 MB image + clickhouse-init wait)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n langfuse -l app.kubernetes.io/component=web,app.kubernetes.io/instance=langfuse --timeout=360s >/dev/null

echo "[smoke:langfuse] worker pod Ready (~500 MB image, shared with web)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n langfuse -l app.kubernetes.io/component=worker,app.kubernetes.io/instance=langfuse --timeout=360s >/dev/null

echo "[smoke:langfuse] /api/public/health returns 200 (in-cluster)"
kubectl ${KC} run langfuse-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/health \
  >/dev/null 2>&1

echo "[smoke:langfuse] OTLP endpoint /api/public/otel reachable"
OTLP_CODE=$(kubectl ${KC} run langfuse-otel-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s --quiet -- \
  sh -c "curl -s -o /dev/null -w '%{http_code}' http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel/v1/traces" \
  2>/dev/null | tail -1)
case "${OTLP_CODE}" in
  200|400|401|405|415) echo "  ✓ HTTP ${OTLP_CODE} (endpoint exists; rejection of empty GET is expected)";;
  *)                   echo "  ✗ unexpected HTTP ${OTLP_CODE}"; exit 1;;
esac

echo "[smoke:langfuse] PASS"
echo
echo "  Web UI:        http://localhost:23006 (after port-forward)"
echo "  Initial login: admin@demo.local / admin-demo-password"
echo "  Public key:    lf_pk_demo000000000000000000000000"
echo "  Secret key:    (see env.hcl demo.langfuse_secret_key)"
