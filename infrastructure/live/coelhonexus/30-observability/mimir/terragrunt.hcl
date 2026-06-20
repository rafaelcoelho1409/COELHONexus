# =============================================================================
# Leaf — mimir (coelhonexus standalone, 30-observability layer)
# =============================================================================
# Mimir distributed-at-replicas=1: ingester + store-gateway + compactor +
# distributor + querier + query-frontend. Backend = local MinIO (3 buckets:
# mimir-blocks, mimir-ruler, mimir-alertmanager).
#
# Datasource auto-wiring: creates ConfigMap labeled `grafana_datasource: 1`.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP `../grafana` from dependencies.paths (we apply mimir BEFORE grafana)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/mimir"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "minio" {
  config_path = "../../20-data/minio"

  mock_outputs = {
    api_endpoint = "http://minio.minio.svc.cluster.local:9000"
    access_key   = "mock"
    secret_key   = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

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
  minio_endpoint   = dependency.minio.outputs.api_endpoint
  minio_access_key = dependency.minio.outputs.access_key
  minio_secret_key = dependency.minio.outputs.secret_key

  # Defaults from variables.tf are appropriate:
  #   chart 6.0.6, 30d retention, 3 buckets, ingester 100m/512Mi/1Gi,
  #   12Gi total PVC, metaMonitoring on.
}
