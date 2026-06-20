# =============================================================================
# k3d module — outputs
# =============================================================================
#
# Outputs are the module's PUBLIC API. Downstream Terragrunt units consume
# them via `dependency` blocks:
#
#   dependency "k3d" {
#     config_path = "../../00-bootstrap/k3d"
#   }
#
#   inputs = {
#     kubeconfig_path = dependency.k3d.outputs.kubeconfig_path
#   }
#
# So: anything a downstream unit might reasonably need from us, expose here.
# =============================================================================

output "cluster_name" {
  description = "Name of the k3d cluster (e.g. for kubectl context names, log labels)."
  value       = var.cluster_name
}

output "kubeconfig_path" {
  description = "Absolute path to the kubeconfig file on the host. Used by downstream units' kubernetes/helm providers."
  value       = var.kubeconfig_path
}

output "kubeconfig_context" {
  description = "kubectl context name for this cluster (auto-merged into ~/.kube/config). Format: k3d-<cluster_name>."
  value       = "k3d-${var.cluster_name}"
}

output "kubeconfig_content" {
  description = "Raw content of the kubeconfig file (sensitive). Useful when a downstream unit needs the kubeconfig content rather than a path (e.g., embedding into a Secret)."
  value       = data.local_file.kubeconfig.content
  sensitive   = true # marks output as sensitive — TG/TF won't print it in plan output or logs
}

output "registry_host" {
  description = "Container registry endpoint reachable from the host. Use for `docker push localhost:5000/myimage:tag`."
  value       = "localhost:${var.registry_port}"
}

output "registry_in_cluster" {
  description = "Container registry endpoint reachable from inside the cluster (for image references in K8s manifests). Format: <cluster-name>-registry:5000."
  value       = "${var.cluster_name}-registry:5000"
}
