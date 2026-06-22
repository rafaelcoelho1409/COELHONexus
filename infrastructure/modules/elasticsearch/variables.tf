# =============================================================================
# elasticsearch module — inputs
# =============================================================================
# THIRD ATTEMPT, FINAL APPROACH: ECK Operator (Elastic Cloud on Kubernetes).
#
# Why we landed here:
#   1. elastic/elasticsearch chart 8.5.1 → archived 2023, broken with newer ES
#      images (keystore format mismatch + Kibana node binary path), failed
#      cleanup of orphaned Helm hook ServiceAccounts blocked re-installs.
#   2. bitnami/elasticsearch → broken since 2025 because Bitnami pulled their
#      free Docker images from Docker Hub (commercial transition); chart's
#      pinned tags like `bitnami/os-shell:12-debian-12-r43` return 404.
#   3. ECK Operator → official Elastic, actively maintained, operator pattern
#      with CRDs. Two helm releases: eck-operator (controller + CRDs) +
#      eck-stack (Elasticsearch + Kibana CRs).
#
# Charts:
#   - elastic/eck-operator v3.3.2 (CRDs + controller)
#   - elastic/eck-stack v0.18.2 (wraps eck-elasticsearch + eck-kibana sub-charts)
#
# Why ES 8.x not 9.x: Nexus's pyproject.toml pins
# `elasticsearch[async]>=8.0.0,<9.0.0` — Python client constrained to 8.x.
# We pin ES version to 8.18.0 in the eck-stack values.
# =============================================================================

variable "operator_chart_version" {
  description = "elastic/eck-operator chart version. 3.3.2 is the latest."
  type        = string
  default     = "3.3.2"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.operator_chart_version))
    error_message = "operator_chart_version must be SemVer."
  }
}

variable "stack_chart_version" {
  description = "elastic/eck-stack chart version. 0.18.2 wraps eck-elasticsearch + eck-kibana."
  type        = string
  default     = "0.18.2"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.stack_chart_version))
    error_message = "stack_chart_version must be SemVer."
  }
}

variable "es_version" {
  description = "Elasticsearch version. 8.18.8 — latest 8.18 patch (2026); ships ES|QL STATS memory fix + Transform health + Entitlements patches. Still <9.0.0 → Nexus's elasticsearch[async] client constraint satisfied."
  type        = string
  default     = "8.18.8"
}

variable "kibana_version" {
  description = "Kibana version. Should match es_version."
  type        = string
  default     = "8.18.8"
}

variable "namespace" {
  description = "Kubernetes namespace for ES + Kibana. The operator runs in elastic-system; this namespace is for the CRs only."
  type        = string
  default     = "elasticsearch"
}

variable "operator_namespace" {
  description = "Namespace where the ECK operator pod lives (cluster-wide install)."
  type        = string
  default     = "elastic-system"
}

# -----------------------------------------------------------------------------
# Network exposure (Tailscale) — Kibana ONLY
# -----------------------------------------------------------------------------

variable "tailscale_hostname_kibana" {
  description = "Short tailnet hostname for Kibana UI."
  type        = string
  default     = "kibana"
}

variable "tailscale_hostname_es" {
  description = "Short tailnet hostname for the Elasticsearch HTTPS REST API. Lets laptop scripts (during Nexus development) query ES without kubectl port-forward. Data-plane endpoint — no Homepage tile."
  type        = string
  default     = "elasticsearch"
}

variable "tailscale_domain" {
  description = "Tailnet domain. Comes from env.hcl."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "IngressClass name from the tailscale-operator unit."
  type        = string
  default     = "tailscale"
}

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------

variable "storage_size" {
  description = "ES PVC size for Nexus's transcript volume."
  type        = string
  default     = "10Gi"
}

variable "storage_class" {
  description = "StorageClass for the ES PVC."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Resource sizing
# -----------------------------------------------------------------------------

variable "es_memory_request" {
  description = "ES pod memory REQUEST. Burstable QoS (request < limit) gives JVM headroom for off-heap allocations (Lucene FS cache, ML controller, native code) without inflating steady-state. Was 2Gi=2Gi (Guaranteed) → kubelet evicted pod when off-heap pushed cgroup ceiling."
  type        = string
  default     = "1Gi"
}

variable "es_memory_limit" {
  description = "ES pod memory LIMIT. 1.5Gi limit + 640m heap (es_java_heap) leaves ~900 MiB for Lucene FS cache + JVM overhead + off-heap allocations."
  type        = string
  default     = "1.5Gi"
}

variable "es_java_heap" {
  description = "Explicit JVM -Xms/-Xmx (passed via ES_JAVA_OPTS). Overrides ECK's auto-derived 50%-of-limit default. 640m on 1.5Gi pod ≈ 42% — leaves more for FS cache, which accelerates search far more than heap at small-index scale."
  type        = string
  default     = "640m"
}

variable "es_cpu_request" {
  type    = string
  default = "200m"
}

variable "kibana_memory_request" {
  type    = string
  default = "400Mi"
}

variable "kibana_memory_limit" {
  description = "Kibana pod memory limit. 600Mi accommodates Node.js heap (400m) + ~200 MiB overhead. Was 1Gi=1Gi."
  type        = string
  default     = "600Mi"
}

variable "kibana_node_max_old_space_mb" {
  description = "Kibana Node.js heap cap (NODE_OPTIONS --max-old-space-size). Pod limit must be > this + ~150 MiB Node overhead."
  type        = number
  default     = 400
}

variable "kibana_cpu_request" {
  type    = string
  default = "100m"
}

# -----------------------------------------------------------------------------
# v2 baseline MinIO — for snapshot backups
# -----------------------------------------------------------------------------

variable "minio_endpoint" {
  description = "MinIO S3 endpoint (in-cluster, with scheme)."
  type        = string
  default     = "http://minio.minio.svc.cluster.local:9000"
}

variable "minio_access_key" {
  description = "MinIO access key."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key."
  type        = string
  sensitive   = true
}

variable "backup_bucket" {
  description = "MinIO bucket for snapshots. Uses shared `backups` bucket; ES uses `elasticsearch/` prefix."
  type        = string
  default     = "backups"
}

variable "snapshot_repo_name" {
  description = "ES snapshot repository name."
  type        = string
  default     = "minio-backup"
}

variable "backup_schedule" {
  description = "Cron for the snapshot CronJob."
  type        = string
  default     = "30 */6 * * *"
}

variable "backup_retention" {
  description = "Number of snapshots to keep."
  type        = number
  default     = 20
}

# -----------------------------------------------------------------------------
# Optional application user
# -----------------------------------------------------------------------------

variable "app_user_enabled" {
  description = "Create a deterministic, least-privilege Elasticsearch app user via ECK file realm. Intended for public demo/local stacks; private stacks can keep their own secret workflow."
  type        = bool
  default     = false
}

variable "app_username" {
  description = "Elasticsearch app username created when app_user_enabled=true."
  type        = string
  default     = "coelhonexus"
}

variable "app_password" {
  description = "Elasticsearch app password created when app_user_enabled=true. Demo-only if committed in env.hcl."
  type        = string
  default     = ""
  sensitive   = true
}

variable "app_role_name" {
  description = "Elasticsearch role name assigned to the app user."
  type        = string
  default     = "coelhonexus_ycs"
}

# -----------------------------------------------------------------------------
# Optional deterministic built-in `elastic` password (local/demo only)
# -----------------------------------------------------------------------------

variable "elastic_password_override" {
  description = "Optional deterministic password for the built-in `elastic` user. Intended for local/demo stacks that must keep a shared app/chart contract expecting username `elastic` without editing app or Helm code."
  type        = string
  default     = ""
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Note: by default ECK auto-generates the built-in `elastic` superuser password
# and stores it in Secret `elasticsearch-es-elastic-user`. The optional
# `elastic_password_override` above is intended only for local/demo stacks that
# must preserve a shared app/chart contract outside this module.
# -----------------------------------------------------------------------------
