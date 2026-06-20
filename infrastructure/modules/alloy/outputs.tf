# =============================================================================
# alloy module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Alloy is installed."
  value       = kubernetes_namespace_v1.alloy.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.alloy.version
}

output "app_version" {
  description = "Alloy app version (matches chart's appVersion)."
  value       = helm_release.alloy.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster OTLP endpoints — apps push their telemetry here
# -----------------------------------------------------------------------------

output "otlp_grpc_endpoint" {
  description = "OTLP gRPC endpoint for in-cluster apps (host:port). OTel SDKs use this format."
  value       = "${var.release_name}.${var.namespace}.svc.cluster.local:4317"
}

output "otlp_http_endpoint" {
  description = "OTLP HTTP endpoint for in-cluster apps."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:4318"
}

output "ui_url_inside_cluster" {
  description = "Alloy's debug UI URL, in-cluster only (port-forward to access from a laptop). Shows the live component graph + flow stats."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:12345"
}

# -----------------------------------------------------------------------------
# External OTLP HTTP endpoint (only populated when Tailscale exposure is on)
# -----------------------------------------------------------------------------

output "otlp_http_external_url" {
  description = "External OTLP HTTP endpoint (HTTPS-terminated by the Tailscale proxy). Use as OTEL_EXPORTER_OTLP_ENDPOINT for laptop / off-cluster apps. Empty string when expose_otlp_http_via_tailscale=false."
  value = (var.expose_otlp_http_via_tailscale && var.tailscale_domain != "" ?
    "https://${var.tailscale_hostname}.${var.tailscale_domain}" :
  "")
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.alloy.status
}
