# =============================================================================
# monitoring-crds module — inputs
# =============================================================================

variable "chart_version" {
  description = "prometheus-community/prometheus-operator-crds Helm chart version. Latest stable is 28.0.1 (app v0.90.1, released 2026-03-25)."
  type        = string
  default     = "28.0.1"

  validation {
    # Sanity: must be a SemVer-ish string. Catches typos like "28.0" or "v28.0.1".
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be a SemVer string like '28.0.1' (no 'v' prefix)."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for the Helm release metadata. The CRDs themselves are cluster-scoped (namespace ignored for CRDs); this only controls where the Helm release record lives."
  type        = string
  default     = "monitoring"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.namespace))
    error_message = "namespace must be lowercase alphanumeric and hyphens (RFC 1123 DNS label)."
  }
}

variable "helm_timeout" {
  description = "Helm install/upgrade timeout in seconds. CRDs install fast; 120s is generous."
  type        = number
  default     = 120
}
