# =============================================================================
# grafana module — provider requirements
# =============================================================================
#
# The grafana-community/grafana chart ships no CRDs, so plain
# `kubernetes_manifest` is fine for the external Ingress (no deferred
# validation needed; cf. feedback_kubectl_manifest_pattern).
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
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    # Used by dashboards.tf to fetch JSON from grafana.com on every apply.
    http = {
      source  = "hashicorp/http"
      version = "~> 3.5"
    }
  }
}
