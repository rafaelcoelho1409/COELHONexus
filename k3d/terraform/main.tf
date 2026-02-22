# K3D coelhonexus Cluster - Terraform Configuration
#
# This configuration replicates the K3D cluster setup with all services:
# - K3D cluster with 1 server + 3 agents
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply

# Compute project root from module path (k3d/terraform -> project root)
locals {
  project_root = abspath("${path.module}/../..")

  # Default volume mounts using project-relative paths
  # Using "all" filter to mount on both server and agent nodes
  default_volume_mounts = [
    {
      host_path      = "${local.project_root}/data/minio"
      container_path = "/data/minio"
      node_filter    = "all"
    },
    {
      host_path      = "${local.project_root}/data/postgresql"
      container_path = "/data/postgresql"
      node_filter    = "all"
    },
  ]

  # Use provided volume_mounts or fall back to defaults
  volume_mounts = var.volume_mounts != null ? var.volume_mounts : local.default_volume_mounts
}

# K3D Cluster Module
module "k3d" {
  source = "./modules/k3d"

  cluster_name  = var.cluster_name
  k3s_version   = var.k3s_version
  servers       = var.servers
  agents        = var.agents
  registry_port = var.registry_port

  # Volume mounts for persistent storage (MinIO and PostgreSQL data)
  # No port mappings needed - Skaffold handles port forwarding for development
  volume_mounts = local.volume_mounts
}


# Rancher Module
module "rancher" {
  count  = var.install_rancher ? 1 : 0
  source = "./modules/rancher"

  cluster_name       = var.cluster_name
  http_node_port     = var.rancher_http_node_port
  https_node_port    = var.rancher_https_node_port
  bootstrap_password = var.rancher_bootstrap_password

  # Use external values file from the module directory
  values_file = "${path.module}/modules/rancher/values.yaml"

  # Explicitly depend on cluster API being ready
  cluster_ready = module.k3d.cluster_ready
  depends_on    = [module.k3d]
}


#To destroy all resources
#k3d cluster delete coelhonexus
#terraform state rm $(terraform state list)
#terraform destroy