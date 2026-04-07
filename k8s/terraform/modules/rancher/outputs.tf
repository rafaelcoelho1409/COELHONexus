output "namespace" {
  description = "Rancher namespace"
  value       = kubernetes_namespace.rancher.metadata[0].name
}

output "release_name" {
  description = "Rancher Helm release name"
  value       = helm_release.rancher.name
}

output "http_url" {
  description = "Rancher HTTP URL"
  value       = "http://localhost:${var.http_node_port}"
}

output "https_url" {
  description = "Rancher HTTPS URL"
  value       = "https://localhost:${var.https_node_port}"
}

output "bootstrap_password" {
  description = "Rancher bootstrap password"
  value       = var.bootstrap_password
  sensitive   = true
}
