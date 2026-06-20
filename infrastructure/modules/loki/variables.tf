# =============================================================================
# loki module — inputs
# =============================================================================
# Chart: grafana-community/loki
#   v13.5.0 (chart) → appVersion 3.7.1 (Loki release).
#   Repo: https://grafana-community.github.io/helm-charts
#
# IMPORTANT: the original `grafana/loki` chart was deprecated 2026-03-16 and
# migrated to `grafana-community/loki`. Chart 6.55.0 was the last release in
# the old repo; the new repo restarts numbering at 13.x. This module uses
# the new community-maintained chart.
#
# Mode: Monolithic / SingleBinary (the chart's default — one Pod runs all
# components). For homelab this is right-sized; SimpleScalable is being
# deprecated in Loki 4.0 anyway.
# =============================================================================

variable "chart_version" {
  description = "grafana-community/loki Helm chart version. Latest: 13.5.0 (appVersion 3.7.1)."
  type        = string
  default     = "13.5.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '13.5.0'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Loki."
  type        = string
  default     = "loki"
}

variable "release_name" {
  description = "Helm release name. Used as prefix for component Service names."
  type        = string
  default     = "loki"
}

# -----------------------------------------------------------------------------
# Note: no Tailscale Ingress vars. Loki has no UI; queries go through
# Grafana via the in-cluster datasource (per memory feedback_no_external_ingress_for_uiless_backends).
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# v2 baseline DBs — MinIO for object storage
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

variable "chunks_bucket" {
  description = "MinIO bucket for log chunks (the bulk of long-term storage)."
  type        = string
  default     = "loki-chunks"
}

variable "ruler_bucket" {
  description = "MinIO bucket for ruler recording/alerting rules. Created even though ruler API is on but unused — Loki validates the storage block at startup."
  type        = string
  default     = "loki-ruler"
}

# -----------------------------------------------------------------------------
# Retention
# -----------------------------------------------------------------------------

variable "retention_period" {
  description = "Compactor retention. v1 had 30d (720h). Bump for longer history (more MinIO disk)."
  type        = string
  default     = "720h" # 30 days
}

# -----------------------------------------------------------------------------
# Resource sizing — Monolithic SingleBinary, homelab
# -----------------------------------------------------------------------------
# v1 measured ~256Mi RAM at idle for SingleBinary; bumps to ~400Mi under
# moderate ingestion. Below leaves room for Alloy's eventual log stream.
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request for the SingleBinary StatefulSet."
  type        = string
  default     = "100m"
}

variable "memory_request" {
  description = "Memory request for the SingleBinary StatefulSet."
  type        = string
  default     = "256Mi"
}

variable "memory_limit" {
  description = "Memory limit. Bump to 1Gi+ if Loki OOMs under Alloy load."
  type        = string
  default     = "512Mi"
}

variable "storage_size" {
  description = "PVC for SingleBinary. Holds WAL + index cache + recent chunks before they ship to S3. Trimmed from v1's 10Gi — chunks ship within minutes."
  type        = string
  default     = "5Gi"
}

variable "storage_class" {
  description = "StorageClass for the PVC. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Limits — log ingestion rate caps to protect the cluster from runaway logging
# -----------------------------------------------------------------------------

variable "ingestion_rate_mb" {
  description = "Per-tenant ingestion rate in MB/s. 10MB/s is generous for a homelab."
  type        = number
  default     = 10
}

variable "ingestion_burst_size_mb" {
  description = "Burst allowance on top of ingestion_rate."
  type        = number
  default     = 20
}
