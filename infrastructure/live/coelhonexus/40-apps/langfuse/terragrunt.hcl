# =============================================================================
# Leaf — langfuse (coelhonexus standalone, 40-apps layer)
# =============================================================================
# Langfuse 3 — LLM observability for FastAPI + Celery + DeepAgents/RR.
# Bundled ClickHouse (chart-managed) + external Postgres/Redis/MinIO.
#
# OTLP endpoint after this leaf is green:
#   http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel
#   (already what apps/fastapi/infra/otel/exporters.py points at)
#
# Initial login (post-apply):
#   email:    admin@demo.local
#   password: admin-demo-password
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency
#   - External Ingress REMOVED from main.tf (2026-07-02) — always inert on
#     this cluster. Browser access is via localhost port-forward/NodePort.
#     The external-domain input is kept: main.tf's public_url fallback still references it.
#   - All SOPS secrets → env.hcl `demo` map
#   - enable_otel_ingestion = true (matches COELHO Cloud for the OTLP path)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//langfuse` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into langfuse/ —
  # needed because main.tf's `module "k3d_expose"` references a SIBLING
  # module via a relative path (same fix as the 20-data leaves).
  source = "${get_repo_root()}/infrastructure/modules//langfuse"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "postgresql" {
  config_path = "../../20-data/postgresql"

  mock_outputs = {
    host           = "postgresql.postgresql.svc.cluster.local"
    port           = 5432
    admin_user     = "postgres"
    admin_password = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "redis" {
  config_path = "../../20-data/redis"

  mock_outputs = {
    host     = "redis-master.redis.svc.cluster.local"
    port     = 6379
    password = "mock"
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
  # --- Postgres / Redis / MinIO from dependency blocks ---
  postgres_host           = dependency.postgresql.outputs.host
  postgres_port           = dependency.postgresql.outputs.port
  postgres_admin_user     = dependency.postgresql.outputs.admin_user
  postgres_admin_password = dependency.postgresql.outputs.admin_password

  redis_host     = dependency.redis.outputs.host
  redis_port     = dependency.redis.outputs.port
  redis_password = dependency.redis.outputs.password

  minio_endpoint   = dependency.minio.outputs.api_endpoint
  minio_access_key = dependency.minio.outputs.access_key
  minio_secret_key = dependency.minio.outputs.secret_key

  # --- External domain — DUMMY, unused fallback (public_url below always wins) ---
  tailscale_domain = "tailscale.local"
  public_url       = "http://localhost:23017"

  # --- Demo secrets from env.hcl (NOT SOPS) ---
  postgres_password       = include.root.locals.env.demo.langfuse_postgres_password
  clickhouse_password     = include.root.locals.env.demo.langfuse_clickhouse_password
  salt                    = include.root.locals.env.demo.langfuse_salt
  encryption_key          = include.root.locals.env.demo.langfuse_encryption_key
  nextauth_secret         = include.root.locals.env.demo.langfuse_nextauth_secret
  init_org_id             = include.root.locals.env.demo.langfuse_init_org_id
  init_project_id         = include.root.locals.env.demo.langfuse_init_project_id
  init_project_public_key = include.root.locals.env.demo.langfuse_public_key
  init_project_secret_key = include.root.locals.env.demo.langfuse_secret_key
  init_user_email         = include.root.locals.env.demo.langfuse_init_user_email
  init_user_password      = include.root.locals.env.demo.langfuse_init_user_password

  # OTLP trace ingestion — REQUIRED for COELHO Nexus.
  enable_otel_ingestion = true

  # Defaults from variables.tf are appropriate:
  #   chart 1.5.31, ClickHouse 1Gi/3Gi (10Gi PVC), web + worker 100m/1Gi,
  #   daily pg_dump backup, 14-day retention.

  # Local access (k3d only) — NodePort 30481->23017, mapped via
  # `k3d cluster edit coelhonexus --port-add "23017:30481@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # public_url above is pinned to this same :23017 address — it's the only
  # access mechanism now (the old port-forward-script path was deleted
  # 2026-07-04; see docs/APP-LAYER-NODEPORT-MIGRATION-2026-07-03.md).
  enable_local_expose = true
  k3d_web_node_port   = 30481
}
