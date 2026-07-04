# =============================================================================
# alloy module — inputs
# =============================================================================
# Chart: grafana/alloy
#   v1.8.0 (chart) → appVersion v1.16.0 (Alloy release).
#   Repo: https://grafana.github.io/helm-charts
#
# Note: Alloy has NOT been migrated to grafana-community yet (unlike Grafana,
# Loki, Tempo). Stay on the original `grafana/alloy` chart. If/when the
# community fork happens, switch the repo URL.
#
# Role: unified telemetry collector for the LGTM stack.
#   - Receives OTLP (gRPC 4317 + HTTP 4318) from in-cluster apps
#   - Discovers ServiceMonitors + PodMonitors → scrapes → writes to Mimir
#   - Tails every Pod's stdout/stderr via the Kubelet API → writes to Loki
#   - Forwards OTLP traces → Tempo, OTLP metrics → Mimir, OTLP logs → Loki
#   - Self-scrapes its own /metrics
# =============================================================================

variable "chart_version" {
  description = "grafana/alloy Helm chart version. Latest: 1.8.0 (appVersion v1.16.0)."
  type        = string
  default     = "1.8.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '1.8.0'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for Alloy."
  type        = string
  default     = "alloy"
}

variable "release_name" {
  description = "Helm release name. Used as the Service name."
  type        = string
  default     = "alloy"
}

# -----------------------------------------------------------------------------
# Cluster identity (label baked into every metric and log Alloy emits)
# -----------------------------------------------------------------------------

variable "cluster_label" {
  description = "Cluster identity label written into every emitted metric/log. Comes from env.hcl (cluster_name)."
  type        = string
  default     = "coelho-cloud"
}

# -----------------------------------------------------------------------------
# Downstream LGTM endpoints (in-cluster). Defaults match the v2 baseline.
# -----------------------------------------------------------------------------

variable "mimir_remote_write_url" {
  description = "Mimir distributor /api/v1/push URL (Prometheus remote_write target)."
  type        = string
  default     = "http://mimir-distributor.mimir.svc.cluster.local:8080/api/v1/push"
}

variable "loki_push_url" {
  description = "Loki /loki/api/v1/push URL. In Monolithic mode the singleBinary Service name is just the release name on port 3100."
  type        = string
  default     = "http://loki.loki.svc.cluster.local:3100/loki/api/v1/push"
}

variable "tempo_otlp_grpc_endpoint" {
  description = "Tempo OTLP gRPC endpoint (host:port — no scheme; OTel exporter adds tls.insecure separately)."
  type        = string
  default     = "tempo.tempo.svc.cluster.local:4317"
}

# -----------------------------------------------------------------------------
# Resource sizing — single Deployment, homelab
# -----------------------------------------------------------------------------
# Alloy in this role (OTLP gateway + log tailing + ServiceMonitor scraping) is
# moderately memory-hungry. v1 measured ~256Mi req, ~600Mi peak under sustained
# scraping. Below leaves headroom.
# -----------------------------------------------------------------------------

variable "cpu_request" {
  description = "CPU request for the Alloy pod."
  type        = string
  default     = "100m"
}

variable "memory_request" {
  description = "Memory request for the Alloy pod."
  type        = string
  default     = "256Mi"
}

variable "memory_limit" {
  description = "Memory limit. NOTE 2026-05-25: previous default 768Mi was being silently DROPPED by the chart because `resources:` was at the wrong YAML path (top-level instead of `alloy.resources`). Now correctly nested. 512Mi + GOMEMLIMIT=450MiB is the tight working budget; bump to 768Mi if you add many ServiceMonitors or high-cardinality metrics."
  type        = string
  default     = "512Mi"
}

variable "alloy_image_tag" {
  description = "Alloy container image tag. v1.16.1 (2026) ships CVE-2026-26996 + CVE-2026-22029 fixes over chart-default v1.16.0."
  type        = string
  default     = "v1.16.1"
}

variable "alloy_gomemlimit" {
  description = "Go runtime soft memory ceiling (GOMEMLIMIT). ~90% of memory_limit so Go GC fires aggressively below the cgroup hard limit. The pod was previously BestEffort QoS (no limit applied due to chart YAML-path bug) → Go runtime had no memory pressure signal → RSS drifted to 1.2 GiB."
  type        = string
  default     = "450MiB"
}

variable "alloy_gogc" {
  description = "GOGC target percentage. Default 100 → 75 = trigger GC at 75% growth, ~5% CPU cost for noticeably tighter RSS."
  type        = number
  default     = 75
}

variable "alloy_log_namespaces" {
  description = "Allowlist of namespaces for log collection (discovery.kubernetes filter). Default omits kube-system, cattle-*, helm-operation-*, local-path-storage (high-volume, low-signal). Add new namespaces as you deploy apps you care about."
  type        = list(string)
  default = [
    # LGTM stack itself
    "mimir", "loki", "tempo", "grafana", "monitoring",
    # Apps
    "gitlab", "airflow", "elasticsearch", "langfuse",
    "openwebui", "mlflow", "neo4j", "qdrant",
    "minio", "postgresql", "redis",
    "argocd", "homepage", "vaultwarden",
    "playwright", "coelhonexus-dev",
    "default", "tailscale", "elastic-system",
    "pgadmin", "redisinsight",
  ]
}

variable "alloy_enable_otlp_receiver" {
  description = "Run the otelcol.receiver.otlp listener on :4317 (gRPC) and :4318 (HTTP). Disable when no in-cluster app pushes OTLP — saves ~30-50 MiB of receiver buffer pools. Re-enable if/when an app starts pushing OTLP traces/metrics/logs to alloy."
  type        = bool
  default     = false
}
