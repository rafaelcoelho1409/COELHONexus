# =============================================================================
# Leaf — cert-manager (coelhonexus standalone, 10-platform layer)
# =============================================================================
# TLS certificate provisioner for the standalone cluster. Required by Rancher
# when `tls_source = "rancher"` (the only viable Rancher TLS option here, since
# we dropped the external ingress operator and have no external TLS-terminating proxy).
#
# Order: applied AFTER monitoring-crds (k3d kubeconfig must exist), BEFORE
# rancher (Rancher's chart creates an `Issuer` resource on first apply, which
# requires cert-manager's CRDs to already exist).
#
# Not present on COELHO Cloud — that cluster uses an external-ingress-operator-provided
# TLS termination via an external proxy. See infrastructure/modules/cert-manager/
# main.tf for the full rationale.
# =============================================================================

include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/cert-manager"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

generate "providers" {
  path      = "providers.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "helm" {
      kubernetes = {
        config_path = "${dependency.k3d.outputs.kubeconfig_path}"
      }
    }
  EOF
}

inputs = {
  # Defaults from variables.tf are appropriate:
  #   chart v1.15.3, namespace cert-manager, 300s helm timeout, CRDs managed
  #   by chart with crds.keep=true (survives helm uninstall).
}
