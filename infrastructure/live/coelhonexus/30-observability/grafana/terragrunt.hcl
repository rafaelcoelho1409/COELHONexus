# =============================================================================
# Leaf — grafana (coelhonexus standalone, 30-observability layer)
# =============================================================================
# UI for the LGTM stack. Datasources arrive via sidecar (it watches for
# ConfigMaps labeled `grafana_datasource: 1` cluster-wide — already created
# by loki + tempo + mimir).
#
# Backend DB: external Postgres (no bundled sqlite). Bootstrap Job creates a
# dedicated `grafana` role + DB on first apply.
#
# Admin login (localhost port-forward contract):
#   admin / admin
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency
#   - External Ingress REMOVED from main.tf (2026-07-02) — always inert on
#     this cluster. Browser access is via localhost port-forward / NodePort.
#     The external-domain input is kept: main.tf's grafana_root_url/grafana_domain
#     locals still reference it as a fallback (unused in practice since
#     root_url is explicitly set below).
#   - postgresql connection from dependency (verbatim — admin user is "postgres",
#     admin password matches env.hcl demo.postgres_password)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//grafana` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into grafana/ —
  # needed because main.tf's `module "k3d_expose"` references a SIBLING
  # module via a relative path (same fix as every other local-expose-enabled
  # leaf).
  source = "${get_repo_root()}/infrastructure/modules//grafana"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Backend DB + bootstrap Job target.
dependency "postgresql" {
  config_path = "../../20-data/postgresql"

  mock_outputs = {
    admin_user     = "postgres"
    admin_password = "mock"
    host           = "postgresql.postgresql.svc.cluster.local"
    port           = 5432
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependencies {
  # monitoring-crds: grafana's ServiceMonitor needs the CRDs.
  # loki/tempo/mimir: each creates a `grafana_datasource: 1` ConfigMap that
  # grafana's sidecar imports at startup. Without these ordering deps,
  # `terragrunt run-all apply` can launch grafana before the ConfigMaps exist,
  # and the smoke's datasource check ("Loki/Tempo/Mimir present") races.
  paths = [
    "../../10-platform/monitoring-crds",
    "../../30-observability/loki",
    "../../30-observability/tempo",
    "../../30-observability/mimir",
  ]
}

generate "providers" {
  path      = "providers.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "kubernetes" {
      config_path = "${dependency.k3d.outputs.kubeconfig_path}"
    }
    provider "helm" {
      kubernetes = {
        config_path = "${dependency.k3d.outputs.kubeconfig_path}"
      }
    }
  EOF
}

inputs = {
  # External domain — DUMMY, unused fallback in main.tf locals (root_url below
  # always wins). No operator in this cluster.
  tailscale_domain = "tailscale.local"
  root_url         = "http://localhost:23005/"
  admin_user              = include.root.locals.env.demo.grafana_admin_user
  admin_password          = include.root.locals.env.demo.grafana_admin_password

  # Postgres backend (from the postgresql leaf's outputs).
  postgres_admin_user     = dependency.postgresql.outputs.admin_user
  postgres_admin_password = dependency.postgresql.outputs.admin_password
  postgres_host           = dependency.postgresql.outputs.host
  postgres_port           = dependency.postgresql.outputs.port

  # Defaults from variables.tf are appropriate:
  #   chart 12.3.0 (grafana-community), 100m/256Mi/512Mi resources,
  #   persistence disabled (DB external), ServiceMonitor on,
  #   sidecar searches ALL namespaces for grafana_datasource/grafana_dashboard.

  # Local access (k3d only) — NodePort 30486->23022, mapped via
  # `k3d cluster edit coelhonexus --port-add "23022:30486@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # Second path alongside the existing 23005 port-forward.
  enable_local_expose = true
  k3d_node_port       = 30486
}
