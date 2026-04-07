variable "cluster_name" {
  description = "Name of the K3D cluster"
  type        = string
}

variable "k3s_version" {
  description = "K3s version to use"
  type        = string
}

variable "servers" {
  description = "Number of server nodes"
  type        = number
}

variable "agents" {
  description = "Number of agent nodes"
  type        = number
}

variable "registry_port" {
  description = "Port for the K3D registry"
  type        = number
}

variable "volume_mounts" {
  description = "List of volume mounts for persistent data"
  type = list(object({
    host_path      = string
    container_path = string
    node_filter    = string
  }))
  default = []
}
