# =============================================================================
# qdrant module — inputs
# =============================================================================
# Chart: qdrant/qdrant
#   v1.17.1 (chart) → appVersion v1.17.1.
#   Repo: https://qdrant.github.io/qdrant-helm
#
# Qdrant is self-contained — no Postgres/Redis/MinIO at runtime. MinIO is
# only used by the snapshot backup CronJob (every 6h, 20-snapshot retention).
# =============================================================================

variable "chart_version" {
  description = "qdrant/qdrant Helm chart version. Latest: 1.17.1 (appVersion v1.17.1)."
  type        = string
  default     = "1.17.1"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '1.17.1'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Qdrant."
  type        = string
  default     = "qdrant"
}

variable "release_name" {
  description = "Helm release name. Used as Service name."
  type        = string
  default     = "qdrant"
}

# -----------------------------------------------------------------------------
# Network exposure (Tailscale)
# -----------------------------------------------------------------------------
# Qdrant has a built-in Dashboard at /dashboard on port 6333. External
# Ingress applies (per memory feedback_no_external_ingress_for_uiless_backends:
# UI exists → Ingress + Homepage tile).
# -----------------------------------------------------------------------------

variable "tailscale_hostname" {
  description = "Short tailnet hostname (e.g. 'qdrant' → qdrant.<domain>.ts.net)."
  type        = string
  default     = "qdrant"
}

variable "tailscale_domain" {
  description = "Tailnet domain (e.g. 'YOUR_TAILNET_DOMAIN.ts.net'). Comes from env.hcl."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "IngressClass name from the tailscale-operator unit."
  type        = string
  default     = "tailscale"
}

# -----------------------------------------------------------------------------
# Auth — API key reused from v1 SOPS (per memory feedback_secrets_reuse)
# -----------------------------------------------------------------------------

variable "api_key" {
  description = "Qdrant API key. Reused from v1 — drop into SOPS as `qdrant.api_key` before applying. Empty string disables auth (NOT recommended even for homelab — Qdrant defaults to no auth and any tailnet member could query)."
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------

variable "storage_size" {
  description = "Qdrant data PVC. v1 had 10Gi; trimmed to 5Gi for homelab — Nexus's vector workload starts small. Bump if collections grow past ~1M vectors."
  type        = string
  default     = "5Gi"
}

variable "snapshot_storage_size" {
  description = "Snapshot PVC. The chart writes snapshots here BEFORE the backup CronJob ships them to MinIO. 5Gi is enough for a few rolling snapshots."
  type        = string
  default     = "5Gi"
}

variable "storage_class" {
  description = "StorageClass for both PVCs."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Resource sizing — based on v1's measured idle (1m CPU / 166Mi RAM)
# -----------------------------------------------------------------------------

variable "cpu_request" {
  type    = string
  default = "10m"
}

variable "memory_request" {
  type    = string
  default = "200Mi"
}

variable "memory_limit" {
  description = "Memory limit. Qdrant can spike during indexing — 512Mi is the v1 measured ceiling."
  type        = string
  default     = "512Mi"
}

variable "replicas" {
  description = "Pod count. Single replica for homelab — clustering needs RWX storage class which local-path doesn't offer."
  type        = number
  default     = 1
}

# -----------------------------------------------------------------------------
# v2 baseline MinIO — used ONLY by the backup CronJob
# -----------------------------------------------------------------------------

variable "minio_endpoint" {
  description = "MinIO S3 endpoint (in-cluster, with scheme)."
  type        = string
  default     = "http://minio.minio.svc.cluster.local:9000"
}

variable "minio_access_key" {
  description = "MinIO access key. Reused from v1 SOPS via the v2 MinIO module."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key."
  type        = string
  sensitive   = true
}

variable "backup_bucket" {
  description = "MinIO bucket for snapshot uploads. Uses the shared `backups` bucket with per-collection subpath (qdrant/<collection>/<timestamp>.snapshot)."
  type        = string
  default     = "backups"
}

variable "backup_schedule" {
  description = "Cron schedule for the snapshot backup. Default: every 6 hours (00:30, 06:30, 12:30, 18:30 UTC)."
  type        = string
  default     = "30 */6 * * *"
}

variable "backup_retention" {
  description = "Number of recent snapshots per collection to keep in MinIO. v1 had 20 (5 days at 6h interval)."
  type        = number
  default     = 20
}

# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------

variable "service_monitor_enabled" {
  description = "Create ServiceMonitor for Alloy/Mimir scraping."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Opt-in NodePort Service so a human on their own laptop can open Qdrant's
# built-in Dashboard (/dashboard, served on the same port as the REST API) at
# localhost:<port> during development. Leave `enable_local_expose` unset
# (default false) on any environment where Tailscale Ingress already provides
# access (e.g. COELHO Cloud) — the module below is never even instantiated in
# that case. See infrastructure/modules/k3d_expose/.
#
# Only the REST port (6333) is exposed — gRPC (6334) and the internal p2p/raft
# port (6335) aren't browser-relevant and single-replica homelab doesn't use
# clustering anyway.

variable "enable_local_expose" {
  description = "Create a NodePort Service for localhost access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_http_node_port" {
  description = "NodePort for local Qdrant Dashboard/REST access (target port 6333). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}
