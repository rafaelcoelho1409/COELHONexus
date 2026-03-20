# K3D coelhonexus Cluster - Terraform Configuration
#
# This configuration replicates the K3D cluster setup with all services:
# - K3D cluster with 1 server + 3 agents
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply

# K3D Cluster Module
# Databases are hosted on COELHO Cloud - no local volume mounts needed
module "k3d" {
  source = "./modules/k3d"

  cluster_name  = var.cluster_name
  k3s_version   = var.k3s_version
  servers       = var.servers
  agents        = var.agents
  registry_port = var.registry_port

  # No volume mounts - connecting to COELHO Cloud databases
  volume_mounts = []
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