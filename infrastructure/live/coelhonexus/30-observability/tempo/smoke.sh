#!/usr/bin/env bash
# Smoke test for tempo — confirms pod Ready + /ready + datasource ConfigMap + bucket Job.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:tempo] namespace 'tempo' exists"
kubectl ${KC} get namespace tempo >/dev/null

echo "[smoke:tempo] tempo pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n tempo -l app.kubernetes.io/name=tempo --timeout=180s >/dev/null

echo "[smoke:tempo] /ready returns 200 (in-cluster)"
kubectl ${KC} run tempo-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://tempo.tempo.svc.cluster.local:3200/ready \
  >/dev/null 2>&1

echo "[smoke:tempo] grafana_datasource ConfigMap created"
kubectl ${KC} get configmap -n tempo -l grafana_datasource=1 -o name | grep -q .

echo "[smoke:tempo] MinIO bucket bootstrap Job Complete"
kubectl ${KC} get job -A -o json | python3 -c "
import sys,json; data=json.load(sys.stdin)
jobs=[j for j in data['items'] if j['metadata']['namespace']=='tempo' and 'bucket' in j['metadata']['name'].lower()]
assert jobs, 'no bucket job found in tempo ns'
for j in jobs:
    succeeded=j.get('status',{}).get('succeeded',0)
    print(f\"  {j['metadata']['name']} succeeded={succeeded}\")
    assert succeeded>=1
"

echo "[smoke:tempo] ServiceMonitor present"
kubectl ${KC} get servicemonitor -n tempo >/dev/null

echo "[smoke:tempo] PASS"
