# =============================================================================
# Leaf — tempo (coelhonexus standalone, 30-observability layer)
# =============================================================================
# Tempo single-binary trace storage. Backend = local MinIO (`tempo-traces`
# bucket, created by bootstrap Job).
#
# Datasource auto-wiring: creates ConfigMap labeled `grafana_datasource: 1`.
# Grafana's sidecar imports it on startup. Tempo's datasource includes
# tracesToLogs (→ loki) and tracesToMetrics (→ mimir) cross-links — those
# work once mimir + grafana also exist (modules #12, #14).
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP `../grafana` + `../mimir` from dependencies.paths — they apply
#     AFTER tempo in our order. Grafana's sidecar picks up ConfigMaps
#     regardless of which one boots first.
# =============================================================================

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

# Ordering-only.
dependencies {
  paths = [
    "../../10-platform/monitoring-crds",
    "../loki",   # tracesToLogs cross-link uses Loki UID — keep ordering for tidiness
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

  # 2026-06-23: local k3d traces proved the 1Gi default too tight for
  # multi-trace Explore / LangFuse investigation; the pod was OOMKilled.
  # Lift only the local coelhonexus leaf so COELHO Cloud keeps its own sizing.
  memory_request = "512Mi"
  memory_limit   = "2Gi"

  # Remaining defaults from variables.tf stay appropriate:
  #   chart 2.1.0, single-binary, OTLP-only receivers, 5Gi PVC, 30-day retention.
}
