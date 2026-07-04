# =============================================================================
# Leaf — rancher (coelhonexus standalone, 10-platform layer)
# =============================================================================
# Rancher cluster UI — view pods/services/events/logs across the standalone
# cluster in real time.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency (no external ingress operator on standalone)
#   - External Ingress REMOVED from main.tf (2026-07-02) — always inert on
#     this cluster. The hostname/domain inputs kept: main.tf's
#     hostname_override fallback still references them (hostname_override
#     below always wins in practice).
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
  # `//rancher` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into rancher/ —
  # needed because main.tf's `module "k3d_expose"` references a SIBLING
  # module via a relative path (same fix as every other local-expose-enabled
  # leaf).
  source = "${get_repo_root()}/infrastructure/modules//rancher"
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
#     an external ingress operator instead).
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

  # External hostname/domain — DUMMY, unused fallback (hostname_override below always wins).
  tailscale_hostname = "rancher"
  tailscale_domain   = "tailscale.local"

  # 2026-06-19: standalone needs Rancher to terminate its own TLS (no external
  # ingress operator in front) AND advertise itself as `localhost` so port-forwarded
  # access works without DNS gymnastics. Without these two, Rancher serves
  # HTTP only on port 443 (TLS handshake fails) and redirects post-login to
  # a broken `<hostname>.<dummy-domain>` URL (no DNS → connection refused).
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

  # Local access (k3d only) — NodePort 30485->23021, mapped via
  # `k3d cluster edit coelhonexus --port-add "23021:30485@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # Second path alongside the existing 23010 port-forward; same self-signed
  # cert either way.
  enable_local_expose = true
  k3d_https_node_port = 30485
}
