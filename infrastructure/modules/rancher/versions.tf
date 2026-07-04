# =============================================================================
# rancher module — provider requirements
# =============================================================================
#
# Three providers needed:
#   - helm: deploy the Rancher chart
#   - kubernetes: create namespace explicitly + apply the external Ingress
#   - null: drive local-exec for cleanup of Rancher-auto-installed sub-components
#          (system-upgrade-controller uninstall, webhook patch, Turtles starve)
#          that aren't exposed as standalone helm releases we could manage directly.
#
# Note: Rancher's chart DOES ship some CRDs (Project, GlobalRole, etc.), but
# we don't deploy any custom resources of those kinds — only a built-in
# Ingress kind, which is always present in any cluster. So plain
# kubernetes_manifest works (no kubectl_manifest needed for this module).
# =============================================================================

terraform {
  required_version = ">= 1.10"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
