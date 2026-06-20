# =============================================================================
# Leaf — rancher (coelhonexus standalone, 10-platform layer)
# =============================================================================
# Rancher cluster UI — view pods/services/events/logs across the standalone
# cluster in real time.
#
# Module is byte-identical to COELHO Cloud's; only the leaf differs.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP dependency "tailscale_operator" (no Tailscale on standalone)
#   - DUMMY tailscale_* inputs (the Ingress resource is created inert)
#   - bootstrap_password from env.hcl `demo` map (forced reset on first login)
#
# After apply + port-forward (host port 23010 → 80):
#   open http://localhost:23010
#   login as `admin` with password from env.hcl demo.rancher_bootstrap_password
#   (Rancher requires immediate password change — pick anything)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/rancher"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only dependencies for `terragrunt run --all apply`:
#   - monitoring-crds: Rancher's chart creates ServiceMonitors (prometheus
#     metrics enabled by default); the CRDs must exist first.
#   - cert-manager: REQUIRED on the standalone cluster because we set
#     `tls_source = "rancher"` below — Rancher's chart asks cert-manager to
#     issue a self-signed TLS cert. Without cert-manager's `Issuer` CRD,
#     helm_release fails with "no matches for kind Issuer in version
#     cert-manager.io/v1". See infrastructure/modules/cert-manager/main.tf
#     for why this divergence from COELHO Cloud is correct (COELHO Cloud uses
#     Tailscale operator instead).
dependencies {
  paths = [
    "../monitoring-crds",
    "../cert-manager",
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
  bootstrap_password = include.root.locals.env.demo.rancher_bootstrap_password

  # Tailscale — DUMMY (no tailscale-operator on standalone).
  tailscale_hostname      = "rancher"
  tailscale_domain        = "tailscale.local"
  tailscale_ingress_class = "tailscale"

  # 2026-06-19: standalone needs Rancher to terminate its own TLS (no Tailscale
  # operator in front) AND advertise itself as `localhost` so port-forwarded
  # access works without DNS gymnastics. Without these two, Rancher serves
  # HTTP only on port 443 (TLS handshake fails) and redirects post-login to
  # `https://rancher.tailscale.local/` (no DNS → connection refused).
  tls_source        = "rancher"
  hostname_override = "localhost"

  # 2026-06-19: OVERRIDE the module's `rancher_features` default to EMPTY.
  #
  # The module default `continuous-delivery=false,rke2=false,provisioningv2=false`
  # is a MEMORY OPTIMIZATION the COELHO Cloud user applied 23 DAYS AFTER the
  # initial Rancher install — by then every CRD the disabled controllers would
  # otherwise create was already installed and persisted in etcd. Flipping the
  # flags later disabled the controllers but the CRDs stayed.
  #
  # On a FRESH install with ANY `features=X=false` set, Rancher v2.14.1 still
  # registers informers for the CRDs that controller X would install — but
  # controller X never runs, so the CRDs never get created. The informer's
  # GVR resolution falls back to `meta.k8s.io` → 404 → cache-sync deadlock →
  # port 80 never opens → 10-min startup probe kills the pod every cycle.
  #
  # Confirmed on this cluster (2026-06-19): `provisioningv2=false` deadlocked
  # the `*v1.Cluster` watch; removing it surfaced `rke2=false` deadlocking
  # `*v1.ETCDSnapshot`. Every flag triggers a new instance of the same bug.
  #
  # Solution: install Rancher with the chart defaults (all features ON, all
  # CRDs installed during boot), then AFTER it's healthy apply the same
  # disabling intent via on-cluster `kind: Feature` CRs (the
  # optimization doc's Tier 1 step #5 path). At that point the CRDs already
  # exist, so controllers can be safely turned off without breaking watches.
  rancher_features = ""

  # Defaults from variables.tf are appropriate:
  #   chart 2.14.1, 1 replica, 100m/512Mi/1.5Gi resources, audit log off.
}
