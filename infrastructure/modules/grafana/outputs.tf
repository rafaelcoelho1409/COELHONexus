# =============================================================================
# grafana module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Grafana is installed."
  value       = kubernetes_namespace_v1.grafana.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.grafana.version
}

output "app_version" {
  description = "Grafana app version (matches chart's appVersion)."
  value       = helm_release.grafana.metadata.app_version
}

output "url" {
  description = "Full HTTPS URL to reach Grafana from any tailnet member."
  value       = "https://${var.tailscale_hostname}.${var.tailscale_domain}"
}

output "tailscale_hostname" {
  description = "Short hostname registered on the tailnet (without domain)."
  value       = var.tailscale_hostname
}

output "admin_user" {
  description = "Grafana admin username (default 'admin')."
  value       = "admin"
}

output "admin_password" {
  description = "Grafana admin password. Retrieve via `terragrunt output -raw admin_password`."
  value       = random_password.admin.result
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Datasource sidecar contract — exported for downstream modules
# -----------------------------------------------------------------------------
# Mimir/Loki/Tempo modules consume these to label their ConfigMaps so the
# Grafana sidecar picks them up. Keeps the wiring contract in one place.
# -----------------------------------------------------------------------------

output "datasource_label_selector" {
  description = "Label key/value the Grafana sidecar uses to discover datasource ConfigMaps. Datasource modules must apply this label."
  value = {
    "grafana_datasource" = "1"
  }
}

output "dashboard_label_selector" {
  description = "Label key/value the Grafana sidecar uses to discover dashboard ConfigMaps."
  value = {
    "grafana_dashboard" = "1"
  }
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.grafana.status
}
