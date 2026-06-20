# =============================================================================
# Leaf — neo4j (coelhonexus standalone, 20-data layer)
# =============================================================================
# Neo4j Community Edition (single node) + APOC plugins. Knowledge graph for
# YCS (Video/Channel/Entity/Relationship) and RR (Paper/Author/Concept/Source).
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP dependency "tailscale_operator"
#   - DUMMY tailscale strings — module has 2 unconditional Ingresses (browser
#     + Bolt) in main.tf. Both inert without an Ingress controller. In-cluster
#     access via `bolt://neo4j.neo4j.svc.cluster.local:7687` works fine
#     (single-node CE, no routing).
#   - neo4j_password from env.hcl `demo` map (not SOPS)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/neo4j"
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
  # Tailscale — DUMMY. Browser + Bolt Ingresses are unconditional in main.tf.
  tailscale_domain        = "tailscale.local"
  tailscale_ingress_class = "tailscale"

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
}
