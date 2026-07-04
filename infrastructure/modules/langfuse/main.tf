# =============================================================================
# langfuse module — Langfuse 3.x LLM observability + tracing
# =============================================================================
#
# v2 design vs v1:
#   - chart 1.5.29 (appVersion 3.172.1) — bumped from v1's 1.5.27 / 3.169.0
#   - bundled Postgres + Valkey + MinIO sub-charts DISABLED → v2 baseline
#     (Postgres + shared `backups` bucket + Redis DB index 3)
#   - ClickHouse stays bundled (no v2 ClickHouse exists; chart's only path
#     for ingestion storage)
#   - all secrets live in 4 module-managed K8s Secrets (langfuse-app,
#     langfuse-postgres, langfuse-redis-password, langfuse-clickhouse,
#     langfuse-minio); chart pulls via existingSecret / secretKeyRef
#   - typed kubernetes_job_v1 for both bootstrap Jobs (per Airflow lesson —
#     kubernetes_manifest mishandles K8s's auto-injected Job pod labels)
#   - daily pg_dump CronJob → MinIO backups/langfuse/postgres/
#
# Apply order:
#   1. Namespace + 5 Secrets
#   2. Bootstrap Jobs: create Postgres role/db + ensure MinIO bucket
#   3. Helm release (langfuse/langfuse)
#   4. External Ingress + Backup CronJob
# =============================================================================

locals {
  labels = {
    "app.kubernetes.io/name"       = "langfuse"
    "app.kubernetes.io/component"  = "ai-platform"
    "app.kubernetes.io/managed-by" = "terraform"
  }

  pg_secret_name         = "langfuse-postgres"
  pg_admin_secret_name   = "langfuse-postgres-admin"
  redis_secret_name      = "langfuse-redis-password"
  clickhouse_secret_name = "langfuse-clickhouse"
  minio_secret_name      = "langfuse-minio"
  app_secret_name        = "langfuse-app"
  public_url             = trimspace(var.public_url) != "" ? trimspace(var.public_url) : "https://${var.tailscale_hostname}.${var.tailscale_domain}"
}

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "langfuse" {
  metadata {
    name   = var.namespace
    labels = local.labels
  }
}

# -----------------------------------------------------------------------------
# Secrets
# -----------------------------------------------------------------------------

# Application-level secrets: salt, encryption key, NextAuth secret, init_*.
# Chart pulls via secretKeyRef (see helm/values.yaml.tpl).
resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = local.app_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    salt                 = var.salt
    encryptionKey        = var.encryption_key
    nextauthSecret       = var.nextauth_secret
    initOrgId            = var.init_org_id
    initProjectId        = var.init_project_id
    initProjectPublicKey = var.init_project_public_key
    initProjectSecretKey = var.init_project_secret_key
    initUserEmail        = var.init_user_email
    initUserPassword     = var.init_user_password
  }
}

# Postgres credentials for the langfuse role (chart's existingSecret pattern
# expects a single `password` key for both userPasswordKey + adminPasswordKey).
resource "kubernetes_secret_v1" "postgres" {
  metadata {
    name      = local.pg_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    password = var.postgres_password
  }
}

# Postgres admin password — used only by the bootstrap_db Job (creates
# the langfuse role + database). Chart never sees this Secret.
resource "kubernetes_secret_v1" "postgres_admin" {
  metadata {
    name      = local.pg_admin_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    postgres_admin_password = var.postgres_admin_password
  }
}

# Shared v2 Redis password — chart reads `redis-password` key (matches
# the convention used by ArgoCD's externalRedis integration).
resource "kubernetes_secret_v1" "redis_password" {
  metadata {
    name      = local.redis_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    redis-password = var.redis_password
  }
}

# Bundled ClickHouse subchart auth — chart's existingSecret + existingSecretKey.
resource "kubernetes_secret_v1" "clickhouse" {
  metadata {
    name      = local.clickhouse_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    password       = var.clickhouse_password
    admin-password = var.clickhouse_password # chart sometimes looks up either key
  }
}

# MinIO credentials — consumed by chart (s3.{access,secret}.secretKeyRef),
# bootstrap_bucket Job, and backup CronJob.
resource "kubernetes_secret_v1" "minio" {
  metadata {
    name      = local.minio_secret_name
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = local.labels
  }

  data = {
    MINIO_ENDPOINT   = var.minio_endpoint
    MINIO_ACCESS_KEY = var.minio_access_key
    MINIO_SECRET_KEY = var.minio_secret_key
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — Postgres role + database
# -----------------------------------------------------------------------------
# Idempotent: CREATE if absent, ALTER password if present. Typed
# kubernetes_job_v1 to avoid the controller-uid label drift error.
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "bootstrap_db" {
  metadata {
    name      = "langfuse-bootstrap-db"
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = merge(local.labels, { "app.kubernetes.io/component" = "bootstrap" })
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "langfuse-bootstrap-db" }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "bootstrap"
          image = "postgres:16-alpine"

          env {
            name  = "PGHOST"
            value = var.postgres_host
          }
          env {
            name  = "PGPORT"
            value = tostring(var.postgres_port)
          }
          env {
            name  = "PGUSER"
            value = var.postgres_admin_user
          }
          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.postgres_admin.metadata[0].name
                key  = "postgres_admin_password"
              }
            }
          }
          env {
            name  = "TARGET_DB"
            value = var.postgres_database
          }
          env {
            name  = "TARGET_USER"
            value = var.postgres_user
          }
          env {
            name  = "TARGET_PASSWORD"
            value = var.postgres_password
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            echo "[1/3] Wait for Postgres..."
            for i in $(seq 1 30); do
              if psql -c '\q' postgres 2>/dev/null; then
                echo "Postgres reachable."
                break
              fi
              sleep 2
            done

            echo "[2/3] Ensure role $${TARGET_USER}..."
            psql postgres <<SQL
              DO \$\$
              BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$${TARGET_USER}') THEN
                  CREATE ROLE $${TARGET_USER} LOGIN PASSWORD '$${TARGET_PASSWORD}';
                ELSE
                  ALTER ROLE $${TARGET_USER} WITH PASSWORD '$${TARGET_PASSWORD}';
                END IF;
              END \$\$;
            SQL

            echo "[3/3] Ensure database $${TARGET_DB} owned by $${TARGET_USER}..."
            psql postgres <<SQL
              SELECT 'CREATE DATABASE $${TARGET_DB} OWNER $${TARGET_USER}'
              WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$${TARGET_DB}')
              \gexec
            SQL
            psql postgres -c "GRANT ALL PRIVILEGES ON DATABASE $${TARGET_DB} TO $${TARGET_USER};"
            psql -d "$${TARGET_DB}" -c "GRANT ALL ON SCHEMA public TO $${TARGET_USER};"
            echo "Bootstrap complete."
          EOT
          ]

          resources {
            requests = {
              cpu    = "10m"
              memory = "32Mi"
            }
            limits = {
              memory = "128Mi"
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

  depends_on = [kubernetes_secret_v1.postgres_admin]
}

# -----------------------------------------------------------------------------
# Bootstrap Job — ensure MinIO bucket exists
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "bootstrap_bucket" {
  metadata {
    name      = "langfuse-bootstrap-bucket"
    namespace = kubernetes_namespace_v1.langfuse.metadata[0].name
    labels    = merge(local.labels, { "app.kubernetes.io/component" = "bootstrap" })
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "langfuse-bootstrap-bucket" }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "mc"
          image = "minio/mc:latest"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.minio.metadata[0].name
            }
          }
          env {
            name  = "BUCKET"
            value = var.artifacts_bucket
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            mc alias set m "$$MINIO_ENDPOINT" "$$MINIO_ACCESS_KEY" "$$MINIO_SECRET_KEY"
            mc mb --ignore-existing "m/$$BUCKET"
            echo "Bucket $$BUCKET ready."
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

  depends_on = [kubernetes_secret_v1.minio]
}

# -----------------------------------------------------------------------------
# Helm release — langfuse/langfuse
# -----------------------------------------------------------------------------
resource "helm_release" "langfuse" {
  name       = var.release_name
  repository = "https://langfuse.github.io/langfuse-k8s"
  chart      = "langfuse"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.langfuse.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      chart_version             = var.chart_version
      public_url                = local.public_url
      postgres_host             = var.postgres_host
      postgres_port             = var.postgres_port
      postgres_database         = var.postgres_database
      postgres_user             = var.postgres_user
      postgres_password         = var.postgres_password
      redis_host                = var.redis_host
      redis_port                = var.redis_port
      redis_db                  = var.redis_db
      minio_endpoint            = var.minio_endpoint
      artifacts_bucket          = var.artifacts_bucket
      artifacts_prefix          = var.artifacts_prefix
      clickhouse_storage_size   = var.clickhouse_storage_size
      clickhouse_memory_request = var.clickhouse_memory_request
      clickhouse_memory_limit   = var.clickhouse_memory_limit
      web_cpu_request           = var.web_cpu_request
      web_memory_request        = var.web_memory_request
      web_memory_limit          = var.web_memory_limit
      worker_cpu_request        = var.worker_cpu_request
      worker_memory_request     = var.worker_memory_request
      worker_memory_limit       = var.worker_memory_limit

      # Tier 1 RAM optimization (2026-05-25)
      log_level                        = var.log_level
      node_max_old_space_size_mb       = var.node_max_old_space_size_mb
      redis_blocking_socket_timeout_ms = var.redis_blocking_socket_timeout_ms
      s3_concurrent_reads              = var.s3_concurrent_reads
      s3_concurrent_writes             = var.s3_concurrent_writes
      clickhouse_write_interval_ms     = var.clickhouse_write_interval_ms

      # CH server cap + worker queue concurrency — see variables.tf rationale
      clickhouse_max_concurrent_queries = var.clickhouse_max_concurrent_queries
      ingestion_queue_concurrency       = var.ingestion_queue_concurrency
      trace_upsert_worker_concurrency   = var.trace_upsert_worker_concurrency
      otel_ingestion_queue_concurrency  = var.otel_ingestion_queue_concurrency

      # Feature queue toggles (true → BullMQ consumer runs; false → queue disabled)
      enable_otel_ingestion       = var.enable_otel_ingestion
      enable_posthog_integration  = var.enable_posthog_integration
      enable_mixpanel_integration = var.enable_mixpanel_integration
      enable_notification_queue   = var.enable_notification_queue
    })
  ]

  # 15 min — first install: ClickHouse boot + Prisma migrations + ClickHouse
  # migrations + LANGFUSE_INIT_* seeding can stack to ~5-8 min on this host.
  timeout       = 900
  wait          = true
  wait_for_jobs = true

  depends_on = [
    kubernetes_secret_v1.app,
    kubernetes_secret_v1.postgres,
    kubernetes_secret_v1.redis_password,
    kubernetes_secret_v1.clickhouse,
    kubernetes_secret_v1.minio,
    kubernetes_job_v1.bootstrap_db,
    kubernetes_job_v1.bootstrap_bucket,
  ]
}

# -----------------------------------------------------------------------------
# Backup CronJob — daily pg_dump → MinIO
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace         = kubernetes_namespace_v1.langfuse.metadata[0].name
    release_name      = var.release_name
    schedule          = var.backup_schedule
    postgres_host     = var.postgres_host
    postgres_port     = tostring(var.postgres_port)
    postgres_user     = var.postgres_user
    postgres_database = var.postgres_database
    bucket            = var.artifacts_bucket
    prefix            = var.artifacts_prefix
    retention_days    = tostring(var.backup_retention_days)
    pg_secret_name    = local.pg_secret_name
    minio_secret_name = local.minio_secret_name
  }))

  depends_on = [helm_release.langfuse]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Service, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the external Ingress above — that stays unconditional and
# works as-is on any environment with a real external ingress controller. This is for
# k3d standalone dev clusters. Selector matches the chart's `langfuse-web`
# Service (`app: web, app.kubernetes.io/instance: langfuse,
# app.kubernetes.io/name: langfuse`), verified via `kubectl get svc -n
# langfuse langfuse-web -o yaml` against the live cluster. ClickHouse's ports
# are internal-only and deliberately not exposed here.
# -----------------------------------------------------------------------------
module "k3d_expose" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.langfuse.metadata[0].name
  service_name = "${var.release_name}-web"
  pod_selector = {
    "app"                        = "web"
    "app.kubernetes.io/instance" = var.release_name
    "app.kubernetes.io/name"     = "langfuse"
  }
  ports = [
    { name = "http", target_port = 3000, node_port = var.k3d_web_node_port },
  ]

  depends_on = [helm_release.langfuse]
}
