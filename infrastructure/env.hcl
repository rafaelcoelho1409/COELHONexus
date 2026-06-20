# =============================================================================
# COELHO Nexus standalone — env.hcl
# =============================================================================
# Shared inputs for every leaf under `live/coelhonexus/`. Read by root.hcl via
# `read_terragrunt_config(find_in_parent_folders("env.hcl")).inputs`.
#
# Demo credentials are HARDCODED below intentionally. They unlock only the
# local `coelhonexus` k3d cluster — reachable only from your own machine.
# Encrypting with SOPS would add tooling friction (sops + age install) for
# zero security benefit at this scope.
#
# If you ever repoint these at real credentials (e.g. you start running this
# cluster against sensitive data), MIGRATE TO SOPS first — see
# infrastructure/README.md §4 for the one-step migration recipe.
# =============================================================================

inputs = {
  # ---------------------------------------------------------------------------
  # Cluster identity
  # ---------------------------------------------------------------------------
  env_name     = "coelhonexus"
  cluster_name = "coelhonexus"                                # → k3d cluster + kubectl context k3d-coelhonexus
  data_path    = "${get_repo_root()}/infrastructure/.data"   # host bind-mount for PVCs

  # ---------------------------------------------------------------------------
  # Substrate posture
  # ---------------------------------------------------------------------------
  # All modules support disabling tailnet exposure via this variable;
  # propagated to every leaf via the env-level inputs.
  enable_tailscale_exposure = false

  # ---------------------------------------------------------------------------
  # Demo credentials — committed plaintext (intentional, demo-only scope).
  # ---------------------------------------------------------------------------
  demo = {
    postgres_user           = "postgres"
    postgres_password       = "postgres"
    redis_password          = "redis-demo-password"
    neo4j_username          = "neo4j"
    neo4j_password          = "neo4j-demo-password"
    elasticsearch_username  = "coelhonexus"
    elasticsearch_password  = "coelhonexus-demo-password"
    minio_access_key        = "minioadmin"
    minio_secret_key        = "minioadmin"
    qdrant_api_key          = "qdrant-demo-api-key"
    playwright_vnc_password = "vnc-demo-password"
    rancher_bootstrap_password = "rancher-demo-bootstrap"
    langfuse_public_key     = "lf_pk_demo000000000000000000000000"
    langfuse_secret_key     = "lf_sk_demo000000000000000000000000000000000000000000000000000000"
    # Langfuse module — additional secrets all demo-only:
    langfuse_postgres_password   = "langfuse-demo-password"
    langfuse_clickhouse_password = "clickhouse-demo-password"
    langfuse_salt                = "demo-salt-stable-across-restarts"
    langfuse_encryption_key      = "0000000000000000000000000000000000000000000000000000000000000000"  # 64 hex = 256-bit demo (insecure; local-only)
    langfuse_nextauth_secret     = "nextauth-secret-stable-across-restarts"
    langfuse_init_org_id         = "demo-org"
    langfuse_init_project_id     = "demo-project"
    langfuse_init_user_email     = "admin@demo.local"
    langfuse_init_user_password  = "admin-demo-password"
    grafana_admin_user      = "admin"
    grafana_admin_password  = "admin"
  }
}
