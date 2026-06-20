# =============================================================================
# tempo module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Tempo is installed."
  value       = kubernetes_namespace_v1.tempo.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.tempo.version
}

output "app_version" {
  description = "Tempo app version (matches chart's appVersion)."
  value       = helm_release.tempo.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — consumed by Alloy (writes/forward) and Grafana (queries)
# -----------------------------------------------------------------------------
# Single-binary Service is named `<release>` (no chart-name suffix). Listens on:
#   3200  — Tempo HTTP API (Grafana datasource queries this)
#   4317  — OTLP gRPC ingest (apps + Alloy push traces here)
#   4318  — OTLP HTTP ingest
# -----------------------------------------------------------------------------

output "query_url" {
  description = "URL Grafana queries Tempo at. The datasource ConfigMap shipped by this module already points here."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:3200"
}

output "otlp_grpc_endpoint" {
  description = "OTLP gRPC endpoint apps + Alloy push traces to (host:port format used by OTel SDKs)."
  value       = "${var.release_name}.${var.namespace}.svc.cluster.local:4317"
}

output "otlp_http_endpoint" {
  description = "OTLP HTTP endpoint."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:4318"
}

output "service_name" {
  description = "Service name (== release name in single-binary mode)."
  value       = var.release_name
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.tempo.status
}
