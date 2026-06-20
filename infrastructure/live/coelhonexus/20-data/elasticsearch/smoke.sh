#!/usr/bin/env bash
# Smoke test for elasticsearch — confirms ECK operator + ES pod Ready + cluster green/yellow.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:elasticsearch] elastic-system (operator) namespace exists"
kubectl ${KC} get namespace elastic-system >/dev/null

echo "[smoke:elasticsearch] ECK operator pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n elastic-system -l app.kubernetes.io/name=elastic-operator --timeout=120s >/dev/null

echo "[smoke:elasticsearch] elasticsearch namespace exists"
kubectl ${KC} get namespace elasticsearch >/dev/null

echo "[smoke:elasticsearch] Elasticsearch CR exists"
kubectl ${KC} get elasticsearch -n elasticsearch elasticsearch >/dev/null

echo "[smoke:elasticsearch] ES pod Ready (cold start can take ~3-5 min)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n elasticsearch -l common.k8s.elastic.co/type=elasticsearch --timeout=420s >/dev/null

echo "[smoke:elasticsearch] retrieving auto-generated elastic password"
PASSWORD=$(kubectl ${KC} get secret -n elasticsearch elasticsearch-es-elastic-user \
  -o jsonpath='{.data.elastic}' | base64 -d)
[ -n "${PASSWORD}" ]

echo "[smoke:elasticsearch] /_cluster/health returns green or yellow (in-cluster, HTTPS)"
HEALTH=$(kubectl ${KC} run es-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=60s -- \
  curl -sk -u "elastic:${PASSWORD}" \
    https://elasticsearch-es-http.elasticsearch.svc.cluster.local:9200/_cluster/health \
  2>/dev/null | tr ',' '\n' | grep status | head -1)
echo "  cluster ${HEALTH}"
echo "${HEALTH}" | grep -qE 'green|yellow'

echo "[smoke:elasticsearch] PASS"
