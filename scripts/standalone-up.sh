#!/usr/bin/env bash
# =============================================================================
# COELHO Nexus standalone — phased cluster bring-up (Flow B: all internal)
# =============================================================================
# Applies each leaf in dep order; runs the leaf's smoke.sh between phases.
# Failure stops the chain. Re-runs are idempotent (each leaf checks state).
#
# Prereqs (verify with `./scripts/standalone-prereqs.sh`):
#   - docker, k3d, kubectl, jq
#   - terragrunt + tofu
#
# Usage:
#   ./scripts/standalone-up.sh                # apply all known phases
#   PHASES="00-bootstrap/k3d" ./scripts/standalone-up.sh   # one phase only
# =============================================================================
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
LIVE_DIR="${REPO_ROOT}/infrastructure/live/coelhonexus"

# Phase order — Rancher is intentionally near the top (right after
# monitoring-crds, before the data layer). COELHO Cloud's bcdb7ae commit shipped
# rancher alongside monitoring-crds when the cluster had nothing else; installing
# Rancher on a near-empty cluster avoids the GVR-discovery race that wedges it
# when 16+ other workloads compete for the API server during cache-sync.
DEFAULT_PHASES="
  00-bootstrap/k3d
  10-platform/monitoring-crds
  10-platform/cert-manager
  10-platform/rancher
  20-data/minio
  20-data/redis
  10-platform/argocd
  20-data/postgresql
  20-data/qdrant
  20-data/neo4j
  20-data/elasticsearch
  30-observability/loki
  30-observability/tempo
  30-observability/mimir
  30-observability/alloy
  30-observability/grafana
  40-apps/langfuse
  50-edge/playwright
"
PHASES="${PHASES:-$DEFAULT_PHASES}"

for phase in $PHASES; do
  echo
  echo "================================================================"
  echo "[standalone-up] PHASE: ${phase}"
  echo "================================================================"

  LEAF="${LIVE_DIR}/${phase}"
  [ -d "${LEAF}" ] || { echo "[standalone-up] leaf ${LEAF} not found"; exit 1; }

  echo "[standalone-up] terragrunt apply..."
  (cd "${LEAF}" && terragrunt apply -auto-approve)

  SMOKE="${LEAF}/smoke.sh"
  if [ -x "${SMOKE}" ]; then
    echo "[standalone-up] running smoke.sh..."
    bash "${SMOKE}" || { echo "[standalone-up] smoke FAILED for ${phase}"; exit 1; }
  else
    echo "[standalone-up] (no smoke.sh — skipping)"
  fi

  echo "[standalone-up] ✓ ${phase} green"
done

echo
echo "[standalone-up] all phases done"
echo "[standalone-up] next: kubectl config use-context k3d-coelhonexus"
