# =============================================================================
# redis module — Bitnami Redis with Redis Stack image
# =============================================================================
#
# Deploys:
#   1. redis namespace
#   2. Bitnami redis Helm release (chart 25.4.1, redis-stack-server image)
#   3. Secret with MinIO backup credentials
#   4. Daily backup CronJob: BGSAVE + RDB → MinIO `backups/redis/`
#   5. (optional) Tailscale-exposed Service for external redis-cli access
#
# v1 → v2 changes:
#   - Chart: bitnami/redis (whichever) → 25.4.1
#   - Dropped `cluster_ready` boolean dependency annotation (v1 anti-pattern)
#   - Tailscale exposure now optional (was always-on in v1)
#   - Image still redis/redis-stack-server (RediSearch/JSON/TS/Bloom modules)
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "redis" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "redis"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Redis Helm release
# -----------------------------------------------------------------------------
resource "helm_release" "redis" {
  name       = var.release_name
  repository = "oci://registry-1.docker.io/bitnamicharts"
  chart      = "redis"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.redis.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      redis_stack_version   = var.redis_stack_version
      redis_password        = var.redis_password
      storage_class         = var.storage_class
      storage_size          = var.storage_size
      maxmemory             = var.maxmemory
      cpu_request           = var.cpu_request
      memory_request        = var.memory_request
      memory_limit          = var.memory_limit
      enable_servicemonitor = var.enable_servicemonitor
    })
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 600
}

# -----------------------------------------------------------------------------
# Secret with MinIO backup creds (consumed by backup CronJob)
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "minio_backup" {
  count = var.enable_backup_cronjob ? 1 : 0

  metadata {
    name      = "${var.release_name}-minio-backup"
    namespace = kubernetes_namespace_v1.redis.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "redis-backup"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    MINIO_ACCESS_KEY = var.minio_access_key
    MINIO_SECRET_KEY = var.minio_secret_key
    MINIO_ENDPOINT   = var.minio_endpoint
    MINIO_BUCKET     = var.minio_bucket
    REDISCLI_AUTH    = var.redis_password # redis-cli uses this env var for auth
  }

  depends_on = [helm_release.redis]
}

# -----------------------------------------------------------------------------
# Backup CronJob
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  count = var.enable_backup_cronjob ? 1 : 0

  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace        = kubernetes_namespace_v1.redis.metadata[0].name
    release_name     = var.release_name
    backup_schedule  = var.backup_schedule
    backup_retention = var.backup_retention
  }))

  depends_on = [
    helm_release.redis,
    kubernetes_secret_v1.minio_backup,
  ]
}

# -----------------------------------------------------------------------------
# Tailscale TCP exposure (optional)
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "tailscale_service" {
  count = var.enable_tailscale_exposure ? 1 : 0

  manifest = yamldecode(templatefile("${path.module}/k8s/service-tailscale.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.redis.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
  }))

  depends_on = [helm_release.redis]
}
