# =============================================================================
# monitoring-crds module — provider requirements
# =============================================================================
#
# Only `helm` is needed:
#   - The chart's `create_namespace = true` setting handles the namespace.
#   - We don't deploy any direct kubernetes_* resources, so no kubernetes provider.
# =============================================================================

terraform {
  required_version = ">= 1.10"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
  }
}
