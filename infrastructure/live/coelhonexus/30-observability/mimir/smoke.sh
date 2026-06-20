#!/usr/bin/env bash
# Smoke test for mimir — distributor + ingester Ready + buckets created + datasource.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:mimir] namespace 'mimir' exists"
kubectl ${KC} get namespace mimir >/dev/null

echo "[smoke:mimir] distributor pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n mimir -l app.kubernetes.io/component=distributor --timeout=180s >/dev/null

echo "[smoke:mimir] ingester pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n mimir -l app.kubernetes.io/component=ingester --timeout=180s >/dev/null

echo "[smoke:mimir] query-frontend pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n mimir -l app.kubernetes.io/component=query-frontend --timeout=120s >/dev/null

echo "[smoke:mimir] distributor /ready returns 200 (in-cluster)"
kubectl ${KC} run mimir-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://mimir-distributor.mimir.svc.cluster.local:8080/ready \
  >/dev/null 2>&1

echo "[smoke:mimir] grafana_datasource ConfigMap created"
kubectl ${KC} get configmap -n mimir -l grafana_datasource=1 -o name | grep -q .

echo "[smoke:mimir] MinIO bucket bootstrap Job Complete"
kubectl ${KC} get job -A -o json | python3 -c "
import sys,json; data=json.load(sys.stdin)
jobs=[j for j in data['items'] if j['metadata']['namespace']=='mimir' and 'bucket' in j['metadata']['name'].lower()]
assert jobs, 'no bucket job found in mimir ns'
for j in jobs:
    succeeded=j.get('status',{}).get('succeeded',0)
    print(f\"  {j['metadata']['name']} succeeded={succeeded}\")
    assert succeeded>=1
"

echo "[smoke:mimir] PASS"
