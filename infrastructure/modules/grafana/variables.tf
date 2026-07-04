# =============================================================================
# grafana module — inputs
# =============================================================================
# Chart: grafana-community/grafana
#   v12.3.0 (chart) → appVersion 13.0.1 (Grafana release).
#   Repo: https://grafana-community.github.io/helm-charts
#
# IMPORTANT: the historical `grafana/grafana` chart was DEPRECATED on
# 2026-01-30 and migrated to `grafana-community/grafana`. This module
# uses the new community-maintained chart.
# =============================================================================

# -----------------------------------------------------------------------------
# Chart + namespace
# -----------------------------------------------------------------------------

variable "chart_version" {
  description = "grafana-community/grafana Helm chart version. Latest: 12.3.0 (appVersion 13.0.1)."
  type        = string
  default     = "12.3.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '12.3.0' (no 'v' prefix)."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Grafana."
  type        = string
  default     = "grafana"
}

variable "release_name" {
  description = "Helm release name. Used as Service name (external Ingress backend)."
  type        = string
  default     = "grafana"
}

variable "admin_user" {
  description = "Grafana admin username. Defaults to 'admin'."
  type        = string
  default     = "admin"
}

variable "admin_password" {
  description = "Optional deterministic Grafana admin password. When null/empty, the module generates one."
  type        = string
  sensitive   = true
  default     = null
}

variable "grafana_cli_image" {
  description = "Image used by the admin-password sync Job. Keep it aligned with the chart appVersion unless you intentionally decouple it."
  type        = string
  default     = "grafana/grafana:13.0.1"
}

variable "root_url" {
  description = "Optional full browser URL for Grafana. Set this for localhost port-forward deployments; otherwise the module derives the external URL."
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Network exposure (external)
# -----------------------------------------------------------------------------

variable "tailscale_hostname" {
  description = "Short external hostname (e.g. 'grafana' → grafana.<domain>.example.com)."
  type        = string
  default     = "grafana"
}

variable "tailscale_domain" {
  description = "External domain (e.g. 'YOUR_EXTERNAL_DOMAIN.example.com'). Comes from env.hcl. Still referenced by the grafana_root_url/grafana_domain fallback in main.tf even though the Ingress resource was removed — that fallback only fires when root_url isn't explicitly set."
  type        = string
}

# -----------------------------------------------------------------------------
# Resource sizing — Grafana (Go HTTP server + sqlite-style cache layer)
# -----------------------------------------------------------------------------
# Real measurements (single-user homelab + a couple of dashboards):
#   ~10m CPU idle, ~150Mi RAM idle, ~300Mi peak when rendering panels.
# Below leaves comfortable headroom while remaining tight enough to run
# alongside Mimir/Loki/Tempo on the same host.
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request per pod."
  type        = string
  default     = "100m"
}

variable "memory_request" {
  description = "Memory request per pod."
  type        = string
  default     = "256Mi"
}

variable "memory_limit" {
  description = "Memory limit per pod."
  type        = string
  default     = "512Mi"
}

variable "replicas" {
  description = "Pod count. Single-replica is fine for homelab — Postgres backend would allow HA but no RUM use case here."
  type        = number
  default     = 1
}

# -----------------------------------------------------------------------------
# v2 baseline DB (per memory: feedback_default_to_v2_baseline_dbs)
# -----------------------------------------------------------------------------
# Postgres replaces the chart's default sqlite-on-PVC. A bootstrap Job creates
# the grafana DB + role on first apply (idempotent — re-runs are no-ops).
# -----------------------------------------------------------------------------

variable "postgres_admin_user" {
  description = "PostgreSQL admin user for the bootstrap Job. Default 'postgres'."
  type        = string
  default     = "postgres"
}

variable "postgres_admin_password" {
  description = "PostgreSQL admin password (sourced from postgresql module via dependency block)."
  type        = string
  sensitive   = true
}

variable "postgres_host" {
  description = "PostgreSQL host (in-cluster DNS)."
  type        = string
  default     = "postgresql.postgresql.svc.cluster.local"
}

variable "postgres_port" {
  description = "PostgreSQL port."
  type        = number
  default     = 5432
}

variable "grafana_db_name" {
  description = "Database name created by the bootstrap Job."
  type        = string
  default     = "grafana"
}

variable "grafana_db_user" {
  description = "Role created by the bootstrap Job. Owns the grafana database."
  type        = string
  default     = "grafana"
}

# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------
# DISABLED in v2 — all stateful data lives in Postgres. The chart's persistence
# would only hold plugins + the BLEVE search index, both regeneratable on pod
# restart. Matches v1 setting and keeps PVC count down (per playbook).
# -----------------------------------------------------------------------------

variable "persistence_enabled" {
  description = "Enable PVC for plugins + BLEVE search index. Default false — DB is external, plugins re-install at boot, BLEVE rebuilds. Flip to true if you install heavy plugins and want to skip re-install latency."
  type        = bool
  default     = false
}

variable "storage_size" {
  description = "PVC size when persistence_enabled=true. 2Gi covers a few plugins + BLEVE."
  type        = string
  default     = "2Gi"
}

variable "storage_class" {
  description = "StorageClass for the PVC. k3d default is 'local-path'."
  type        = string
  default     = "local-path"
}

# -----------------------------------------------------------------------------
# Sidecar (auto-discovery of datasources + dashboards from labeled ConfigMaps)
# -----------------------------------------------------------------------------
# When Mimir/Loki/Tempo are deployed, each module ships a ConfigMap labeled
# `grafana_datasource: "1"` and the Grafana sidecar imports it on the fly.
# Same pattern for dashboards (`grafana_dashboard: "1"`).
# -----------------------------------------------------------------------------

variable "sidecar_search_namespace" {
  description = "Namespace scope for sidecar ConfigMap discovery. 'ALL' = cluster-wide (standard for LGTM). Restrict if you need narrower RBAC."
  type        = string
  default     = "ALL"
}

# -----------------------------------------------------------------------------
# ServiceMonitor (optional — Alloy/Mimir scrapes Grafana's own metrics)
# -----------------------------------------------------------------------------
# Enabled by default so when Alloy comes online it auto-discovers Grafana's
# /metrics endpoint via the Prometheus Operator CRD. Costs nothing if no
# scraper is running yet (CR sits idle).
# -----------------------------------------------------------------------------

variable "service_monitor_enabled" {
  description = "Create a ServiceMonitor CR for Prometheus-Operator-style scraping (Alloy + Mimir use this)."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Dashboard provisioning
# -----------------------------------------------------------------------------

variable "provision_dashboards" {
  description = "If true, download dashboards listed in dashboards/dashboard-ids.json from grafana.com on every apply, rewrite datasource UIDs, and create labeled ConfigMaps for the Grafana sidecar to import. Set false to skip the data.http fetches (useful for offline / air-gapped applies)."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Opt-in NodePort Service for localhost access via k3d's loadbalancer port
# mapping. Leave `enable_local_expose` unset (default false) on any
# environment where external Ingress already provides access (e.g. COELHO
# Cloud) — the module below is never instantiated in that case. See
# infrastructure/modules/k3d_expose/.
#
# NOTE: Grafana already has a working localhost path via
# scripts/standalone-port-forward.sh (23005->80). This NodePort is a second,
# independent mechanism to REACH the UI.

variable "enable_local_expose" {
  description = "Create a NodePort Service for localhost access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_node_port" {
  description = "NodePort for local Grafana access (target port 3000, the chart's named 'grafana' container port). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}
