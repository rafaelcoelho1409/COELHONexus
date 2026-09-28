# Leaf — tempo (coelhonexus standalone, 30-observability layer)
# Single-binary trace storage. Backend = local MinIO (tempo-traces bucket).
# Grafana datasource ConfigMap created here; sidecar imports on startup.
#
# Adaptations vs COELHO Cloud's leaf:
#   - memory_request/memory_limit lifted 256Mi/1Gi -> 512Mi/2Gi (local-only
#     override below; 1Gi default OOMKilled on multi-trace Explore here).
#   - Ported 2026-09-28 from COELHO Cloud's 2026-09-23 incident fixes
#     (same DD ingestion workload triggers the same burst pattern here):
#     loosened livenessProbe/readinessProbe in helm/values.yaml.tpl, and
#     streamingEnabled: false in k8s/datasource.yaml.tpl (gRPC-over-h2c
#     streaming incompatible with this chart's plain-HTTP Tempo).

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/tempo"
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
  paths = [
    "../../10-platform/monitoring-crds",
    "../loki",
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
  minio_endpoint   = dependency.minio.outputs.api_endpoint
  minio_access_key = dependency.minio.outputs.access_key
  minio_secret_key = dependency.minio.outputs.secret_key

  # 1Gi default OOMKilled on multi-trace Explore; lifted for local k3d only.
  memory_request = "512Mi"
  memory_limit   = "2Gi"
}
