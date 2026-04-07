# =============================================================================
# K3D MODULE OUTPUTS
# =============================================================================

output "cluster_name" {
  description = "Name of the K3D cluster"
  value       = var.cluster_name
}

output "cluster_id" {
  description = "ID of the cluster resource (for dependencies)"
  value       = null_resource.cluster.id
}

output "kubeconfig_path" {
  description = "Path to the local kubeconfig file"
  value       = "${path.root}/kubeconfig"
}

output "kubeconfig_context" {
  description = "Kubectl context name for this cluster"
  value       = "k3d-${var.cluster_name}"
}

output "registry_endpoint" {
  description = "Endpoint for the K3D local registry"
  value       = "localhost:${var.registry_port}"
}

output "registry_name" {
  description = "Registry name for internal cluster use"
  value       = "${var.cluster_name}-registry:${var.registry_port}"
}

output "cluster_ready" {
  description = "Signals that cluster is ready. Use as dependency for other modules."
  value       = null_resource.configure_auto_restart.id
}

output "k3d_config_path" {
  description = "Path to the generated k3d config file"
  value       = local_file.k3d_config.filename
}
