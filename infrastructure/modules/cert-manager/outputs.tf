# =============================================================================
# cert-manager module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where cert-manager runs. Downstream leaves (e.g., rancher) use this as an ordering hint via Terragrunt's dependency block."
  value       = helm_release.cert_manager.namespace
}

output "release_name" {
  description = "Helm release name. Useful for `helm get values <release>` debugging."
  value       = helm_release.cert_manager.name
}
