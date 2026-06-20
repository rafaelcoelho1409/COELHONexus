# =============================================================================
# postgresql module — provider requirements
# =============================================================================
#
# Three providers needed:
#   - helm: deploy bitnami/postgresql chart (18.6.2, app v18)
#   - kubernetes: namespace + Secret for backup CronJob + the CronJob itself
#   - random: generate per-app DB passwords (NOT used for the admin password —
#     that comes from SOPS for v1 reuse continuity)
#
# Chart provenance:
#   v1 used bitnami/postgresql 18.2.3. v2 bumps to 18.6.2 (latest stable).
#   Same chart family, same standalone-mode primary path. Bitnami chart 18.x
#   deploys PostgreSQL 18 (per appVersion). API surface is stable across
#   18.x — chart upgrade is in-place safe.
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
  }
}
