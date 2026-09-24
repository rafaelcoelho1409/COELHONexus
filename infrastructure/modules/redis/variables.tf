# =============================================================================
# redis module — inputs
# =============================================================================
# Chart: bitnami/redis v25.4.1 (latest as of 2026-05).
# Image override: redis/redis-stack-server (7.4.0-v8) — adds RediSearch,
#   RedisJSON, RedisTimeSeries, RedisBloom modules. Same flavor as v1.
# =============================================================================

# -----------------------------------------------------------------------------
# Chart + namespace
# -----------------------------------------------------------------------------

variable "chart_version" {
  description = "bitnami/redis chart version. Latest stable: 25.4.1."
  type        = string
  default     = "25.4.1"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '25.4.1' (no 'v' prefix)."
  }
}

variable "redis_stack_version" {
  description = "redis-stack-server image tag. Includes RediSearch/RedisJSON/RedisTimeSeries/RedisBloom modules."
  type        = string
  default     = "7.4.0-v8"
}

variable "namespace" {
  description = "Kubernetes namespace for Redis."
  type        = string
  default     = "redis"
}

variable "release_name" {
  description = "Helm release name. Service name will be `<release>-master` (Bitnami chart convention)."
  type        = string
  default     = "redis"
}

# -----------------------------------------------------------------------------
# Auth (from SOPS)
# -----------------------------------------------------------------------------

variable "redis_password" {
  description = "Redis password. Sourced from SOPS-encrypted secrets (redis.password)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.redis_password) >= 8
    error_message = "redis_password must be ≥8 chars."
  }
}

# -----------------------------------------------------------------------------
# Memory + storage
# -----------------------------------------------------------------------------
# v1 ran 256mb maxmemory + 5Gi PVC successfully. Keeping the same defaults —
# Redis Stack modules add ~150Mi overhead so memory_limit needs ~448Mi
# headroom over maxmemory.
# -----------------------------------------------------------------------------

variable "maxmemory" {
  description = "Redis maxmemory limit (e.g., '256mb', '1gb'). Keep modest for homelab."
  type        = string
  default     = "256mb"
}

variable "storage_size" {
  description = "Redis PVC size for AOF/RDB persistence. Recommended: 2× maxmemory."
  type        = string
  default     = "5Gi"
}

variable "storage_class" {
  description = "StorageClass for the PVC. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Resources
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request for the Redis container."
  type        = string
  default     = "25m"
}

variable "memory_request" {
  description = "Memory request for the Redis container."
  type        = string
  default     = "96Mi"
}

variable "memory_limit" {
  description = "Memory limit. Must accommodate maxmemory (256mb) + Stack module overhead (~192mb)."
  type        = string
  default     = "448Mi"
}

# -----------------------------------------------------------------------------
# Metrics + ServiceMonitor
# -----------------------------------------------------------------------------

variable "enable_servicemonitor" {
  description = "Create a ServiceMonitor for Mimir/Alloy scraping. Bitnami chart ships native redis_exporter + ServiceMonitor."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Backup CronJob — RDB snapshot → MinIO
# -----------------------------------------------------------------------------

variable "enable_backup_cronjob" {
  description = "Enable scheduled BGSAVE + RDB upload to MinIO. Disable for dev or if MinIO isn't ready."
  type        = bool
  default     = true
}

variable "backup_schedule" {
  description = "Cron schedule for backups (UTC). Default: daily 02:15 (offset from Postgres at 02:00)."
  type        = string
  default     = "15 2 * * *"
}

variable "backup_retention" {
  description = "Number of RDB files to retain on MinIO."
  type        = number
  default     = 30
}

variable "minio_endpoint" {
  description = "In-cluster S3 endpoint for the v2 MinIO. Pass via dependency block in the leaf."
  type        = string
}

variable "minio_access_key" {
  description = "MinIO access key. From SOPS via the leaf."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key. From SOPS via the leaf."
  type        = string
  sensitive   = true
}

variable "minio_bucket" {
  description = "MinIO bucket name for backups. Default 'backups' matches the bucket created by the minio module."
  type        = string
  default     = "backups"
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Redis is in-cluster only by default. Apps connect via the ClusterIP service.
# Opt-in NodePort Service so a human on their own laptop can run redis-cli
# against localhost:<port> during development. Leave `enable_local_expose`
# unset (default false) on any environment where external Ingress already
# provides access — the module below is never even instantiated in that
# case. See infrastructure/modules/k3d_expose/.
# -----------------------------------------------------------------------------

variable "enable_local_expose" {
  description = "Create a NodePort Service for localhost redis-cli access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_redis_node_port" {
  description = "NodePort for local redis-cli access (target port 6379). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}
