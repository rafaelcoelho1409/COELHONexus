# =============================================================================
# K3D MODULE - Kubernetes Cluster in Docker
# =============================================================================
#
# Uses k3d config file for clean, declarative cluster definition.
# Registry mirrors are configured inline - no separate registries.yaml needed.
#
# For development, use Skaffold which handles port forwarding automatically.
# =============================================================================

terraform {
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
  }
}

# -----------------------------------------------------------------------------
# Generate k3d config file from template
# -----------------------------------------------------------------------------
resource "local_file" "k3d_config" {
  content = templatefile("${path.module}/k3d-config.yaml.tpl", {
    cluster_name  = var.cluster_name
    k3s_version   = var.k3s_version
    servers       = var.servers
    agents        = var.agents
    registry_port = var.registry_port
    volumes       = var.volume_mounts
  })
  filename = "${path.module}/.generated/k3d-config.yaml"
}

# -----------------------------------------------------------------------------
# Create K3D Cluster using config file
# -----------------------------------------------------------------------------
resource "null_resource" "cluster" {
  depends_on = [local_file.k3d_config]

  triggers = {
    cluster_name   = var.cluster_name
    config_content = local_file.k3d_config.content
  }

  provisioner "local-exec" {
    command = "k3d cluster create --config ${local_file.k3d_config.filename}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "k3d cluster delete ${self.triggers.cluster_name} 2>/dev/null || true"
  }
}

# -----------------------------------------------------------------------------
# Extract kubeconfig to local file
# -----------------------------------------------------------------------------
resource "null_resource" "extract_kubeconfig" {
  depends_on = [null_resource.cluster]

  triggers = {
    cluster_id = null_resource.cluster.id
  }

  provisioner "local-exec" {
    command = "k3d kubeconfig get ${var.cluster_name} > ${path.root}/kubeconfig && chmod 600 ${path.root}/kubeconfig"
  }
}

# -----------------------------------------------------------------------------
# Wait for cluster to be ready
# -----------------------------------------------------------------------------
resource "null_resource" "wait_for_cluster" {
  depends_on = [null_resource.extract_kubeconfig]

  triggers = {
    cluster_id = null_resource.cluster.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      echo "Waiting for Kubernetes API..."
      for i in {1..30}; do
        if kubectl cluster-info --context=k3d-${var.cluster_name} >/dev/null 2>&1; then
          echo "API ready, waiting for nodes..."
          kubectl wait --for=condition=Ready nodes --all --timeout=120s --context=k3d-${var.cluster_name}
          echo "All nodes ready"
          exit 0
        fi
        echo "Waiting for API... ($i/30)"
        sleep 2
      done
      echo "Timeout waiting for cluster"
      exit 1
    EOT
  }
}

# -----------------------------------------------------------------------------
# Configure auto-restart for cluster containers
# -----------------------------------------------------------------------------
resource "null_resource" "configure_auto_restart" {
  depends_on = [null_resource.wait_for_cluster]

  triggers = {
    cluster_id = null_resource.cluster.id
  }

  provisioner "local-exec" {
    command = "docker update --restart=unless-stopped $(docker ps -aq --filter 'name=k3d-${var.cluster_name}') 2>/dev/null || true"
  }
}
