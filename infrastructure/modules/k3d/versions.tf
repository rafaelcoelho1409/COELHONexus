# =============================================================================
# k3d module — provider requirements
# =============================================================================
#
# A *pure* OpenTofu module: no `provider {}` blocks here, only required_providers.
# Why: Terragrunt generates per-unit provider config. If we hardcoded providers
# here, every unit consuming this module would be locked to those providers.
#
# This module uses null_resource (for local-exec wrapping the k3d CLI) and
# local_file (for tracking the kubeconfig). No kubernetes/helm here — the
# cluster doesn't exist yet at this stage.
# =============================================================================

terraform {
  # Pin minimum OpenTofu version. Anything below 1.10 won't have native S3
  # state locking, which root.hcl relies on.
  required_version = ">= 1.10"

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}
