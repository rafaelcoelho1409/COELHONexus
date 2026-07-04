# =============================================================================
# k3d module — input variables
# =============================================================================
#
# Each variable is what the leaf `terragrunt.hcl` (or root.hcl/env.hcl) feeds
# into this module. Defaults are tuned for a single-host homelab.
# =============================================================================

variable "cluster_name" {
  description = "k3d cluster name. Used in Docker container names (k3d-<name>-server-0, k3d-<name>-agent-N)."
  type        = string

  # `validation` blocks run at plan time. Fail fast on invalid inputs instead
  # of letting `k3d cluster create` reject the name 30 seconds in.
  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.cluster_name))
    error_message = "cluster_name must be lowercase alphanumeric and hyphens (RFC 1123 DNS label format)."
  }
}

variable "k3s_version" {
  description = "K3s image tag. Sets the rancher/k3s:<tag> image used by all nodes."
  type        = string
  # Default to v1.34.7-k3s1 (stable, released 2026-04-27).
  # v1.35 is also stable but 4 days old as of pin-time — picked v1.34 so every
  # Helm chart we'll touch has had months to verify compatibility.
  # Researched 2026-05-01 via GitHub API: github.com/k3s-io/k3s/releases.
  default = "v1.34.7-k3s1"
}

variable "servers" {
  description = "Number of K3s server (control-plane) nodes. Homelab usually = 1."
  type        = number
  default     = 1

  validation {
    condition     = var.servers >= 1
    error_message = "Must have at least 1 server (control-plane) node."
  }
}

variable "agents" {
  description = "Number of K3s agent (worker) nodes."
  type        = number
  default     = 4
}

variable "registry_port" {
  description = "Host port for the in-cluster local Docker registry (built into k3d). Matches the registry_port used by another cluster this chart can also target, so a single localhost:5001 reference works for whichever one is currently running — they're never both up at once."
  type        = number
  default     = 5001
}

variable "data_path" {
  description = "Host path bind-mounted into all k3d nodes. local-path provisioner stores PVC data here, so PVCs survive cluster recreation."
  type        = string

  # Reject relative paths and unexpanded tildes (e.g. "~/data" — Terraform doesn't expand ~).
  validation {
    condition     = startswith(var.data_path, "/")
    error_message = "data_path must be an absolute path starting with /. Tilde (~) is NOT expanded by Terraform."
  }
}

variable "kubeconfig_path" {
  description = "Absolute path on the host where the kubeconfig file is written. Downstream Terragrunt units read this via `dependency.k3d.outputs.kubeconfig_path`."
  type        = string

  validation {
    condition     = startswith(var.kubeconfig_path, "/")
    error_message = "kubeconfig_path must be an absolute path starting with /."
  }
}
