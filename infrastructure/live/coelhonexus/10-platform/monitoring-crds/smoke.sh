#!/usr/bin/env bash
# Smoke test for monitoring-crds — confirms the Prometheus Operator CRDs are
# Established and the Helm release is `deployed`.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"

echo "[smoke:monitoring-crds] namespace 'monitoring' exists"
kubectl --kubeconfig="${KUBECONFIG_PATH}" get namespace monitoring >/dev/null

echo "[smoke:monitoring-crds] Helm release deployed"
kubectl --kubeconfig="${KUBECONFIG_PATH}" get secret -n monitoring \
  -l owner=helm,name=prometheus-operator-crds -o name >/dev/null

echo "[smoke:monitoring-crds] core CRDs Established"
for crd in \
  servicemonitors.monitoring.coreos.com \
  podmonitors.monitoring.coreos.com \
  probes.monitoring.coreos.com \
  prometheusrules.monitoring.coreos.com \
  alertmanagers.monitoring.coreos.com \
  prometheuses.monitoring.coreos.com; do
  kubectl --kubeconfig="${KUBECONFIG_PATH}" wait --for=condition=Established \
    "crd/${crd}" --timeout=30s >/dev/null
  echo "  ✓ ${crd}"
done

echo "[smoke:monitoring-crds] PASS"
