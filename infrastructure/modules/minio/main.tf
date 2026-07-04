# =============================================================================
# minio module — MinIO standalone (S3-compatible object storage)
# =============================================================================
#
# Deploys:
#   1. minio namespace
#   2. MinIO Helm release (charts.min.io 5.4.0)
#   3. Two external Ingresses:
#      - Console UI  → https://<console-hostname>.<domain>
#      - S3 API      → https://<api-hostname>.<domain>
#   4. ServiceMonitor for Prometheus (chart 5.4.0 doesn't include this resource)
#
# Architecture:
#   - chart's own Ingress: DISABLED (external ingress controller handles external)
#   - chart's TLS: handled by the external ingress controller's proxy (no internal certs)
#   - chart deploys TWO Services: <release> (API 9000), <release>-console (UI 9001)
#   - External Ingresses point at the appropriate Service
#   - external ingress controller (deployed earlier) spawns proxy pods for each hostname
#
# Chart provenance:
#   v1 ran charts.min.io/minio 5.4.0 successfully on this hardware.
#   The MinIO community chart was archived 2026-04-25 (read-only); pinned
#   version still pulls and deploys cleanly. Bitnami's MinIO chart was
#   evaluated but had local deployment issues per user's prior experience.
#
# v1 → v2 changes (kept minimal — chart is the same):
#   - PVC: 50Gi → 15Gi (3× reduction; v1 used <5GB in practice)
#   - Module folder layout: helm/ + k8s/ subfolders (v2 convention)
#   - External hostnames: full Homepage annotations including `href` +
#     `pod-selector` (chart uses old `app=minio` label, not modern)
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "minio" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "minio"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# MinIO Helm release
# -----------------------------------------------------------------------------
# Repository note: `charts.min.io` is archived as of 2026-04-25 but the
# repo is read-only, not deleted — `helm repo add` and chart pulls still
# work. Pin chart_version explicitly so latest pointer drift can't bite us.
# -----------------------------------------------------------------------------
resource "helm_release" "minio" {
  name       = var.release_name
  repository = "https://charts.min.io/"
  chart      = "minio"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.minio.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      root_user      = var.root_user
      root_password  = var.root_password
      storage_class  = var.storage_class
      storage_size   = var.storage_size
      cpu_request    = var.cpu_request
      memory_request = var.memory_request
      memory_limit   = var.memory_limit
      gomemlimit     = var.gomemlimit
      replicas       = var.replicas
    })
    # Note: default buckets are inlined as literal YAML in values.yaml.tpl.
    # An earlier version interpolated yamlencode(var.default_buckets) into the
    # template, but that broke Helm's value parsing (verified 2026-05-02 —
    # Helm received only `policy: none, purge: false` and chart defaults
    # took over, producing a 16-replica StatefulSet). Inlining is safer.
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 600
}

# -----------------------------------------------------------------------------
# ServiceMonitor — chart 5.4.0 doesn't include the ServiceMonitor template
# even with `metrics.serviceMonitor.enabled: true`. Create it manually so
# Alloy/Mimir scrape MinIO's /minio/v2/metrics/* endpoints.
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "servicemonitor" {
  manifest = yamldecode(templatefile("${path.module}/k8s/servicemonitor.yaml.tpl", {
    namespace    = kubernetes_namespace_v1.minio.metadata[0].name
    release_name = var.release_name
  }))

  depends_on = [helm_release.minio]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Services, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the external Ingresses above — those stay unconditional and
# work as-is on any environment with a real external ingress controller. This is for
# k3d standalone dev clusters. Both the API and Console Services share the
# same selector (`app: minio, release: minio`) since the chart backs both
# with the same pod, just different ports — verified via `kubectl get svc -n
# minio minio -o yaml` / `minio-console -o yaml` against the live cluster.
# -----------------------------------------------------------------------------
module "k3d_expose_api" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.minio.metadata[0].name
  service_name = var.release_name
  pod_selector = {
    "app"     = "minio"
    "release" = var.release_name
  }
  ports = [
    { name = "http", target_port = 9000, node_port = var.k3d_api_node_port },
  ]

  depends_on = [helm_release.minio]
}

module "k3d_expose_console" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.minio.metadata[0].name
  service_name = "${var.release_name}-console"
  pod_selector = {
    "app"     = "minio"
    "release" = var.release_name
  }
  ports = [
    { name = "http", target_port = 9001, node_port = var.k3d_console_node_port },
  ]

  depends_on = [helm_release.minio]
}
