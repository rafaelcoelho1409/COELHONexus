#!/usr/bin/env bash
# Smoke test for argocd — confirms all 4 core pods Ready + image-updater + admin secret.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../../../../.." && pwd))"
KUBECONFIG_PATH="${REPO_ROOT}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
KC="--kubeconfig=${KUBECONFIG_PATH}"

echo "[smoke:argocd] namespace 'argocd' exists"
kubectl ${KC} get namespace argocd >/dev/null

echo "[smoke:argocd] core pods Ready (server, controller, repo-server, applicationset; ~300 MB image, cold pull serial)"
for label in \
  "app.kubernetes.io/name=argocd-server" \
  "app.kubernetes.io/name=argocd-application-controller" \
  "app.kubernetes.io/name=argocd-repo-server" \
  "app.kubernetes.io/name=argocd-applicationset-controller"; do
  kubectl ${KC} wait --for=condition=Ready \
    pods -n argocd -l "${label}" --timeout=300s >/dev/null
  echo "  ✓ ${label#*=}"
done

echo "[smoke:argocd] Image Updater pod Ready"
kubectl ${KC} wait --for=condition=Ready \
  pods -n argocd -l app.kubernetes.io/name=argocd-image-updater --timeout=240s >/dev/null

echo "[smoke:argocd] initial admin password secret exists"
kubectl ${KC} get secret -n argocd argocd-initial-admin-secret >/dev/null

echo "[smoke:argocd] server Service reachable on :80 (in-cluster)"
kubectl ${KC} run argocd-smoke-check \
  --image=curlimages/curl:8.10.1 --rm -i --restart=Never --timeout=30s -- \
  curl -sf -o /dev/null http://argocd-server.argocd.svc.cluster.local/healthz \
  >/dev/null 2>&1

echo "[smoke:argocd] PASS"
echo
echo "  Initial admin password:"
echo "  kubectl --kubeconfig=${KUBECONFIG_PATH} -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo "  (will be needed when you port-forward → http://localhost:23007)"
