# =============================================================================
# Leaf — argocd (coelhonexus standalone, 10-platform layer)
# =============================================================================
# Bundles ArgoCD + Image Updater.
#
# NOTE (2026-06-19): On the standalone coelhonexus cluster, `skaffold run` is
# the canonical app-deploy path (cross-platform, single tool — see skaffold.yaml).
# ArgoCD here exists as a "production GitOps demo" mirror of COELHO Cloud's
# setup; without an in-cluster GitLab it doesn't actually sync apps. Image
# Updater is left configured for parity but never fires on this cluster.
#
# Layer note: lives in 10-platform but applies AFTER 20-data/redis because
# ArgoCD uses the central Redis (logical DB index 2). Phase ordering in
# standalone-up.sh respects this.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency (no operator in this cluster)
#   - DROP dependencies.paths for "40-apps/gitlab" (no GitLab here)
#   - External Ingress REMOVED from main.tf (2026-07-02) — always inert on
#     this cluster. Real access via port-forward/NodePort. The hostname
#     + domain inputs are kept: the Helm chart's server.domain value still
#     references them.
#   - gitlab_url = "" + gitlab_token = "" → repo-creds Secret is conditional
#     in main.tf (count = url!=""&&token!=""?1:0); not created here.
#   - k3d_registry_endpoint overridden to coelhonexus-registry:5000
#   - Redis password from env.hcl `demo` map (still flows via dependency)
#   - admin_password from env.hcl `demo` map → post-install sync Job forces
#     a deterministic password (chart default is a random one-time secret)
#
# Admin login (localhost port-forward or NodePort — see main.tf):
#   admin / admin  (env.hcl demo.argocd_admin_password)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//argocd` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into argocd/ —
  # needed because main.tf's `module "k3d_expose"` references a SIBLING
  # module via a relative path (same fix as every other local-expose-enabled
  # leaf).
  source = "${get_repo_root()}/infrastructure/modules//argocd"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Central Redis (logical DB 2). v2-baseline pattern — no bundled Redis.
dependency "redis" {
  config_path = "../../20-data/redis"

  mock_outputs = {
    host     = "redis-master.redis.svc.cluster.local"
    port     = 6379
    password = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only — ArgoCD components ship ServiceMonitors.
dependencies {
  paths = ["../monitoring-crds"]
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
  # ---------------------------------------------------------------------------
  # External hostname/domain — DUMMY, still referenced by the Helm chart's server.domain value.
  # ---------------------------------------------------------------------------
  tailscale_hostname = "argocd"
  tailscale_domain   = "tailscale.local"

  # ---------------------------------------------------------------------------
  # GitLab — empty (no GitLab in this cluster).
  # `count = var.gitlab_url != "" && var.gitlab_token != "" ? 1 : 0` skips
  # the repo-creds Secret. Image Updater also won't poll a GitLab registry.
  # ---------------------------------------------------------------------------
  gitlab_url               = ""
  gitlab_token             = ""
  gitlab_registry_endpoint = ""

  # ---------------------------------------------------------------------------
  # Central Redis (from the redis leaf's outputs).
  # ---------------------------------------------------------------------------
  redis_host     = dependency.redis.outputs.host
  redis_port     = dependency.redis.outputs.port
  redis_password = dependency.redis.outputs.password
  # redis_db defaults to 2 (argocd) — fine.

  # ---------------------------------------------------------------------------
  # Image Updater — configured for parity with COELHO Cloud. On standalone,
  # `skaffold run` does the build+deploy directly (rewrites image refs with
  # unique sha256 tags), so Image Updater never has anything to detect here.
  # ---------------------------------------------------------------------------
  enable_image_updater         = true
  image_updater_check_interval = "2m"
  k3d_registry_endpoint        = "coelhonexus-registry:5000"

  # Defaults from variables.tf are appropriate:
  #   chart 9.4.17, image-updater 1.1.4, notifications + dex off,
  #   applicationset on, ServiceMonitors on, controller 512Mi req / 1Gi limit
  #   (the OOM fix that triggered this whole observability work).

  # Local access (k3d only) — NodePort 30487->23023, mapped via
  # `k3d cluster edit coelhonexus --port-add "23023:30487@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # Second path alongside the existing 23007 port-forward; both plain HTTP.
  enable_local_expose = true
  k3d_node_port       = 30487

  # Deterministic admin password (post-install sync Job — see main.tf).
  # Login: admin / <this value>. Same demo-credentials convention as every
  # other service (grafana_admin_password, rancher_bootstrap_password, etc).
  admin_password = include.root.locals.env.demo.argocd_admin_password
}
