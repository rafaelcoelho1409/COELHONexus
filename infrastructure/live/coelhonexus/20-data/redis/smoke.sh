#!/usr/bin/env bash
# Smoke test for redis — confirms pod Ready + redis-cli PING + Stack modules loaded.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:redis] namespace 'redis' exists"
kubectl ${KC} get namespace redis >/dev/null

echo "[smoke:redis] master pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n redis -l app.kubernetes.io/component=master --timeout=120s >/dev/null

echo "[smoke:redis] retrieving password from secret"
REDIS_PASSWORD=$(kubectl ${KC} get secret -n redis redis \
  -o jsonpath='{.data.redis-password}' | base64 -d)
[ -n "${REDIS_PASSWORD}" ]

echo "[smoke:redis] PING → PONG"
PONG=$(kubectl ${KC} exec -n redis redis-master-0 -- \
  redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning PING)
[ "${PONG}" = "PONG" ]

echo "[smoke:redis] Stack modules loaded (RediSearch/Json/TimeSeries/Bloom)"
MODULES=$(kubectl ${KC} exec -n redis redis-master-0 -- \
  redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning MODULE LIST)
for m in search ReJSON timeseries bf; do
  echo "${MODULES}" | grep -qi "${m}" || { echo "  missing module: ${m}"; exit 1; }
done
echo "  ✓ search, ReJSON, timeseries, bf"

echo "[smoke:redis] ServiceMonitor created"
kubectl ${KC} get servicemonitor -n redis >/dev/null

echo "[smoke:redis] PASS"
