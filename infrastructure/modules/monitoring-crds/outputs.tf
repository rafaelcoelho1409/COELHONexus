# =============================================================================
# monitoring-crds module — outputs
# =============================================================================
#
# Downstream units don't typically NEED outputs from this module — they
# consume the CRDs by declaring `kind: ServiceMonitor` etc. directly. But
# exposing these outputs lets dependent units use:
#
#   dependencies {
#     paths = ["../monitoring-crds"]
#   }
#
# (the plural `dependencies` block, no outputs needed — pure ordering)
#
# OR a downstream unit can reference `dependency.crds.outputs.ready` to force
# Tofu to wait until the CRDs are up.
# =============================================================================

output "namespace" {
  description = "Namespace where the Helm release metadata is recorded. CRDs themselves are cluster-scoped."
  value       = helm_release.crds.namespace
}

output "chart_version" {
  description = "The Helm chart version installed (matches input var.chart_version)."
  value       = helm_release.crds.version
}

output "app_version" {
  description = "The prometheus-operator app version corresponding to chart_version (e.g., v0.90.1 for chart 28.0.1)."
  # NOTE: helm provider v3.x changed `metadata` from a list to a single object.
  # v2.x syntax was metadata[0].app_version; v3.x is metadata.app_version.
  value = helm_release.crds.metadata.app_version
}

output "ready" {
  description = "Helm release status string ('deployed' on success). Useful as a marker for `dependency` blocks downstream."
  value       = helm_release.crds.status
}
