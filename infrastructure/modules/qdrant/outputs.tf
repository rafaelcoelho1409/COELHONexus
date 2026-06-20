# =============================================================================
# qdrant module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Qdrant is installed."
  value       = kubernetes_namespace_v1.qdrant.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.qdrant.version
}

output "app_version" {
  description = "Qdrant app version (matches chart's appVersion)."
  value       = helm_release.qdrant.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — apps point here
# -----------------------------------------------------------------------------

output "rest_endpoint" {
  description = "Qdrant REST API endpoint (HTTP, in-cluster). Use as `QDRANT_URL` for Python qdrant-client."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:6333"
}

output "grpc_endpoint" {
  description = "Qdrant gRPC endpoint (in-cluster). Use as `QDRANT_GRPC_URL` for performance-sensitive paths."
  value       = "${var.release_name}.${var.namespace}.svc.cluster.local:6334"
}

# -----------------------------------------------------------------------------
# External (Tailnet) URL — for laptop dev workflows + the Dashboard UI
# -----------------------------------------------------------------------------

output "url" {
  description = "Web UI + REST endpoint via Tailscale. Append /dashboard for the built-in UI."
  value       = "https://${var.tailscale_hostname}.${var.tailscale_domain}"
}

output "dashboard_url" {
  description = "Direct link to the Qdrant Dashboard."
  value       = "https://${var.tailscale_hostname}.${var.tailscale_domain}/dashboard"
}

output "tailscale_hostname" {
  description = "Tailnet hostname (without domain)."
  value       = var.tailscale_hostname
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.qdrant.status
}
