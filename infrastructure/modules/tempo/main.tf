# =============================================================================
# tempo module — Grafana Tempo (distributed traces, single-binary mode)
# =============================================================================
#
# Deploys:
#   1. tempo namespace
#   2. MinIO credentials Secret (envFrom for the Tempo pod — keeps keys out
#      of the rendered values ConfigMap)
#   3. Bootstrap Job — creates the tempo-traces MinIO bucket idempotently
#   4. grafana-community/tempo Helm release (chart 2.1.0, appVersion 2.10.1)
#      in single-binary StatefulSet mode (replicas=1):
#        - S3 backend = v2 MinIO
#        - OTLP receivers (gRPC 4317 + HTTP 4318) — modern default
#        - Optional legacy receivers (Jaeger/Zipkin) gated by var
#        - ServiceMonitor on (Alloy/Mimir scrapes Tempo's own /metrics)
#        - Persistence enabled (5Gi for WAL + recent blocks before S3 ship)
#        - 30d retention via compactor block_retention
#   5. Grafana datasource ConfigMap (label `grafana_datasource: "1"`) so
#      Grafana's sidecar auto-imports it as UID `tempo`. Datasource also
#      wires up tracesToLogs (loki) + tracesToMetrics (mimir) + serviceMap
#      (mimir) + nodeGraph for the full LGTM cross-pillar UX.
#
# No external Ingress / Homepage tile (per memory:
# feedback_no_external_ingress_for_uiless_backends). All viewing happens
# through Grafana → Explore (Tempo).
#
# Chart migration note: grafana/tempo was deprecated → moved to
# grafana-community/tempo (numbering jumped 1.24 → 2.x).
# =============================================================================

# -----------------------------------------------------------------------------
# Locals — endpoint format conversion (same gotcha as Mimir/Loki)
# -----------------------------------------------------------------------------
# mc CLI (bootstrap Job) wants the full URL with scheme.
# Tempo's S3 client (thanos-style) wants host:port ONLY (no scheme); the
# `insecure: true` flag selects HTTP. Build both from var.minio_endpoint.
# -----------------------------------------------------------------------------
locals {
  minio_endpoint_host = replace(replace(var.minio_endpoint, "https://", ""), "http://", "")
}

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "tempo" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "tempo"
      "app.kubernetes.io/component"  = "traces-storage"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# MinIO credentials Secret — surfaces creds as env vars on the Tempo pod
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "minio_credentials" {
  metadata {
    name      = "${var.release_name}-minio-credentials"
    namespace = kubernetes_namespace_v1.tempo.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "tempo"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    AWS_ACCESS_KEY_ID     = var.minio_access_key
    AWS_SECRET_ACCESS_KEY = var.minio_secret_key
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — create the MinIO bucket idempotently
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "create_bucket" {
  metadata {
    name      = "${var.release_name}-create-bucket"
    namespace = kubernetes_namespace_v1.tempo.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "tempo-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "tempo-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "create-bucket"
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
            name  = "TRACES_BUCKET"
            value = var.traces_bucket
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            echo "=== Configuring mc client against $MINIO_ENDPOINT ==="
            mc alias set minio "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

            echo "=== Creating Tempo bucket (idempotent) ==="
            mc mb --ignore-existing "minio/$TRACES_BUCKET"

            echo "=== Verifying ==="
            mc ls "minio/$TRACES_BUCKET"

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

  depends_on = [kubernetes_namespace_v1.tempo]
}

# -----------------------------------------------------------------------------
# Helm release — grafana-community/tempo
# -----------------------------------------------------------------------------
resource "helm_release" "tempo" {
  name       = var.release_name
  repository = "https://grafana-community.github.io/helm-charts"
  chart      = "tempo"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.tempo.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      minio_endpoint           = local.minio_endpoint_host # host:port only
      minio_credentials_secret = kubernetes_secret_v1.minio_credentials.metadata[0].name
      traces_bucket            = var.traces_bucket
      retention_period         = var.retention_period
      enable_legacy_receivers  = var.enable_legacy_receivers ? "true" : "false"
      cpu_request              = var.cpu_request
      memory_request           = var.memory_request
      memory_limit             = var.memory_limit
      storage_class            = var.storage_class
      storage_size             = var.storage_size
    })
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 600

  depends_on = [
    kubernetes_job_v1.create_bucket,
    kubernetes_secret_v1.minio_credentials,
  ]
}

# -----------------------------------------------------------------------------
# Grafana datasource — ConfigMap with label `grafana_datasource: "1"`
# -----------------------------------------------------------------------------
# Datasource wires up the cross-pillar UX:
#   - tracesToLogs   → loki  (click span → see logs around that time/trace)
#   - tracesToMetrics → mimir (click span → service-graph metrics)
#   - serviceMap      → mimir (visualize service topology from RED metrics)
#   - nodeGraph       → enabled (Grafana's built-in trace graph)
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "grafana_datasource" {
  manifest = yamldecode(templatefile("${path.module}/k8s/datasource.yaml.tpl", {
    namespace    = kubernetes_namespace_v1.tempo.metadata[0].name
    service_name = var.release_name
  }))

  depends_on = [helm_release.tempo]
}
