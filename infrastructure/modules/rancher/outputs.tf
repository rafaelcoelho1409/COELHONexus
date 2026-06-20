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

output "rancher_url" {
  description = "Full HTTPS URL to reach Rancher's UI from any Tailnet member."
  value       = "https://${var.tailscale_hostname}.${var.tailscale_domain}"
}

output "tailscale_hostname" {
  description = "Short hostname registered on the tailnet (without domain). Useful when migrating cutover ('rancher-v2' → 'rancher')."
  value       = var.tailscale_hostname
}

output "ready" {
  description = "Helm release status string ('deployed' on success). Use as a marker for downstream `dependency` blocks."
  value       = helm_release.rancher.status
}
