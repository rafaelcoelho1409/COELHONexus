# =============================================================================
# mimir module — Grafana Mimir (Prometheus-compatible long-term metrics)
# =============================================================================
#
# Deploys:
#   1. mimir namespace
#   2. Bootstrap Job — creates 3 MinIO buckets (blocks/ruler/alertmanager)
#      via mc CLI. Idempotent (`mc mb --ignore-existing`).
#   3. mimir-distributed Helm release (chart 6.0.6, appVersion 3.0.4) with:
#        - All components at replicas=1, RF=1 (homelab single-node)
#        - S3 backend = v2 MinIO (in-cluster, HTTP)
#        - Caches OFF (chunks/index/metadata/results) — RAM trade
#        - Alertmanager OFF (Grafana's unified alerting handles routing)
#        - Kafka ingest_storage OFF (using classic remote_write)
#        - metaMonitoring ON: ServiceMonitor + recording rules + dashboards
#   4. Tailscale Ingress at mimir.<domain> → gateway:8080 (Homepage tile)
#   5. Grafana datasource ConfigMap (label `grafana_datasource: "1"`) so
#      Grafana's sidecar auto-imports it. UID 'mimir' is referenced by
#      every dashboard ConfigMap shipped from other modules.
#
# v2 baseline DBs (per memory feedback_default_to_v2_baseline_dbs):
#   - chart's bundled MinIO sub-chart DISABLED — we use v2 central MinIO
#   - chart's bundled Memcached sub-charts DISABLED — RAM trade
#
# Credential reuse (per memory feedback_secrets_reuse):
#   minio_access_key / minio_secret_key are reused from v2 MinIO module
#   (same values as v1 — admin / <SOPS-stored root_password>).
# =============================================================================

# -----------------------------------------------------------------------------
# Locals — endpoint format conversion
# -----------------------------------------------------------------------------
# mc CLI (bootstrap Job) wants the full URL with scheme:
#     http://minio.minio.svc.cluster.local:9000
# thanos-io/objstore S3 client (Mimir runtime) wants host:port ONLY (no
# scheme, no path) — the `insecure: true` config flag selects HTTP.
# Build both from the single user-provided var.minio_endpoint.
# -----------------------------------------------------------------------------
locals {
  minio_endpoint_host = replace(replace(var.minio_endpoint, "https://", ""), "http://", "")
}

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "mimir" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "mimir"
      "app.kubernetes.io/component"  = "metrics-storage"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — create the 3 MinIO buckets idempotently
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "create_buckets" {
  metadata {
    name      = "${var.release_name}-create-buckets"
    namespace = kubernetes_namespace_v1.mimir.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "mimir-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "mimir-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "create-buckets"
          image = "minio/mc:latest"

          env {
            name  = "MINIO_ENDPOINT"
            value = var.minio_endpoint
          }
          env {
            name  = "MINIO_ACCESS_KEY"
            value = var.minio_access_key
          }
          env {
            name  = "MINIO_SECRET_KEY"
            value = var.minio_secret_key
          }
          env {
            name  = "BLOCKS_BUCKET"
            value = var.blocks_bucket
          }
          env {
            name  = "RULER_BUCKET"
            value = var.ruler_bucket
          }
          env {
            name  = "AM_BUCKET"
            value = var.alertmanager_bucket
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            echo "=== Configuring mc client against $MINIO_ENDPOINT ==="
            mc alias set minio "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

            echo "=== Creating Mimir buckets (idempotent) ==="
            mc mb --ignore-existing "minio/$BLOCKS_BUCKET"
            mc mb --ignore-existing "minio/$RULER_BUCKET"
            mc mb --ignore-existing "minio/$AM_BUCKET"

            echo "=== Verifying ==="
            mc ls "minio/$BLOCKS_BUCKET"
            mc ls "minio/$RULER_BUCKET"
            mc ls "minio/$AM_BUCKET"

            echo "=== Done. ==="
          EOT
          ]

          resources {
            requests = {
              cpu    = "10m"
              memory = "32Mi"
            }
            limits = {
              memory = "64Mi"
            }
          }
        }
      }
    }
  }

  wait_for_completion = true
  timeouts {
    create = "5m"
  }

  depends_on = [kubernetes_namespace_v1.mimir]
}

# -----------------------------------------------------------------------------
# Helm release — grafana/mimir-distributed
# -----------------------------------------------------------------------------
resource "helm_release" "mimir" {
  name       = var.release_name
  repository = "https://grafana.github.io/helm-charts"
  chart      = "mimir-distributed"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.mimir.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      minio_endpoint   = local.minio_endpoint_host # host:port only — thanos-io rejects schemes
      minio_access_key = var.minio_access_key
      minio_secret_key = var.minio_secret_key

      blocks_bucket       = var.blocks_bucket
      ruler_bucket        = var.ruler_bucket
      alertmanager_bucket = var.alertmanager_bucket
      retention_period    = var.retention_period

      storage_class = var.storage_class

      ingester_cpu_request    = var.ingester_cpu_request
      ingester_memory_request = var.ingester_memory_request
      ingester_memory_limit   = var.ingester_memory_limit
      ingester_pvc_size       = var.ingester_pvc_size

      distributor_memory_request = var.distributor_memory_request
      distributor_memory_limit   = var.distributor_memory_limit

      querier_memory_request = var.querier_memory_request
      querier_memory_limit   = var.querier_memory_limit

      query_frontend_memory_request = var.query_frontend_memory_request
      query_frontend_memory_limit   = var.query_frontend_memory_limit

      store_gateway_memory_request = var.store_gateway_memory_request
      store_gateway_memory_limit   = var.store_gateway_memory_limit
      store_gateway_pvc_size       = var.store_gateway_pvc_size

      compactor_memory_request = var.compactor_memory_request
      compactor_memory_limit   = var.compactor_memory_limit
      compactor_pvc_size       = var.compactor_pvc_size

      ruler_memory_request = var.ruler_memory_request
      ruler_memory_limit   = var.ruler_memory_limit

      query_scheduler_memory_request = var.query_scheduler_memory_request
      query_scheduler_memory_limit   = var.query_scheduler_memory_limit

      gateway_memory_request = var.gateway_memory_request
      gateway_memory_limit   = var.gateway_memory_limit
    })
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 900 # Mimir has many components — first install is slow

  depends_on = [
    kubernetes_job_v1.create_buckets,
  ]
}

# -----------------------------------------------------------------------------
# NOTE: No external Tailscale Ingress for Mimir.
# -----------------------------------------------------------------------------
# Mimir has no UI (only a Prometheus-compatible API); all viewing happens
# through Grafana via the datasource ConfigMap below. Same applies to Loki,
# Tempo, and Alloy in this stack — only Grafana gets external exposure +
# Homepage tile. Ad-hoc API debugging from outside the cluster is done via
# `kubectl port-forward svc/mimir-gateway 8080:80 -n mimir` or by curling
# the in-cluster Service from another pod.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Grafana datasource — ConfigMap with label `grafana_datasource: "1"`
# -----------------------------------------------------------------------------
# Grafana's sidecar (in the grafana namespace) watches all namespaces for
# ConfigMaps with this label and imports them as datasources at runtime —
# no Grafana restart required.
#
# The datasource UID is `mimir` (lowercase), matching the convention in the
# v1 dashboards.tf script (which rewrites Prometheus UID → mimir on the way
# in). Future dashboard ConfigMaps reference this UID.
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "grafana_datasource" {
  manifest = yamldecode(templatefile("${path.module}/k8s/datasource.yaml.tpl", {
    namespace       = kubernetes_namespace_v1.mimir.metadata[0].name
    gateway_service = "${var.release_name}-gateway"
  }))

  depends_on = [helm_release.mimir]
}
