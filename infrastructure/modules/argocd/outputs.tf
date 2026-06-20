# =============================================================================
# argocd module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where ArgoCD is installed."
  value       = kubernetes_namespace_v1.argocd.metadata[0].name
}

output "chart_version" {
  description = "Installed ArgoCD Helm chart version."
  value       = helm_release.argocd.version
}

output "app_version" {
  description = "ArgoCD app version (matches chart's appVersion)."
  value       = helm_release.argocd.metadata.app_version
}

output "image_updater_chart_version" {
  description = "Installed Image Updater chart version. Empty when enable_image_updater=false."
  value       = var.enable_image_updater ? helm_release.image_updater[0].version : ""
}

# -----------------------------------------------------------------------------
# External (Tailnet) URLs
# -----------------------------------------------------------------------------

output "url" {
  description = "Web UI URL — log in as `admin` / <kubectl get secret argocd-initial-admin-secret>."
  value       = "https://${var.tailscale_hostname}.${var.tailscale_domain}"
}

output "tailscale_hostname" {
  description = "Tailnet hostname (without domain)."
  value       = var.tailscale_hostname
}

# -----------------------------------------------------------------------------
# In-cluster endpoint — used by argocd CLI from within the cluster
# -----------------------------------------------------------------------------

output "in_cluster_url" {
  description = "ArgoCD server in-cluster Service URL (gRPC + HTTP)."
  value       = "${var.release_name}-server.${var.namespace}.svc.cluster.local:443"
}

# -----------------------------------------------------------------------------
# Initial admin password retrieval hint
# -----------------------------------------------------------------------------

output "admin_password_command" {
  description = "kubectl one-liner that prints the initial admin password. The chart auto-generates it into a Secret on first install."
  value       = "kubectl -n ${var.namespace} get secret ${var.release_name}-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.argocd.status
}
