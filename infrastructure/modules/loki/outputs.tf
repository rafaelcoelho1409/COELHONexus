# =============================================================================
# loki module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Loki is installed."
  value       = kubernetes_namespace_v1.loki.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.loki.version
}

output "app_version" {
  description = "Loki app version (matches chart's appVersion)."
  value       = helm_release.loki.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — consumed by Alloy (writes) and Grafana (queries)
# -----------------------------------------------------------------------------
# In Monolithic mode, the SingleBinary StatefulSet exposes a single Service
# named `<release>` that handles BOTH push (port 3100 /loki/api/v1/push) and
# query (port 3100 /loki/api/v1/query). No separate gateway needed.
# -----------------------------------------------------------------------------

output "push_url" {
  description = "URL Alloy pushes log streams to (HTTP, in-cluster)."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:3100/loki/api/v1/push"
}

output "query_url" {
  description = "URL Grafana queries Loki at (LogQL API). The Grafana datasource ConfigMap shipped by this module already points here."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:3100"
}

output "service_name" {
  description = "Service name for the SingleBinary StatefulSet (== release name in Monolithic mode)."
  value       = var.release_name
}

output "ready" {
  description = "Helm release status string ('deployed' on success). Use as marker for downstream `dependency` blocks (Alloy waits on this)."
  value       = helm_release.loki.status
}
