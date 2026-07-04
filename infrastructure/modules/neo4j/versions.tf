# =============================================================================
# neo4j module — provider requirements
# =============================================================================
# `tls` provider was used in an earlier iteration to generate a self-signed
# Bolt cert. Removed when we switched to external Ingress for Bolt (LE cert
# auto-provisioned by the external ingress controller).
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
  }
}
