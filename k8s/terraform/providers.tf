# Kubernetes Provider
# Configured to use the kubeconfig from the K3D cluster
# K3D automatically switches to the new cluster context, so we use current context
# This allows fresh installations to work (cluster doesn't exist during provider init)
provider "kubernetes" {
  config_path = pathexpand("~/.kube/config")
}

# Helm Provider
# Configured to use the kubeconfig from the K3D cluster
# K3D automatically switches to the new cluster context, so we use current context
provider "helm" {
  kubernetes = {
    config_path = pathexpand("~/.kube/config")
  }
}
