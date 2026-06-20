# =============================================================================
# langfuse module — provider requirements
# =============================================================================
# No docker provider: vanilla `langfuse/langfuse:3.172.1` image (chart-pinned,
# MIT/Apache-2). No CRDs ship with the chart, so kubernetes_manifest is fine
# for Ingress + Backup CronJob (no kubectl provider needed).
# =============================================================================

terraform {
  required_version = ">= 1.10"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }

    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
  }
}
