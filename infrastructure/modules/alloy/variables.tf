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
# Role: metrics + OTLP gateway for the LGTM stack (alloy-metrics instance).
#   - Receives OTLP (gRPC 4317 + HTTP 4318) from in-cluster apps
#   - Discovers ServiceMonitors + PodMonitors → scrapes → writes to Mimir
#   - Forwards OTLP traces → Tempo, OTLP metrics → Mimir, OTLP logs → Loki
#   - Self-scrapes its own /metrics
#
# Pod log tailing lives in the separate helm_release.alloy_logs (DaemonSet,
# file-based tailing) defined in this same module — ported from the
# COELHO Cloud fix, 2026-09-23.
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
  description = "Memory limit. Raised 512Mi→768Mi 2026-09-23, ported from COELHO Cloud's 2026-09-05 fix: under real sustained load (ServiceMonitor growth, slow downstream exports) Alloy OOMKilled ×2 at 512Mi while retry-storming. Not cluster-specific — the same growth pattern applies to any real install of this stack, not just a fresh/idle one."
  type        = string
  default     = "768Mi"
}

variable "alloy_image_tag" {
  description = "Alloy container image tag. v1.16.1 (2026) ships CVE-2026-26996 + CVE-2026-22029 fixes over chart-default v1.16.0."
  type        = string
  default     = "v1.16.1"
}

variable "alloy_gomemlimit" {
  description = "Go runtime soft memory ceiling (GOMEMLIMIT). ~90% of memory_limit so Go GC fires aggressively below the cgroup hard limit. 450MiB→680MiB 2026-09-23 alongside the memory_limit bump above (ported from COELHO Cloud's 2026-09-05 fix)."
  type        = string
  default     = "680MiB"
}

variable "alloy_gogc" {
  description = "GOGC target percentage. Default 100 → 75 = trigger GC at 75% growth, ~5% CPU cost for noticeably tighter RSS."
  type        = number
  default     = 75
}

variable "alloy_enable_otlp_receiver" {
  description = "Run the otelcol.receiver.otlp listener on :4317 (gRPC) and :4318 (HTTP). Disable when no in-cluster app pushes OTLP — saves ~30-50 MiB of receiver buffer pools. Re-enable if/when an app starts pushing OTLP traces/metrics/logs to alloy."
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# helm_release.alloy_logs — Kubernetes pod log tailing (DaemonSet)
# -----------------------------------------------------------------------------
# Split out from helm_release.alloy 2026-09-23 (ported from the COELHO Cloud
# fix) — file-based tailing instead of the API-based `loki.source.kubernetes`,
# which leaks retrying tailers for deleted pods under frequent redeploys.
# Sized much lighter than the metrics release: each pod only tails its OWN
# node's pod logs (server-side field selector on spec.nodeName), no
# OTLP/Prometheus-scrape/WAL overhead.
# -----------------------------------------------------------------------------

variable "logs_release_name" {
  description = "Helm release name for the log-tailing DaemonSet."
  type        = string
  default     = "alloy-logs"
}

variable "logs_cpu_request" {
  description = "CPU request per alloy-logs pod (one per node)."
  type        = string
  default     = "20m"
}

variable "logs_memory_request" {
  description = "Memory request per alloy-logs pod."
  type        = string
  default     = "48Mi"
}

variable "logs_memory_limit" {
  description = "Memory limit per alloy-logs pod. File-based tailing has no WAL/receiver buffers, so this stays far below the metrics release's limit."
  type        = string
  default     = "128Mi"
}

variable "logs_gomemlimit" {
  description = "Go runtime soft memory ceiling (GOMEMLIMIT) for alloy-logs. ~90% of logs_memory_limit."
  type        = string
  default     = "115MiB"
}

variable "alloy_log_namespace_denylist" {
  description = "Regex (relabel-style alternation) of namespaces to EXCLUDE from log collection. A denylist, not an allowlist: discovery.kubernetes runs cluster-wide and this small, effectively-fixed regex of K8s/Rancher internals is dropped at the discovery.relabel stage — every current and future project namespace is collected automatically, with no terragrunt apply needed when a new one appears."
  type        = string
  default     = "kube-system|cattle-.*|helm-.*|local-path-storage"
}
