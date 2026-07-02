# =============================================================================
# Leaf — qdrant (coelhonexus standalone, 20-data layer)
# =============================================================================
# Vector store for YCS embeddings + RR paper deduplication. Self-contained
# (no DB deps at runtime); MinIO used only by the snapshot CronJob.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP dependency "tailscale_operator"
#   - DUMMY tailscale strings (Ingress unconditional in main.tf — inert)
#   - api_key from env.hcl `demo` map (deterministic demo credential;
#     same value is injected into the app layer as `QDRANT_API_KEY`)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//qdrant` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into qdrant/ —
  # needed because main.tf's `module "k3d_expose"` references a SIBLING
  # module via a relative path (same fix as neo4j's leaf).
  source = "${get_repo_root()}/infrastructure/modules//qdrant"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Snapshot backup CronJob target.
dependency "minio" {
  config_path = "../minio"

  mock_outputs = {
    api_endpoint = "http://minio.minio.svc.cluster.local:9000"
    access_key   = "mock"
    secret_key   = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only — chart creates a ServiceMonitor.
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
  # Tailscale — DUMMY (Ingress is unconditional in main.tf; created inert).
  tailscale_domain        = "tailscale.local"
  tailscale_ingress_class = "tailscale"

  # Deterministic demo API key. Qdrant and the app layer both use this value.
  api_key = include.root.locals.env.demo.qdrant_api_key

  # MinIO snapshot endpoint.
  minio_endpoint   = dependency.minio.outputs.api_endpoint
  minio_access_key = dependency.minio.outputs.access_key
  minio_secret_key = dependency.minio.outputs.secret_key

  # Defaults from variables.tf are appropriate:
  #   chart 1.17.1, single replica, 5Gi data + 5Gi snapshot PVCs,
  #   10m/200Mi/512Mi resources, ServiceMonitor on, backups every 6h.

  # Local access (k3d only) — REST/Dashboard 30476->23011, mapped via
  # `k3d cluster edit coelhonexus --port-add "23011:30476@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  enable_local_expose = true
  k3d_http_node_port  = 30476
}
