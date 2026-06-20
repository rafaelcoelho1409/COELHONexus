# =============================================================================
# rancher module — inputs
# =============================================================================

# -----------------------------------------------------------------------------
# Chart + namespace
# -----------------------------------------------------------------------------

variable "chart_version" {
  description = "rancher-stable/rancher Helm chart version. Latest stable: 2.14.1."
  type        = string
  default     = "2.14.1"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '2.14.1' (no 'v' prefix)."
  }
}

variable "rancher_image_tag" {
  description = "Rancher container image tag. Should match chart_version with 'v' prefix."
  type        = string
  default     = "v2.14.1"
}

variable "namespace" {
  description = "Kubernetes namespace for Rancher. Convention is 'cattle-system' (used by Rancher's own internal references)."
  type        = string
  default     = "cattle-system"
}

variable "release_name" {
  description = "Helm release name. Used as the prefix for in-cluster Service names (the Tailscale Ingress backend references this)."
  type        = string
  default     = "rancher"
}

# -----------------------------------------------------------------------------
# Network exposure (Tailscale)
# -----------------------------------------------------------------------------

variable "tailscale_hostname" {
  description = "Short hostname for the Tailnet (e.g., 'rancher-v2' → rancher-v2.<domain>.ts.net). The leaf passes this with the migration suffix."
  type        = string
}

variable "tailscale_domain" {
  description = "Tailnet domain (e.g., 'YOUR_TAILNET_DOMAIN.ts.net'). Comes from env.hcl."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "IngressClass name configured by the upstream tailscale-operator unit. Almost always 'tailscale'."
  type        = string
  default     = "tailscale"
}

variable "tls_source" {
  description = "Rancher chart `tls` value. `external` = Rancher serves HTTP only; expects an external proxy (Tailscale operator) to terminate TLS — this is the COELHO Cloud default. `rancher` = Rancher generates self-signed certs and terminates TLS itself — required on standalone clusters with no TLS-terminating proxy (e.g., COELHONexus k3d)."
  type        = string
  default     = "external"

  validation {
    condition     = contains(["external", "rancher", "ingress"], var.tls_source)
    error_message = "tls_source must be one of: external, rancher, ingress."
  }
}

variable "hostname_override" {
  description = "Override the auto-constructed `<tailscale_hostname>.<tailscale_domain>` hostname Rancher uses in self-referencing URLs (e.g., the post-login redirect). Set to `localhost` on standalone clusters so port-forwarded access works without DNS gymnastics. Empty string = use the Tailscale-style construction."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Bootstrap (sensitive)
# -----------------------------------------------------------------------------

variable "bootstrap_password" {
  description = "Initial admin password used for first login. Rancher forces a password change on first login; after that this value is historical. Sourced from SOPS-encrypted secrets."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.bootstrap_password) >= 12
    error_message = "bootstrap_password must be ≥12 chars (Rancher's own minimum)."
  }
}

# -----------------------------------------------------------------------------
# Resource sizing — tuned in v1 from real measurements
# -----------------------------------------------------------------------------
# Real usage: ~13m CPU, ~936Mi RAM. Memory limit MUST be ≥1.5Gi — chart was
# OOMKilled at 1Gi during startup (exit 137).
# -----------------------------------------------------------------------------

variable "replicas" {
  description = "Number of Rancher pods. Homelab default = 1."
  type        = number
  default     = 1

  validation {
    condition     = var.replicas >= 1
    error_message = "replicas must be at least 1."
  }
}

variable "cpu_request" {
  description = "CPU request per Rancher pod."
  type        = string
  default     = "100m"
}

variable "memory_request" {
  description = "Memory request per Rancher pod."
  type        = string
  default     = "512Mi"
}

variable "memory_limit" {
  description = "Memory limit per Rancher pod. Below 1.5Gi causes OOMKilled during startup (verified in v1)."
  type        = string
  default     = "1.5Gi"
}

# -----------------------------------------------------------------------------
# Optional features
# -----------------------------------------------------------------------------

variable "audit_log_level" {
  description = "Rancher audit log level (0=metadata only, 3=full bodies). 0 keeps disk + CPU low for homelab."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1, 2, 3], var.audit_log_level)
    error_message = "audit_log_level must be one of: 0, 1, 2, 3."
  }
}

variable "enable_prometheus_metrics" {
  description = "Set CATTLE_PROMETHEUS_METRICS=true so Rancher emits metrics on /metrics."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Go runtime + Cattle worker tuning — the OOM-cascade fix (2026-05-24)
# -----------------------------------------------------------------------------
# Rancher pod was OOMKilling (exit 137, 4× in 22h) at the 1.5Gi limit when the
# default 5 worker threads + 15s resync interval caused RSS spikes past the
# limit. GOMEMLIMIT + GOGC give the Go runtime a soft ceiling so GC fires
# AGGRESSIVELY before kubelet's OOMKiller, eliminating the cascade that took
# helmops (and other controllers) down with it.
# -----------------------------------------------------------------------------

variable "gomemlimit" {
  description = "Go runtime soft memory ceiling (GOMEMLIMIT). Set ~10% below pod memory_limit so GC fires before OOMKiller does. Rancher images since v2.9.0 use Go 1.21+."
  type        = string
  default     = "1300MiB"
}

variable "gogc" {
  description = "Go GC target percentage (GOGC). Default 100 → 75 = trigger GC at 75% growth; tighter RSS at minor CPU cost. Imperceptible on single-cluster homelab."
  type        = number
  default     = 75
}

variable "cattle_worker_count" {
  description = "CATTLE_WORKER_COUNT — concurrent reconcile workers per controller. Default 5; only need 1 for a single-cluster homelab. Raise back if you ever add downstream clusters."
  type        = number
  default     = 1
}

variable "cattle_resync_seconds" {
  description = "CATTLE_RESYNC_DEFAULT — full-resync period in seconds. Default 15s → 60s cuts reconcile cycles 4× without any user-visible latency impact."
  type        = number
  default     = 60
}

# -----------------------------------------------------------------------------
# Rancher feature flags — disable subsystems unused at homelab scale
# -----------------------------------------------------------------------------
# Disabling these does NOT remove the Fleet pods (provisioningv2 architectural
# lock-in keeps fleet-controller + fleet-agent running) but it DOES:
#   - delete the gitjob Deployment (~62 MiB saved)
#   - stop loading provisioningv2 controllers (reduces RSS in rancher pod)
#   - stop loading rke2/k3s provisioning drivers
# Side effects: Fleet Continuous Delivery UI tab disappears (you use Argo CD),
# Rancher cluster provisioning UI disappears (you use k3d/Terraform).
# -----------------------------------------------------------------------------

variable "rancher_features" {
  description = "Rancher --features CSV (key=value). Disable continuous-delivery (Fleet CD UI + gitjob), rke2 (RKE2 provisioning driver), provisioningv2 (v2 cluster provisioning controllers). Do NOT add multi-cluster-management=false — that's immutable at install time and removes UI features you want."
  type        = string
  default     = "continuous-delivery=false,rke2=false,provisioningv2=false"
}

# -----------------------------------------------------------------------------
# Cleanup of Rancher's auto-installed sub-components (managed via null_resource
# because Rancher's app-catalog installs are NOT exposed as standalone helm
# repos — we can't `helm_release` them without first importing the existing
# release into Terraform state. null_resource + local-exec keeps the intent
# tracked in tfstate (re-runs only when triggers change) while still being
# declarative from the user's perspective.
# -----------------------------------------------------------------------------

variable "enable_system_upgrade_controller" {
  description = "Deploy system-upgrade-controller in cattle-system. Only needed for RKE2/K3s downstream node OS upgrades — irrelevant on k3d (host-managed via `docker restart`). When false, a null_resource uninstalls the helm release if present."
  type        = bool
  default     = false
}

variable "rancher_webhook_memory_limit" {
  description = "Memory limit applied to the rancher-webhook Deployment via strategic patch. Chart leaves limits unset (Burstable, unbounded). 192Mi fits the ~115 MiB observed steady-state with headroom."
  type        = string
  default     = "192Mi"
}

variable "enable_turtles_capi" {
  description = "Whether Rancher Turtles should keep the embedded CAPI providers running. False = patch turtles args to add embedded-capi=false to feature-gates + scale capi-controller-manager to 0 (saves ~48 MiB). Required only if you provision downstream clusters via CAPI (you don't on k3d homelab)."
  type        = bool
  default     = false
}
