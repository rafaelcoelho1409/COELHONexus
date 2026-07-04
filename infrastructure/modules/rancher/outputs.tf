# =============================================================================
# rancher module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Rancher is installed."
  value       = kubernetes_namespace_v1.rancher.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.rancher.version
}

output "app_version" {
  description = "Rancher app version (matches the chart's appVersion)."
  value       = helm_release.rancher.metadata.app_version
}


output "ready" {
  description = "Helm release status string ('deployed' on success). Use as a marker for downstream `dependency` blocks."
  value       = helm_release.rancher.status
}
