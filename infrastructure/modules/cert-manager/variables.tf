# =============================================================================
# cert-manager module — inputs
# =============================================================================

variable "chart_version" {
  description = "jetstack/cert-manager Helm chart version. Latest stable as of 2026-06: v1.15.3. Note the 'v' prefix matches the chart convention."
  type        = string
  default     = "v1.15.3"

  validation {
    # cert-manager chart uses `v` prefix (e.g., "v1.15.3"), unlike most other
    # charts. Validation matches that convention.
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be a SemVer string with 'v' prefix like 'v1.15.3'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for cert-manager. Convention: 'cert-manager'."
  type        = string
  default     = "cert-manager"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.namespace))
    error_message = "namespace must be lowercase alphanumeric and hyphens (RFC 1123 DNS label)."
  }
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "cert-manager"
}

variable "helm_timeout" {
  description = "Helm install/upgrade timeout in seconds. cert-manager has 3 deployments + webhook validation to come up; 300s is generous."
  type        = number
  default     = 300
}
