# =============================================================================
# mimir module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Mimir is installed."
  value       = kubernetes_namespace_v1.mimir.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.mimir.version
}

output "app_version" {
  description = "Mimir app version (matches chart's appVersion)."
  value       = helm_release.mimir.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — consumed by Alloy (writes) and Grafana (queries)
# -----------------------------------------------------------------------------

output "remote_write_url" {
  description = "URL Alloy/Prometheus writes metrics to (HTTP, in-cluster)."
  value       = "http://${var.release_name}-distributor.${var.namespace}.svc.cluster.local:8080/api/v1/push"
}

output "query_url" {
  description = "URL Grafana queries Mimir at (Prometheus-compatible API). The Grafana datasource ConfigMap shipped by this module already points here."
  value       = "http://${var.release_name}-gateway.${var.namespace}.svc.cluster.local/prometheus"
}

output "gateway_service" {
  description = "Name of the unified gateway Service (external Ingress backend)."
  value       = "${var.release_name}-gateway"
}

output "ready" {
  description = "Helm release status string ('deployed' on success). Use as marker for downstream `dependency` blocks (Alloy waits on this)."
  value       = helm_release.mimir.status
}
