# =============================================================================
# postgresql module — Bitnami PostgreSQL 18 (standalone) + backup CronJob
# =============================================================================
#
# Deploys:
#   1. postgresql namespace
#   2. Bitnami postgresql Helm release (chart 18.6.2, app v18)
#      - StatefulSet, single primary, ClusterIP-only
#      - Includes postgres_exporter + ServiceMonitor for Mimir
#      - Tuned postgresql.conf for ~384Mi memory limit
#   3. Secret with MinIO backup credentials
#   4. (Optional) Daily backup CronJob: pg_dump → gzip → MinIO `backups` bucket
#
# v1 → v2 changes:
#   - Chart: 18.2.3 → 18.6.2 (latest stable, same appVersion family)
#   - Dropped `cluster_ready` boolean dependency (anti-pattern per playbook)
#   - Dropped password-sync Job hack (Bitnami 18.x handles password updates;
#     if you change admin_password, manually re-run the Helm release;
#     the chart will trigger StatefulSet rollout to pick up the new Secret)
#   - Multi-database init removed — each app's module creates its own DB
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "postgresql" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "postgresql"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# PostgreSQL Helm release
# -----------------------------------------------------------------------------
# Repository note: Bitnami publishes on both OCI and HTTPS. The HTTPS repo
# is more compatible with older helm CLIs; OCI is fine on helm ≥3.8 which
# we have. Using OCI for consistency with v2's other Bitnami modules.
# -----------------------------------------------------------------------------
resource "helm_release" "postgresql" {
  name       = var.release_name
  repository = "oci://registry-1.docker.io/bitnamicharts"
  chart      = "postgresql"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.postgresql.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      admin_user            = var.admin_user
      admin_password        = var.admin_password
      default_database      = var.default_database
      storage_class         = var.storage_class
      storage_size          = var.storage_size
      cpu_request           = var.cpu_request
      memory_request        = var.memory_request
      memory_limit          = var.memory_limit
      max_connections       = var.max_connections
      shared_buffers        = var.shared_buffers
      effective_cache_size  = var.effective_cache_size
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
# Created only when backups are enabled. Holds:
#   MINIO_ACCESS_KEY, MINIO_SECRET_KEY — MinIO root creds
#   MINIO_ENDPOINT                     — in-cluster S3 URL
#   MINIO_BUCKET                       — target bucket (default: backups)
#   PGPASSWORD                         — for pg_dump's libpq env-var auth
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "minio_backup" {
  count = var.enable_backup_cronjob ? 1 : 0

  metadata {
    name      = "${var.release_name}-minio-backup"
    namespace = kubernetes_namespace_v1.postgresql.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "postgresql-backup"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    MINIO_ACCESS_KEY = var.minio_access_key
    MINIO_SECRET_KEY = var.minio_secret_key
    MINIO_ENDPOINT   = var.minio_endpoint
    MINIO_BUCKET     = var.minio_bucket
    PGPASSWORD       = var.admin_password
  }

  depends_on = [helm_release.postgresql]
}

# -----------------------------------------------------------------------------
# Backup CronJob — pg_dump → MinIO
# -----------------------------------------------------------------------------
# Built-in Ingress kind isn't enough here — CronJob is a built-in resource
# kind too, plain kubernetes_manifest works.
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  count = var.enable_backup_cronjob ? 1 : 0

  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace        = kubernetes_namespace_v1.postgresql.metadata[0].name
    release_name     = var.release_name
    admin_user       = var.admin_user
    default_database = var.default_database
    backup_schedule  = var.backup_schedule
    backup_retention = var.backup_retention
  }))

  depends_on = [
    helm_release.postgresql,
    kubernetes_secret_v1.minio_backup,
  ]
}

# -----------------------------------------------------------------------------
# Tailscale TCP exposure (optional — type=LoadBalancer, loadBalancerClass=tailscale)
# -----------------------------------------------------------------------------
# When enabled, creates a SECOND Service alongside the chart-managed ClusterIP.
# The Tailscale operator detects the loadBalancerClass=tailscale and
# provisions a proxy pod that registers `<tailscale_hostname>.<domain>` on
# the tailnet, routing inbound TCP 5432 to the primary Postgres pod.
#
# Both services co-exist:
#   - chart's ClusterIP: in-cluster app traffic (5432)
#   - this LoadBalancer:   external psql access from any tailnet device
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "tailscale_service" {
  count = var.enable_tailscale_exposure ? 1 : 0

  manifest = yamldecode(templatefile("${path.module}/k8s/service-tailscale.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.postgresql.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
  }))

  depends_on = [helm_release.postgresql]
}
