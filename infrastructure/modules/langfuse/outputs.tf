# =============================================================================
# langfuse module — outputs
# =============================================================================

output "namespace" {
  description = "Kubernetes namespace where Langfuse is installed."
  value       = kubernetes_namespace_v1.langfuse.metadata[0].name
}

output "release_name" {
  description = "Helm release name."
  value       = helm_release.langfuse.name
}

output "chart_version" {
  description = "Pinned chart version."
  value       = helm_release.langfuse.version
}

output "web_service_host" {
  description = "In-cluster web service hostname (for SDK clients on the cluster)."
  value       = "${helm_release.langfuse.name}-web.${kubernetes_namespace_v1.langfuse.metadata[0].name}.svc.cluster.local"
}

output "web_url_internal" {
  description = "In-cluster Langfuse base URL (port 3000)."
  value       = "http://${helm_release.langfuse.name}-web.${kubernetes_namespace_v1.langfuse.metadata[0].name}.svc.cluster.local:3000"
}

output "public_url" {
  description = "Browser URL for Langfuse. Uses `public_url` when provided, otherwise the external URL."
  value       = local.public_url
}
