variable "namespace" {
  description = "Kubernetes namespace for Rancher"
  type        = string
  default     = "cattle-system"
}

variable "release_name" {
  description = "Helm release name"
  type        = string
  default     = "rancher"
}

variable "chart_version" {
  description = "Rancher Helm chart version"
  type        = string
  default     = ""
}

variable "http_node_port" {
  description = "NodePort for Rancher HTTP service"
  type        = number
  default     = 30081
}

variable "https_node_port" {
  description = "NodePort for Rancher HTTPS service"
  type        = number
  default     = 30444
}

variable "bootstrap_password" {
  description = "Bootstrap password for Rancher"
  type        = string
  default     = "admin"
  sensitive   = true
}

variable "rancher_image_tag" {
  description = "Rancher image tag"
  type        = string
  default     = "v2.13.2"
}

variable "cluster_name" {
  description = "Name of the K3D cluster (used for dependency)"
  type        = string
}

variable "values_file" {
  description = "Path to Helm values YAML file (optional, will use inline values if not provided)"
  type        = string
  default     = ""
}

variable "cluster_ready" {
  description = "Signal that the cluster API is ready (dependency)"
  type        = string
  default     = ""
}
