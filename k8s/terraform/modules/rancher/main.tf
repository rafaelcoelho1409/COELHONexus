terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

# Create Rancher namespace
resource "kubernetes_namespace" "rancher" {
  metadata {
    name = var.namespace
    annotations = {
      "cluster-ready" = var.cluster_ready
    }
  }
}

# Install Rancher
resource "helm_release" "rancher" {
  name       = var.release_name
  repository = "https://releases.rancher.com/server-charts/stable"
  chart      = "rancher"
  namespace  = kubernetes_namespace.rancher.metadata[0].name
  version    = var.chart_version != "" ? var.chart_version : null
  timeout    = 600

  wait             = true
  wait_for_jobs    = true
  create_namespace = false

  # Values from external YAML file
  values = [file(var.values_file)]
}

# Patch Rancher service with specific NodePort values
resource "null_resource" "patch_rancher_service" {
  depends_on = [helm_release.rancher]

  provisioner "local-exec" {
    command = <<-EOT
      kubectl patch service ${var.release_name} -n ${var.namespace} --type='json' -p='[
        {"op": "replace", "path": "/spec/ports/0/nodePort", "value": ${var.http_node_port}},
        {"op": "replace", "path": "/spec/ports/1/nodePort", "value": ${var.https_node_port}}
      ]'
    EOT
  }
}

# Wait for Rancher to be ready
resource "null_resource" "wait_for_rancher" {
  depends_on = [null_resource.patch_rancher_service]

  provisioner "local-exec" {
    command = "kubectl wait --namespace ${var.namespace} --for=condition=ready pod --selector=app=${var.release_name} --timeout=300s"
  }
}
