# =============================================================================
# qdrant module — Vector database with snapshot backups to MinIO
# =============================================================================
#
# Deploys:
#   1. qdrant namespace
#   2. API key Secret (chart's `apiKey.secretKeyRef` target; reused from v1 SOPS)
#   3. MinIO creds Secret for the backup CronJob (env-from)
#   4. Bootstrap Job — verify the `backups` MinIO bucket exists (idempotent)
#   5. qdrant/qdrant Helm release (chart 1.17.1, appVersion v1.17.1):
#        - Single replica (homelab; clustering requires RWX storage)
#        - 5Gi data PVC + 5Gi snapshot PVC (trimmed from v1's 10Gi each)
#        - ServiceMonitor enabled (Alloy auto-scrapes via prometheus.operator)
#        - API-key auth on (read from Secret via env)
#   6. Tailscale Ingress at qdrant.<domain> + Homepage tile (Databases group)
#   7. Backup CronJob — every 6h (configurable):
#        - Init container: alpine + curl/jq → POST /collections/*/snapshots
#        - Main container: minio/mc → upload snapshots to MinIO
#        - Retention: keep last N snapshots per collection (default 20)
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "qdrant" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "qdrant"
      "app.kubernetes.io/component"  = "vector-db"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# API key Secret — chart reads via apiKey.secretKeyRef
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "api_key" {
  metadata {
    name      = "${var.release_name}-api-key"
    namespace = kubernetes_namespace_v1.qdrant.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "qdrant"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    api-key = var.api_key
  }
}

# -----------------------------------------------------------------------------
# Backup CronJob env-from Secret — MinIO creds + Qdrant API key
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "backup_creds" {
  metadata {
    name      = "${var.release_name}-backup-creds"
    namespace = kubernetes_namespace_v1.qdrant.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "qdrant-backup"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    MINIO_ENDPOINT   = var.minio_endpoint
    MINIO_ACCESS_KEY = var.minio_access_key
    MINIO_SECRET_KEY = var.minio_secret_key
    MINIO_BUCKET     = var.backup_bucket
    QDRANT_API_KEY   = var.api_key
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — ensure the backup bucket exists (idempotent)
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "ensure_bucket" {
  metadata {
    name      = "${var.release_name}-ensure-bucket"
    namespace = kubernetes_namespace_v1.qdrant.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "qdrant-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "qdrant-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "mc"
          image = "minio/mc:latest"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.backup_creds.metadata[0].name
            }
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            mc alias set m "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
            mc mb --ignore-existing "m/$MINIO_BUCKET"
            echo "Bucket $MINIO_BUCKET ready."
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

  depends_on = [kubernetes_secret_v1.backup_creds]
}

# -----------------------------------------------------------------------------
# Helm release — qdrant/qdrant
# -----------------------------------------------------------------------------
resource "helm_release" "qdrant" {
  name       = var.release_name
  repository = "https://qdrant.github.io/qdrant-helm"
  chart      = "qdrant"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.qdrant.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      replicas                = var.replicas
      storage_size            = var.storage_size
      snapshot_storage_size   = var.snapshot_storage_size
      storage_class           = var.storage_class
      cpu_request             = var.cpu_request
      memory_request          = var.memory_request
      memory_limit            = var.memory_limit
      api_key_secret          = kubernetes_secret_v1.api_key.metadata[0].name
      service_monitor_enabled = var.service_monitor_enabled ? "true" : "false"
    })
  ]

  wait    = true
  timeout = 600

  depends_on = [
    kubernetes_secret_v1.api_key,
    kubernetes_job_v1.ensure_bucket,
  ]
}

# -----------------------------------------------------------------------------
# Tailscale Ingress
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.qdrant.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
    tailscale_domain   = var.tailscale_domain
    ingress_class_name = var.tailscale_ingress_class
  }))

  depends_on = [helm_release.qdrant]
}

# -----------------------------------------------------------------------------
# Backup CronJob — snapshot every 6h to MinIO
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace        = kubernetes_namespace_v1.qdrant.metadata[0].name
    release_name     = var.release_name
    qdrant_url       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:6333"
    backup_schedule  = var.backup_schedule
    backup_retention = var.backup_retention
    creds_secret     = kubernetes_secret_v1.backup_creds.metadata[0].name
  }))

  depends_on = [
    helm_release.qdrant,
    kubernetes_secret_v1.backup_creds,
  ]
}
