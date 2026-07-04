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

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.qdrant.status
}
