# K3D Cluster Configuration
variable "cluster_name" {
  description = "Name of the K3D cluster"
  type        = string
  default     = "coelhonexus"
}

variable "k3s_version" {
  description = "K3s version to use"
  type        = string
  default     = "v1.28.5-k3s1"
}

variable "servers" {
  description = "Number of server nodes"
  type        = number
  default     = 1
}

variable "agents" {
  description = "Number of agent nodes"
  type        = number
  default     = 1
}

variable "registry_port" {
  description = "Port for the K3D registry"
  type        = number
  default     = 5002
}

# Service Installation Flags

variable "install_rancher" {
  description = "Whether to install Rancher"
  type        = bool
  default     = true
}

# Rancher Configuration
variable "rancher_http_host_port" {
  description = "Host port for Rancher HTTP"
  type        = number
  default     = 7081
}

variable "rancher_http_node_port" {
  description = "NodePort for Rancher HTTP service"
  type        = number
  default     = 30081
}

variable "rancher_https_host_port" {
  description = "Host port for Rancher HTTPS"
  type        = number
  default     = 7444
}

variable "rancher_https_node_port" {
  description = "NodePort for Rancher HTTPS service"
  type        = number
  default     = 30444
}

variable "rancher_bootstrap_password" {
  description = "Bootstrap password for Rancher (change on first login)"
  type        = string
  default     = "admin"
  sensitive   = true
}

# Storage Configuration
# Volume mounts are computed dynamically in main.tf using project_root
variable "volume_mounts" {
  description = "Host volumes to mount into the cluster for persistent storage (computed from project root)"
  type = list(object({
    host_path      = string
    container_path = string
    node_filter    = string
  }))
  default = null  # Will be computed in main.tf if not provided
}
