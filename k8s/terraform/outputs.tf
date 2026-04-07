# K3D Cluster Outputs
output "cluster_name" {
  description = "Name of the K3D cluster"
  value       = module.k3d.cluster_name
}

output "cluster_context" {
  description = "Kubectl context for the cluster"
  value       = module.k3d.kubeconfig_context
}

output "registry_endpoint" {
  description = "Endpoint for the K3D registry"
  value       = module.k3d.registry_endpoint
}

# Rancher Outputs
output "rancher_http_url" {
  description = "Rancher HTTP URL"
  value       = var.install_rancher ? "http://localhost:${var.rancher_http_host_port}" : "Not installed"
}

output "rancher_https_url" {
  description = "Rancher HTTPS URL"
  value       = var.install_rancher ? "https://localhost:${var.rancher_https_host_port}" : "Not installed"
}

output "rancher_bootstrap_password" {
  description = "Rancher bootstrap password (change on first login)"
  value       = var.install_rancher ? var.rancher_bootstrap_password : "Not installed"
  sensitive   = true
}

# Summary Output
output "summary" {
  description = "Summary of all services"
  value       = <<-EOT

    ========================================
    K3D Cluster Setup Complete!
    ========================================

    Cluster: ${module.k3d.cluster_name}
    Context: ${module.k3d.kubeconfig_context}
    Registry: ${module.k3d.registry_endpoint}

    Services:
    ${var.install_rancher ? "  - Rancher:    http://localhost:${var.rancher_http_host_port}" : ""}

    Credentials:
    ${var.install_rancher ? "  - Rancher bootstrap password:\n    terraform output rancher_bootstrap_password" : ""}

    Next Steps:
      1. Verify cluster: kubectl get nodes
      2. Check services: kubectl get pods --all-namespaces
      3. Access services via the URLs above

  EOT
}
