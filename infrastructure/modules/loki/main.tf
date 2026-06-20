# =============================================================================
# loki module — Grafana Loki (LogQL log aggregation, monolithic mode)
# =============================================================================
#
# Deploys:
#   1. loki namespace
#   2. MinIO credentials Secret (envFrom for the SingleBinary pod — keeps
#      keys out of the rendered values ConfigMap)
#   3. Bootstrap Job — creates 2 MinIO buckets (chunks/ruler) idempotently
#   4. grafana-community/loki Helm release (chart 13.5.0, appVersion 3.7.1):
#        - deploymentMode: Monolithic (chart default; SimpleScalable is being
#          deprecated in Loki 4.0 anyway)
#        - SingleBinary StatefulSet, replicas=1
#        - Schema v13 + TSDB store (modern, BoltDB-shipper deprecated)
#        - S3 backend = v2 MinIO
#        - Caches OFF (singleBinary handles internally)
#        - Gateway OFF (singleBinary Service handles all traffic — push + query)
#        - Test pods + lokiCanary OFF (homelab noise)
#        - monitoring on: ServiceMonitor + dashboards + alert rules off
#   5. Grafana datasource ConfigMap (label `grafana_datasource: "1"`) so
#      Grafana's sidecar auto-imports it as UID `loki`. No Loki restart needed.
#
# No external Tailscale Ingress / Homepage tile (per memory:
# feedback_no_external_ingress_for_uiless_backends). All viewing happens
# through Grafana → Explore (Loki).
#
# Chart migration note: grafana/loki was deprecated 2026-03-16 → moved to
# grafana-community/loki (numbering jumped 6.55 → 13.x).
# =============================================================================

# -----------------------------------------------------------------------------
# Locals — endpoint format conversion (same gotcha as Mimir module)
# -----------------------------------------------------------------------------
# mc CLI (bootstrap Job) wants the full URL with scheme:
#     http://minio.minio.svc.cluster.local:9000
# The Loki S3 client (thanos-style) wants host:port ONLY (no scheme); the
# `insecure: true` flag selects HTTP. Build both from var.minio_endpoint.
# -----------------------------------------------------------------------------
locals {
  minio_endpoint_host = replace(replace(var.minio_endpoint, "https://", ""), "http://", "")
}

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "loki" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "loki"
      "app.kubernetes.io/component"  = "logs-storage"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# MinIO credentials Secret — surfaces creds as env vars on the Loki pod
# -----------------------------------------------------------------------------
# The Loki Helm chart accepts S3 creds via `loki.storage.s3.accessKeyId` /
# `secretAccessKey`, but wiring them through a Secret + envFrom keeps the
# rendered values ConfigMap clean (no plaintext creds sitting in the
# `<release>-config` ConfigMap that's visible to anyone with kubectl get).
# Loki reads $AWS_ACCESS_KEY_ID and $AWS_SECRET_ACCESS_KEY from env when
# the values config uses `accessKeyId: "${AWS_ACCESS_KEY_ID}"` style refs.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "minio_credentials" {
  metadata {
    name      = "${var.release_name}-minio-credentials"
    namespace = kubernetes_namespace_v1.loki.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "loki"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    AWS_ACCESS_KEY_ID     = var.minio_access_key
    AWS_SECRET_ACCESS_KEY = var.minio_secret_key
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — create the 2 MinIO buckets idempotently
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "create_buckets" {
  metadata {
    name      = "${var.release_name}-create-buckets"
    namespace = kubernetes_namespace_v1.loki.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "loki-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "loki-bootstrap"
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
            name  = "CHUNKS_BUCKET"
            value = var.chunks_bucket
          }
          env {
            name  = "RULER_BUCKET"
            value = var.ruler_bucket
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            echo "=== Configuring mc client against $MINIO_ENDPOINT ==="
            mc alias set minio "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

            echo "=== Creating Loki buckets (idempotent) ==="
            mc mb --ignore-existing "minio/$CHUNKS_BUCKET"
            mc mb --ignore-existing "minio/$RULER_BUCKET"

            echo "=== Verifying ==="
            mc ls "minio/$CHUNKS_BUCKET"
            mc ls "minio/$RULER_BUCKET"

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

  depends_on = [kubernetes_namespace_v1.loki]
}

# -----------------------------------------------------------------------------
# Helm release — grafana-community/loki
# -----------------------------------------------------------------------------
resource "helm_release" "loki" {
  name       = var.release_name
  repository = "https://grafana-community.github.io/helm-charts"
  chart      = "loki"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.loki.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      minio_endpoint           = local.minio_endpoint_host # host:port only
      minio_credentials_secret = kubernetes_secret_v1.minio_credentials.metadata[0].name
      chunks_bucket            = var.chunks_bucket
      ruler_bucket             = var.ruler_bucket
      retention_period         = var.retention_period
      ingestion_rate_mb        = var.ingestion_rate_mb
      ingestion_burst_size_mb  = var.ingestion_burst_size_mb
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
    kubernetes_job_v1.create_buckets,
    kubernetes_secret_v1.minio_credentials,
  ]
}

# -----------------------------------------------------------------------------
# Grafana datasource — ConfigMap with label `grafana_datasource: "1"`
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "grafana_datasource" {
  manifest = yamldecode(templatefile("${path.module}/k8s/datasource.yaml.tpl", {
    namespace    = kubernetes_namespace_v1.loki.metadata[0].name
    service_name = var.release_name
  }))

  depends_on = [helm_release.loki]
}
