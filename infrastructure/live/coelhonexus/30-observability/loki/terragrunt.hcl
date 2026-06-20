# =============================================================================
# Leaf — loki (coelhonexus standalone, 30-observability layer)
# =============================================================================
# Loki monolithic SingleBinary mode. Backend = local MinIO (2 buckets:
# loki-chunks, loki-ruler, created idempotently by a bootstrap Job).
#
# Datasource auto-wiring: this module creates a ConfigMap labeled
# `grafana_datasource: 1`; Grafana's sidecar (added in module #14) imports
# it on startup. UID `loki` is referenced by every dashboard.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP "../grafana" from dependencies.paths — in COELHO Cloud's order
#     grafana applied first, here loki applies first. The ConfigMap is
#     picked up by the sidecar regardless of which one boots first.
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/loki"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# MinIO — S3 endpoint + creds.
dependency "minio" {
  config_path = "../../20-data/minio"

  mock_outputs = {
    api_endpoint = "http://minio.minio.svc.cluster.local:9000"
    access_key   = "mock"
    secret_key   = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only — ServiceMonitor needs the CRDs.
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
  #   chart 13.5.0, Monolithic SingleBinary, schema v13 + TSDB,
  #   5Gi PVC, 100m/256Mi/512Mi resources, 30-day retention.
}
