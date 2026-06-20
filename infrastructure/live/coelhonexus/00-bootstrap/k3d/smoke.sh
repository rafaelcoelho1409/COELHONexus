#!/usr/bin/env bash
# Smoke test for the k3d leaf — confirms cluster came up + nodes Ready.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"

echo "[smoke:k3d] cluster ${COELHONEXUS_CLUSTER_NAME:-coelhonexus} should be listed"
k3d cluster list -o json | jq -e '.[] | select(.name == "coelhonexus")' >/dev/null

echo "[smoke:k3d] kubeconfig exists at ${KUBECONFIG_PATH}"
[ -f "${KUBECONFIG_PATH}" ]

echo "[smoke:k3d] API responsive"
kubectl --kubeconfig="${KUBECONFIG_PATH}" cluster-info >/dev/null

echo "[smoke:k3d] all nodes Ready"
kubectl --kubeconfig="${KUBECONFIG_PATH}" wait --for=condition=Ready nodes --all --timeout=30s

echo "[smoke:k3d] registry container exists"
docker ps --format '{{.Names}}' | grep -qx coelhonexus-registry

echo "[smoke:k3d] host registry at :5000 reachable"
curl -sf http://localhost:5000/v2/_catalog >/dev/null

echo "[smoke:k3d] PASS"
