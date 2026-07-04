# =============================================================================
# Leaf — redis (coelhonexus standalone, 20-data layer)
# =============================================================================
# Bitnami Redis with redis-stack-server image (RediSearch + RedisJSON +
# RedisTimeSeries + RedisBloom modules). Consumed downstream by:
#   - argocd (logical DB 2 — caching, OIDC sessions, manifest cache)
#   - celery (logical DB 0 — task broker + result backend)
#   - langfuse (logical DB 3 — queue + cache)
#   - RR runtime (pub/sub for SSE phase events; fs mirror; LLM counters)
#
# Backup CronJob: nightly 02:15 UTC → MinIO `backups` bucket, retention 30.
#
# Adaptations vs COELHO Cloud's leaf (otherwise verbatim):
#   - Password from env.hcl `demo` map (not SOPS)
#   - External exposure stays off (module default + we have no
#     external ingress operator anyway)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/redis"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

# Backup CronJob writes RDB snapshots to MinIO.
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

# Ordering-only dep — Redis module creates a ServiceMonitor.
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
  # Auth from env.hcl `demo` map.
  redis_password = include.root.locals.env.demo.redis_password

  # MinIO backup endpoint flows through the dependency block.
  minio_endpoint   = dependency.minio.outputs.s3_config.endpoint
  minio_access_key = dependency.minio.outputs.s3_config.access_key
  minio_secret_key = dependency.minio.outputs.s3_config.secret_key

  # External exposure OFF — module supports the toggle natively.
  enable_tailscale_exposure = false

  # Defaults from variables.tf are appropriate:
  #   chart 25.4.1, redis-stack-server 7.4.0-v8, standalone, 5Gi PVC,
  #   maxmemory=256mb, 25m/96Mi/448Mi resources, ServiceMonitor enabled,
  #   backup CronJob daily 02:15, retention=30, bucket=backups.
}
