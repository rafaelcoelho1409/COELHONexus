# =============================================================================
# postgresql module — inputs
# =============================================================================
# Chart: bitnami/postgresql v18.6.2 (latest as of 2026-05).
# v1 ran 18.2.3 successfully — same major, predictable upgrade.
#
# Per-app database/user creation:
#   The Postgres module deploys ONLY the server with admin credentials.
#   Each downstream app (GitLab, MLflow, Langfuse, etc.) creates its own
#   database + user during its own deployment using the admin connection.
#   Same separation-of-concerns as v1 — no cross-module coupling.
# =============================================================================

# -----------------------------------------------------------------------------
# Chart + namespace
# -----------------------------------------------------------------------------

variable "chart_version" {
  description = "bitnami/postgresql chart version. Latest stable: 18.6.2 (deploys PostgreSQL 18)."
  type        = string
  default     = "18.6.2"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '18.6.2' (no 'v' prefix)."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for PostgreSQL."
  type        = string
  default     = "postgresql"
}

variable "release_name" {
  description = "Helm release name. Used as the StatefulSet/Service name (chart convention)."
  type        = string
  default     = "postgresql"
}

# -----------------------------------------------------------------------------
# Admin credentials (from SOPS — reused from v1 for migration continuity)
# -----------------------------------------------------------------------------

variable "admin_user" {
  description = "PostgreSQL admin username. Default 'postgres' matches v1 + Bitnami chart convention."
  type        = string
  default     = "postgres"
}

variable "admin_password" {
  description = "PostgreSQL admin password. Sourced from SOPS-encrypted secrets (postgres.password). Required ≥8 chars."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.admin_password) >= 8
    error_message = "admin_password must be ≥8 chars."
  }
}

variable "default_database" {
  description = "Initial database created at install time. Apps create their own DBs separately."
  type        = string
  default     = "postgres"
}

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------
# v1 ran 10Gi successfully — kept the same default. PostgreSQL 18 with the
# default WAL settings + a few small databases (rancher local mode + apps)
# stays well under 5Gi in practice. local-path supports online resize.
# -----------------------------------------------------------------------------

variable "storage_size" {
  description = "PostgreSQL PVC size. v1 default 10Gi was never approached; kept as-is for headroom."
  type        = string
  default     = "10Gi"
}

variable "storage_class" {
  description = "StorageClass for the PVC. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Resource sizing — kept from v1's measured tuning
# -----------------------------------------------------------------------------
# v1 real usage: 20m CPU, 172Mi RAM under typical homelab workload.
# Plus metrics exporter at 1m / 15Mi. Below leaves clear headroom.
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request for the Postgres container."
  type        = string
  default     = "50m"
}

variable "memory_request" {
  description = "Memory request for the Postgres container."
  type        = string
  default     = "200Mi"
}

variable "memory_limit" {
  description = "Memory limit for the Postgres container."
  type        = string
  default     = "384Mi"
}

# -----------------------------------------------------------------------------
# Postgres tuning (postgresql.conf) — kept from v1
# -----------------------------------------------------------------------------
# These knobs were measured against actual workload. Don't change without
# load-testing — defaults assume ~384Mi mem limit.
# -----------------------------------------------------------------------------

variable "max_connections" {
  description = "max_connections in postgresql.conf. 100 is more than enough for homelab apps."
  type        = number
  default     = 100
}

variable "shared_buffers" {
  description = "shared_buffers — typically ~25% of memory_limit."
  type        = string
  default     = "128MB"
}

variable "effective_cache_size" {
  description = "effective_cache_size — Postgres's hint about OS page cache. ~75% of memory_limit."
  type        = string
  default     = "384MB"
}

# -----------------------------------------------------------------------------
# Metrics + ServiceMonitor
# -----------------------------------------------------------------------------

variable "enable_servicemonitor" {
  description = "Create a ServiceMonitor for Mimir/Alloy scraping. Bitnami chart ships this natively."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Backup CronJob — pg_dump → MinIO (v2)
# -----------------------------------------------------------------------------
# Backup pattern from v1: init container runs pg_dump, main container uploads
# via `mc` to MinIO's `backups` bucket. Retention via head -N + rm.
# -----------------------------------------------------------------------------

variable "enable_backup_cronjob" {
  description = "Enable scheduled pg_dump → MinIO. Disable for dev or if MinIO isn't ready yet."
  type        = bool
  default     = true
}

variable "backup_schedule" {
  description = "Cron schedule for backups (UTC). Default: daily 02:00."
  type        = string
  default     = "0 2 * * *"
}

variable "backup_retention" {
  description = "Number of backup files to retain on MinIO. Older ones are deleted by the upload step."
  type        = number
  default     = 30
}

variable "minio_endpoint" {
  description = "In-cluster S3 endpoint for the v2 MinIO. Pass via dependency block in the leaf."
  type        = string
}

variable "minio_access_key" {
  description = "MinIO access key (root_user). Sourced from SOPS via the leaf."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key (root_password). Sourced from SOPS via the leaf."
  type        = string
  sensitive   = true
}

variable "minio_bucket" {
  description = "MinIO bucket name for backups. Default 'backups' matches v1 convention + the bucket created by the minio module."
  type        = string
  default     = "backups"
}

# -----------------------------------------------------------------------------
# Tailscale TCP exposure (optional)
# -----------------------------------------------------------------------------
# Expose Postgres at <hostname>.<tailnet-domain>:5432 for direct psql access
# from your laptop or any tailnet device. Uses the Tailscale operator's
# LoadBalancer service pattern (spec.loadBalancerClass: tailscale) — the
# operator provisions a proxy pod that registers a TCP service on the
# tailnet. Note: Tailscale's network is already encrypted (WireGuard), so
# Postgres can run plaintext over the proxy — no need for Postgres TLS.
# -----------------------------------------------------------------------------

variable "enable_tailscale_exposure" {
  description = "Expose Postgres on the tailnet via the Tailscale operator's LoadBalancer pattern. Off by default (Postgres is internal); turn on for direct psql access from your laptop."
  type        = bool
  default     = false
}

variable "tailscale_hostname" {
  description = "Short tailnet hostname for direct psql access (e.g. 'postgresql' → postgresql.<domain>.ts.net:5432). Only used when enable_tailscale_exposure=true."
  type        = string
  default     = "postgresql"
}

variable "tailscale_domain" {
  description = "Tailnet domain (e.g. 'YOUR_TAILNET_DOMAIN.ts.net'). Comes from env.hcl. Required when enable_tailscale_exposure=true."
  type        = string
  default     = ""
}
