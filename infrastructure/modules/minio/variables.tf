# =============================================================================
# minio module — inputs
# =============================================================================
# Chart: https://charts.min.io/ (community chart). MinIO archived this
# repository on 2026-04-25 but the chart still pulls and deploys cleanly —
# it just stopped receiving updates. We pin a specific version so the
# `latest` reference doesn't shift if MinIO ever restores the repo.
#
# Why this chart vs Bitnami 17.x:
#   - charts.min.io is what v1 ran successfully on this hardware
#   - Bitnami's MinIO chart had local deployment issues for the user
#   - Migration discipline: port what works, don't introduce new variables
#   - Pinned + archived = predictable, no surprise updates
# The MinIO BINARY (image tag) can still be bumped independently of chart.
# =============================================================================

# -----------------------------------------------------------------------------
# Chart + namespace
# -----------------------------------------------------------------------------

variable "chart_version" {
  description = "charts.min.io/minio Helm chart version. v1 used 5.4.0 successfully — same pin here for reproducibility."
  type        = string
  default     = "5.4.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '5.4.0' (no 'v' prefix)."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for MinIO."
  type        = string
  default     = "minio"
}

variable "release_name" {
  description = "Helm release name. Used as the API Service name; the chart auto-creates a `<release>-console` Service for port 9001."
  type        = string
  default     = "minio"
}


# -----------------------------------------------------------------------------
# Credentials (sensitive)
# -----------------------------------------------------------------------------
# Reused from v1 during migration so the same client configs (mc, S3 SDKs,
# backup CronJobs) work against v2 without re-keying. Sourced from SOPS.
# -----------------------------------------------------------------------------

variable "root_user" {
  description = "MinIO root user (S3 access key). v1 used 'admin'."
  type        = string
  default     = "admin"

  validation {
    condition     = length(var.root_user) >= 3
    error_message = "root_user must be ≥3 characters."
  }
}

variable "root_password" {
  description = "MinIO root password (S3 secret key). Sourced from SOPS-encrypted secrets. MinIO requires ≥8 chars."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.root_password) >= 8
    error_message = "root_password must be ≥8 chars (MinIO's own minimum)."
  }
}

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------
# v1 used 50Gi but never grew past ~5GB in practice. v2 starts at 15Gi —
# 3× current usage with headroom. local-path supports online resize: bump
# this value + restart the pod and the PV/PVC grow.
# -----------------------------------------------------------------------------

variable "storage_size" {
  description = "MinIO PVC size. v1 wasted disk at 50Gi; v2 starts smaller. Bump if you actually grow into it."
  type        = string
  default     = "15Gi"
}

variable "storage_class" {
  description = "StorageClass for the MinIO PVC. k3d default is 'local-path' (Rancher local-path-provisioner)."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Resource sizing — kept from v1's measured tuning
# -----------------------------------------------------------------------------
# v1 real usage: 2m CPU, 145Mi RAM. Requests/limits below leave generous
# headroom while keeping the scheduler honest.
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request per MinIO pod."
  type        = string
  default     = "10m"
}

variable "memory_request" {
  description = "Memory request per MinIO pod."
  type        = string
  default     = "200Mi"
}

variable "memory_limit" {
  description = "Memory limit per MinIO pod. Below 384Mi risks OOM under load (verified in v1)."
  type        = string
  default     = "384Mi"
}

variable "gomemlimit" {
  description = "GOMEMLIMIT env var for MinIO's Go GC. Set ~10% below memory_limit for efficient GC."
  type        = string
  default     = "350MiB"
}

# -----------------------------------------------------------------------------
# Default buckets — INLINED in values.yaml.tpl, not parameterized
# -----------------------------------------------------------------------------
# Earlier this was a `default_buckets` variable rendered via yamlencode() into
# the template. That broke Helm's value parsing (verified 2026-05-02), so the
# bucket list now lives as literal YAML inside helm/values.yaml.tpl.
# Modify there if you need to change them.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Replicas — standalone mode = 1
# -----------------------------------------------------------------------------

variable "replicas" {
  description = "Number of MinIO pods. Standalone mode = 1. Distributed mode needs ≥4 and is a different config path."
  type        = number
  default     = 1

  validation {
    condition     = var.replicas >= 1
    error_message = "replicas must be at least 1."
  }
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Opt-in NodePort Services for localhost access via k3d's loadbalancer port
# mapping. Leave `enable_local_expose` unset (default false) on any
# environment where external Ingress already provides access (e.g. COELHO
# Cloud) — neither module below is instantiated in that case. See
# infrastructure/modules/k3d_expose/.
#
# This NodePort is the sole localhost access mechanism for MinIO (23016->9001
# console, 23015->9000 API) — the old port-forward-script path was deleted
# 2026-07-04; see docs/APP-LAYER-NODEPORT-MIGRATION-2026-07-03.md.

variable "enable_local_expose" {
  description = "Create NodePort Services for localhost access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_api_node_port" {
  description = "NodePort for local S3 API access (target port 9000). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}

variable "k3d_console_node_port" {
  description = "NodePort for local Console UI access (target port 9001). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}
