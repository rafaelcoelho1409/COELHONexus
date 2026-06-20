#!/usr/bin/env bash
# =============================================================================
# COELHO Nexus standalone — host port forwards
# =============================================================================
# Mirrors `scripts/argocd-port-forward.sh` (which targets the production
# coelho-cloud cluster); this one targets the standalone `coelhonexus` k3d
# cluster + adds host ports for the platform UIs as we add modules.
#
# Each forward runs in `while true; do …; sleep 10; done` — survives pod
# restarts, kubectl drops, kube-API hiccups. Auto-reconnects.
#
# Usage:
#   ./scripts/standalone-port-forward.sh                # start all
#   pkill -f "port-forward .* k3d-coelhonexus|coelhonexus-pf"    # stop all
#
# Add a new line per module here as each one comes up green.
# =============================================================================
set -euo pipefail

# Cleanup any previous instance
pkill -f "coelhonexus-pf" 2>/dev/null || true
sleep 1

KUBECONFIG_FLAG="--kubeconfig=$(git rev-parse --show-toplevel)/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
CTX="k3d-coelhonexus"

# Forwarder loop: every entry below resilient to pod restart + missing svc.
_pf() {
  local label="$1" ns="$2" svc="$3" host_port="$4" target_port="$5"
  nohup bash -c "
    while true; do
      kubectl ${KUBECONFIG_FLAG} --context=${CTX} \
        port-forward -n ${ns} svc/${svc} ${host_port}:${target_port} 2>/dev/null
      sleep 10
    done
  " > "/tmp/coelhonexus-pf-${label}.log" 2>&1 &
  disown
}

echo "[standalone-port-forward] starting forwards on ${CTX}..."

# -----------------------------------------------------------------------------
# Apps — Skaffold deploys to the `coelhonexus-dev` namespace on the standalone
# cluster (Helm release `coelhonexus`, namespace override via skaffold.yaml).
# Production-style ArgoCD-driven deploys would target `coelhonexus` but that
# path doesn't exist on standalone (see docs/K8S-DUAL-CLUSTER-FLEX). If you
# ever helm-install directly into `coelhonexus`, flip these lines or run
# scripts/argocd-port-forward.sh instead.
# -----------------------------------------------------------------------------
_pf fastapi   coelhonexus-dev  coelhonexus-fastapi   23000  8000
_pf flower    coelhonexus-dev  coelhonexus-flower    23002  5555
_pf fasthtml  coelhonexus-dev  coelhonexus-fasthtml  23003  3000
_pf fastmcp   coelhonexus-dev  coelhonexus-fastmcp   23004  8000

# -----------------------------------------------------------------------------
# Platform UIs — added as modules come up green
# -----------------------------------------------------------------------------
_pf minio-api minio        minio                 23009  9000   # MinIO S3 API (for mc admin / s3 SDKs)
_pf minio     minio        minio-console         23008  9001   # MinIO Console UI

_pf argocd    argocd       argocd-server         23007  80     # ArgoCD UI (admin pw: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)

_pf grafana   grafana      grafana               23005  80     # Grafana UI (admin pw: kubectl -n grafana get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)

_pf langfuse  langfuse     langfuse-web          23006  3000   # LangFuse UI (login: admin@demo.local / admin-demo-password)
_pf rancher   cattle-system  rancher             23010  443    # Rancher UI on HTTPS (login: admin / rancher-demo-bootstrap — forces reset on first login). Browse to https://localhost:23010 and accept the self-signed cert.

# Module #4 (redis):       no UI (CLI access via kubectl exec)
# Module #15 (langfuse):   _pf langfuse  langfuse     langfuse-web          23006  3000

cat <<EOF

[standalone-port-forward] forwards started (auto-reconnect on failure):
  fastapi:    http://localhost:23000
  flower:     http://localhost:23002
  fasthtml:   http://localhost:23003
  fastmcp:    http://localhost:23004
  minio (UI): http://localhost:23008    (login: minioadmin / minioadmin)
  minio (S3): http://localhost:23009    (use with mc/aws-cli/s3 SDKs)
  argocd:     http://localhost:23007    (login: admin / kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
  grafana:    http://localhost:23005    (login: admin / kubectl -n grafana get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)
  langfuse:   http://localhost:23006    (login: admin@demo.local / admin-demo-password)
  rancher:    https://localhost:23010   (login: admin / rancher-demo-bootstrap — Rancher forces reset on first login; accept self-signed cert)

  Stop:       pkill -f coelhonexus-pf
  Logs:       /tmp/coelhonexus-pf-*.log
EOF


# 
