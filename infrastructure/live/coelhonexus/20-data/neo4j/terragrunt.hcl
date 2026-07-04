# =============================================================================
# Leaf — neo4j (coelhonexus standalone, 20-data layer)
# =============================================================================
# Neo4j Community Edition (single node) + APOC plugins. Knowledge graph for
# YCS (Video/Channel/Entity/Relationship) and RR (Paper/Author/Concept/Source).
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency
#   - External Ingresses (browser + Bolt) REMOVED from main.tf (2026-07-02) —
#     always inert on this cluster (no Ingress controller). Real access is via
#     the k3d_expose NodePort module below. The external-domain input is kept: Neo4j's
#     own browser_advertised_address/bolt_advertised_address Helm values still
#     reference it (unrelated to the Ingress resources that got removed).
#   - neo4j_password from env.hcl `demo` map (not SOPS)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//neo4j` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into neo4j/ — needed
  # because main.tf's `module "k3d_expose"` references a SIBLING module via
  # a relative path, which breaks if only neo4j/ itself gets copied.
  source = "${get_repo_root()}/infrastructure/modules//neo4j"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Backup CronJob target.
dependency "minio" {
  config_path = "../minio"

  mock_outputs = {
    api_endpoint = "http://minio.minio.svc.cluster.local:9000"
    access_key   = "mock"
    secret_key   = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only.
dependencies {
  paths = ["../../10-platform/monitoring-crds"]
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
  # External domain — DUMMY, feeds Neo4j's own advertised_address Helm
  # values only (the Ingress resources that used to reference it were
  # removed; see header comment above).
  tailscale_domain = "tailscale.local"

  # Auth from env.hcl `demo` map (built-in `neo4j` user).
  neo4j_password = include.root.locals.env.demo.neo4j_password

  # MinIO backup endpoint.
  minio_endpoint   = dependency.minio.outputs.api_endpoint
  minio_access_key = dependency.minio.outputs.access_key
  minio_secret_key = dependency.minio.outputs.secret_key

  # Defaults from variables.tf are appropriate:
  #   chart 2026.3.1 (Neo4j 2026.3 / Community), 5Gi PVC,
  #   500m-1000m CPU, 2Gi memory (chart minimums),
  #   APOC plugins enabled, Bolt TLS self-signed,
  #   backups every 6h.

  # Local access (k3d only) — HTTP 30474->23001, Bolt 30475->23012, mapped via
  # `k3d cluster edit coelhonexus --port-add "23001:30474@loadbalancer"`
  # `k3d cluster edit coelhonexus --port-add "23012:30475@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # Both required: Neo4j Browser's JS opens a separate Bolt connection for
  # login, distinct from the HTTP port that serves the page itself.
  enable_local_expose = true
  k3d_http_node_port  = 30474
  k3d_bolt_node_port  = 30475
}
