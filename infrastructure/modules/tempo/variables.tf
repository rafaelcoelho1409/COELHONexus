# =============================================================================
# tempo module — inputs
# =============================================================================
# Chart: grafana-community/tempo (Single Binary)
#   v2.1.0 (chart) → appVersion 2.10.1 (Tempo release).
#   Repo: https://grafana-community.github.io/helm-charts
#
# IMPORTANT: the original `grafana/tempo` chart was deprecated and migrated
# to `grafana-community/tempo` (same forking event as Grafana and Loki, ~Q1
# 2026). This module uses the new community-maintained chart.
#
# Mode: monolithic single-binary StatefulSet. For homelab this is right-
# sized; the distributed `tempo-distributed` chart targets large-scale
# deployments (many ingesters + queriers + compactors).
# =============================================================================

variable "chart_version" {
  description = "grafana-community/tempo Helm chart version. Latest: 2.1.0 (appVersion 2.10.1)."
  type        = string
  default     = "2.1.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '2.1.0'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Tempo."
  type        = string
  default     = "tempo"
}

variable "release_name" {
  description = "Helm release name. Used as Service name (in-cluster ingest + query target)."
  type        = string
  default     = "tempo"
}

# -----------------------------------------------------------------------------
# Note: no Tailscale Ingress vars. Tempo has no UI; all viewing happens via
# Grafana → Explore (Tempo). Per memory feedback_no_external_ingress_for_uiless_backends.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# v2 baseline DBs — MinIO for trace storage
# -----------------------------------------------------------------------------

variable "minio_endpoint" {
  description = "S3 endpoint for MinIO (in-cluster, includes scheme). Module strips the scheme for Tempo's S3 client."
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

variable "traces_bucket" {
  description = "MinIO bucket for trace blocks."
  type        = string
  default     = "tempo-traces"
}

# -----------------------------------------------------------------------------
# Retention
# -----------------------------------------------------------------------------

variable "retention_period" {
  description = "Compaction block retention. v1 had 720h (30d). Drop to 168h (7d) if tracing volume balloons."
  type        = string
  default     = "720h"
}

# -----------------------------------------------------------------------------
# Resource sizing — single binary, homelab
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request for the Tempo StatefulSet."
  type        = string
  default     = "100m"
}

variable "memory_request" {
  description = "Memory request for the Tempo StatefulSet."
  type        = string
  default     = "256Mi"
}

variable "memory_limit" {
  description = "Memory limit. Tempo's mem-ballast is 1Gi by default (see helm/values.yaml.tpl) — keep limit ≥ ballast + working set."
  type        = string
  default     = "1Gi"
}

variable "storage_size" {
  description = "PVC for the WAL + recent blocks before they ship to S3. Trimmed from v1's 10Gi — ingester flushes regularly."
  type        = string
  default     = "5Gi"
}

variable "storage_class" {
  description = "StorageClass for the PVC. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Receivers — keep OTLP only by default (modern standard, what Alloy speaks).
# Set the enable_legacy_receivers flag to also expose Jaeger + Zipkin if you
# ship apps using those SDKs (legacy auto-instrumentation).
# -----------------------------------------------------------------------------

variable "enable_legacy_receivers" {
  description = "If true, also enable Jaeger (gRPC + thrift_http + thrift_compact) and Zipkin receivers. Default false — modern OpenTelemetry SDKs use OTLP. Costs zero RAM if no traffic flows."
  type        = bool
  default     = false
}
