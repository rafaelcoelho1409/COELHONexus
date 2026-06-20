#!/usr/bin/env bash
# Smoke test for playwright — headed + headless pods Ready + CDP endpoints reachable.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:playwright] namespace 'playwright' exists"
kubectl ${KC} get namespace playwright >/dev/null

echo "[smoke:playwright] headless pod Ready (playwright image ~870 MB — first pull is slow)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n playwright -l app.kubernetes.io/component=headless --timeout=600s >/dev/null

echo "[smoke:playwright] headed pod Ready (chromium image ~1.5 GB — first pull is slow)"
kubectl ${KC} wait --for=condition=Ready \
  pods -n playwright -l app.kubernetes.io/component=headed --timeout=600s >/dev/null

echo "[smoke:playwright] CDP headed /json/version reachable on :9222"
kubectl ${KC} run pw-headed-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://playwright-headed.playwright.svc.cluster.local:9222/json/version \
  >/dev/null 2>&1

echo "[smoke:playwright] CDP headless /json/version reachable on :9224"
kubectl ${KC} run pw-headless-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf http://playwright-headless.playwright.svc.cluster.local:9224/json/version \
  >/dev/null 2>&1

echo "[smoke:playwright] PASS"
