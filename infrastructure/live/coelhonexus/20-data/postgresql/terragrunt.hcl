# =============================================================================
# Leaf — postgresql (coelhonexus standalone, 20-data layer)
# =============================================================================
# Bitnami PostgreSQL 18 standalone. Consumed downstream by:
#   - langfuse (primary DB)
#   - app conversation history (services/youtube/conversation_history table)
#   - app RR stores (radar_scans, radar_profiles, etc.)
#   - app AsyncPostgresSaver (LangGraph checkpointing for planner + synth)
#
# Backup CronJob: nightly 02:00 UTC → MinIO `backups` bucket / `postgres/` prefix.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency
#   - External exposure disabled (module supports the toggle)
#   - admin_password from env.hcl `demo` map (not SOPS)
#   - minio creds from dependency.minio.outputs.s3_config (verbatim)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/postgresql"
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
    s3_config = {
      endpoint   = "http://minio.minio.svc.cluster.local:9000"
      access_key = "mock"
      secret_key = "mock"
      region     = "us-east-1"
    }
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Ordering-only — postgresql ships a ServiceMonitor.
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
  # Admin credentials from env.hcl `demo` map.
  admin_password = include.root.locals.env.demo.postgres_password

  # MinIO backup endpoint flows through the dependency block.
  minio_endpoint   = dependency.minio.outputs.s3_config.endpoint
  minio_access_key = dependency.minio.outputs.s3_config.access_key
  minio_secret_key = dependency.minio.outputs.s3_config.secret_key

  # External exposure OFF — module supports the toggle.
  enable_tailscale_exposure = false

  # Defaults from variables.tf are appropriate:
  #   chart 18.6.2, standalone, 10Gi PVC, 50m/200Mi/384Mi resources,
  #   max_connections=100, shared_buffers=128MB, ServiceMonitor enabled,
  #   backup CronJob daily 02:00, retention=30.
}
