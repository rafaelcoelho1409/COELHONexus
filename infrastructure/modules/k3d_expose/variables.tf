# =============================================================================
# k3d_expose module — inputs
# =============================================================================

variable "namespace" {
  description = "Kubernetes namespace the target Service/pods live in."
  type        = string
}

variable "service_name" {
  description = "Base name for the new NodePort Service (suffixed with `-local`). Usually the same as the target module's `release_name`."
  type        = string
}

variable "pod_selector" {
  description = "Label selector matching the SAME pods as the target's existing ClusterIP Service. Copy verbatim from `kubectl get svc <name> -n <namespace> -o yaml` — a wrong selector fails safe (zero endpoints, no traffic) rather than routing anywhere unexpected."
  type        = map(string)
}

variable "ports" {
  description = "Ports to expose, one entry per protocol/UI the target actually needs reachable from outside the cluster (e.g. Neo4j needs BOTH its HTTP Browser UI and its Bolt driver port — exposing only one loads the page but breaks the login step). Each `node_port` must be unique across the WHOLE cluster, not just this namespace — Kubernetes rejects a duplicate outright at apply time."
  type = list(object({
    name        = string
    target_port = number
    node_port   = number
  }))

  validation {
    condition     = alltrue([for p in var.ports : p.node_port >= 30000 && p.node_port <= 32767])
    error_message = "Every node_port must be in the Kubernetes NodePort range 30000-32767."
  }
}
