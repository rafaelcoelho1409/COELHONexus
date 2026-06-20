# =============================================================================
# mimir module — inputs
# =============================================================================
# Chart: grafana/mimir-distributed
#   v6.0.6 (chart) → appVersion 3.0.4 (Mimir release).
#   Repo: https://grafana.github.io/helm-charts
#
# Mode: distributed-with-replicas=1 (each component a single Pod). The chart
# is named "mimir-distributed" but at replicas=1 it behaves like a homelab
# monolith spread across pods (lower per-pod RAM than `target: all`, easier
# to scale individual components later if needed).
# =============================================================================

variable "chart_version" {
  description = "grafana/mimir-distributed Helm chart version. Latest: 6.0.6 (appVersion 3.0.4)."
  type        = string
  default     = "6.0.6"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '6.0.6'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Mimir."
  type        = string
  default     = "mimir"
}

variable "release_name" {
  description = "Helm release name. Used as prefix for component Service names (e.g. <release>-gateway, <release>-distributor)."
  type        = string
  default     = "mimir"
}

# -----------------------------------------------------------------------------
# Note: no Tailscale Ingress vars. Mimir has no UI; queries go through
# Grafana via the in-cluster datasource. Same pattern for Loki/Tempo/Alloy.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# v2 baseline DBs — MinIO for object storage (per memory feedback_default_to_v2_baseline_dbs)
# -----------------------------------------------------------------------------
# Mimir uses S3 for blocks (long-term), ruler rules, and alertmanager state.
# A bootstrap Job creates the three buckets on first apply (idempotent).
# -----------------------------------------------------------------------------

variable "minio_endpoint" {
  description = "S3 endpoint for MinIO (in-cluster, includes scheme)."
  type        = string
  default     = "http://minio.minio.svc.cluster.local:9000"
}

variable "minio_access_key" {
  description = "MinIO access key. Reused from v1 — see memory feedback_secrets_reuse."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key."
  type        = string
  sensitive   = true
}

variable "blocks_bucket" {
  description = "MinIO bucket for TSDB blocks (the bulk of long-term metrics)."
  type        = string
  default     = "mimir-blocks"
}

variable "ruler_bucket" {
  description = "MinIO bucket for ruler recording/alerting rules."
  type        = string
  default     = "mimir-ruler"
}

variable "alertmanager_bucket" {
  description = "MinIO bucket for alertmanager state. Created even though alertmanager is disabled — Mimir validates the config block at startup."
  type        = string
  default     = "mimir-alertmanager"
}

# -----------------------------------------------------------------------------
# Retention
# -----------------------------------------------------------------------------

variable "retention_period" {
  description = "Compactor block retention period. v1 default 30d (homelab). Bump to 90d/180d if you want longer history (more MinIO disk)."
  type        = string
  default     = "30d"
}

# -----------------------------------------------------------------------------
# Resource sizing — homelab-trimmed (~50% of v1's pre-Alloy estimate)
# -----------------------------------------------------------------------------
# v1 was sized for 244K active series. v2 starts with zero series until Alloy
# comes online; trimming to ~half. Grafana's capacity planning formulas:
#   ingester: 2.5 GB per 300K series
#   distributor: 1 GB per 25K samples/sec
#   querier/store-gateway: 1 GB per 10 queries/sec
# -----------------------------------------------------------------------------

variable "ingester_cpu_request" {
  description = "Ingester CPU request."
  type        = string
  default     = "100m"
}

variable "ingester_memory_request" {
  description = "Ingester memory request."
  type        = string
  default     = "512Mi"
}

variable "ingester_memory_limit" {
  description = "Ingester memory limit. Bump to 1Gi+ once active series climb past ~50K."
  type        = string
  default     = "1Gi"
}

variable "ingester_pvc_size" {
  description = "Ingester PVC size. Holds in-memory active series flushed to local disk before shipping to S3."
  type        = string
  default     = "5Gi"
}

variable "distributor_memory_request" {
  description = "Distributor memory request."
  type        = string
  default     = "256Mi"
}

variable "distributor_memory_limit" {
  description = "Distributor memory limit. Bumped 256→1024Mi after observing OOMKills during post-restart metric storms — every recovering pod sends backlogged metrics simultaneously, and rejected-request buffers grow fast under rate-limit rejection."
  type        = string
  default     = "1Gi"
}

variable "querier_memory_request" {
  description = "Querier memory request."
  type        = string
  default     = "256Mi"
}

variable "querier_memory_limit" {
  description = "Querier memory limit."
  type        = string
  default     = "512Mi"
}

variable "query_frontend_memory_request" {
  description = "Query frontend memory request."
  type        = string
  default     = "128Mi"
}

variable "query_frontend_memory_limit" {
  description = "Query frontend memory limit."
  type        = string
  default     = "256Mi"
}

variable "store_gateway_memory_request" {
  description = "Store-gateway memory request."
  type        = string
  default     = "256Mi"
}

variable "store_gateway_memory_limit" {
  description = "Store-gateway memory limit."
  type        = string
  default     = "512Mi"
}

variable "store_gateway_pvc_size" {
  description = "Store-gateway PVC. Sync dir for blocks fetched from S3 — small because blocks are paged in on demand."
  type        = string
  default     = "2Gi"
}

variable "compactor_memory_request" {
  description = "Compactor memory request."
  type        = string
  default     = "256Mi"
}

variable "compactor_memory_limit" {
  description = "Compactor memory limit. Bump to 1Gi+ if compaction cycles OOM (visible in metaMonitoring)."
  type        = string
  default     = "512Mi"
}

variable "compactor_pvc_size" {
  description = "Compactor PVC. Temp space for downloading/recompacting blocks."
  type        = string
  default     = "5Gi"
}

variable "ruler_memory_request" {
  description = "Ruler memory request."
  type        = string
  default     = "64Mi"
}

variable "ruler_memory_limit" {
  description = "Ruler memory limit."
  type        = string
  default     = "128Mi"
}

variable "query_scheduler_memory_request" {
  description = "Query scheduler memory request."
  type        = string
  default     = "64Mi"
}

variable "query_scheduler_memory_limit" {
  description = "Query scheduler memory limit."
  type        = string
  default     = "128Mi"
}

variable "gateway_memory_request" {
  description = "Gateway memory request. Single ingress proxy in front of distributor (writes) + query-frontend (reads)."
  type        = string
  default     = "64Mi"
}

variable "gateway_memory_limit" {
  description = "Gateway memory limit."
  type        = string
  default     = "128Mi"
}

# -----------------------------------------------------------------------------
# Storage class
# -----------------------------------------------------------------------------

variable "storage_class" {
  description = "StorageClass for component PVCs. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}
