#!/usr/bin/env bash
# Smoke test for neo4j — confirms pod Ready + cypher RETURN 1 + APOC loaded.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:neo4j] namespace 'neo4j' exists"
kubectl ${KC} get namespace neo4j >/dev/null

echo "[smoke:neo4j] pod Ready (neo4j can take ~2-3 min on cold start)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n neo4j -l app=neo4j --timeout=300s >/dev/null

echo "[smoke:neo4j] retrieving password from env (env.hcl demo value)"
PASSWORD="neo4j-demo-password"   # matches env.hcl demo.neo4j_password

echo "[smoke:neo4j] cypher RETURN 1 (Bolt)"
RESULT=$(kubectl ${KC} exec -n neo4j neo4j-0 -- cypher-shell \
  -u neo4j -p "${PASSWORD}" \
  --format plain "RETURN 1" 2>&1 | tail -1 | tr -d '[:space:]')
[ "${RESULT}" = "1" ]

echo "[smoke:neo4j] APOC plugin loaded"
APOC=$(kubectl ${KC} exec -n neo4j neo4j-0 -- cypher-shell \
  -u neo4j -p "${PASSWORD}" \
  --format plain "RETURN apoc.version()" 2>&1 | tail -1 | tr -d '[:space:]"')
[ -n "${APOC}" ]
echo "  ✓ apoc.version() = ${APOC}"

echo "[smoke:neo4j] PASS"
