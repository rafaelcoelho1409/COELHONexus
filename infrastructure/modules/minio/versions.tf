# =============================================================================
# minio module — provider requirements
# =============================================================================
#
# Two providers needed:
#   - helm: deploy the MinIO chart from charts.min.io
#   - kubernetes: namespace + external Ingresses + ServiceMonitor (all are
#     built-in or come from prometheus-operator CRDs already installed by
#     the monitoring-crds unit; plain kubernetes_manifest is sufficient,
#     no kubectl_manifest needed since this module deploys no custom
#     resources from external CRDs).
#
# Note on chart choice: v2 uses the same charts.min.io chart that v1 ran
# successfully on this hardware. The repository was archived 2026-04-25
# but is read-only, not deleted — `helm pull` still works against the
# pinned 5.4.0 release. Bitnami's MinIO 17.x was evaluated but had local
# deployment issues per user's prior experience.
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
