# =============================================================================
# postgresql module — outputs
# =============================================================================
# Downstream modules (GitLab, MLflow, Langfuse, Vaultwarden, Grafana) read
# these to get connection info. The admin_password output is sensitive and
# can be used to bootstrap per-app DBs/users from the consumer side.
# =============================================================================

output "namespace" {
  description = "Namespace where PostgreSQL is installed."
  value       = kubernetes_namespace_v1.postgresql.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.postgresql.version
}

output "app_version" {
  description = "PostgreSQL server version (matches chart's appVersion — typically 18.x)."
  value       = helm_release.postgresql.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster connection info
# -----------------------------------------------------------------------------

output "service_name" {
  description = "Helm release name == StatefulSet/Service name (chart convention)."
  value       = var.release_name
}

output "host" {
  description = "In-cluster DNS name for the PostgreSQL primary."
  value       = "${var.release_name}.${kubernetes_namespace_v1.postgresql.metadata[0].name}.svc.cluster.local"
}

output "port" {
  description = "PostgreSQL listen port."
  value       = 5432
}

# -----------------------------------------------------------------------------
# Admin credentials (sensitive)
# -----------------------------------------------------------------------------

output "admin_user" {
  description = "PostgreSQL admin username (== `postgres` by default)."
  value       = var.admin_user
}

output "admin_password" {
  description = "PostgreSQL admin password. Used by downstream modules to bootstrap their own DBs/users."
  value       = var.admin_password
  sensitive   = true
}

output "default_database" {
  description = "Default database name created at install time."
  value       = var.default_database
}

# -----------------------------------------------------------------------------
# Convenience block for downstream `dependency` consumers
# -----------------------------------------------------------------------------
# Pattern: a downstream module can do
#   dependency "postgres" { config_path = "../postgresql" }
#   inputs = { db = dependency.postgres.outputs.connection }
# instead of stitching host/port/user/password manually.
# -----------------------------------------------------------------------------

output "connection" {
  description = "Bundled in-cluster Postgres connection block for downstream modules."
  value = {
    host     = "${var.release_name}.${kubernetes_namespace_v1.postgresql.metadata[0].name}.svc.cluster.local"
    port     = 5432
    user     = var.admin_user
    password = var.admin_password
    database = var.default_database
    url      = "postgresql://${var.admin_user}:${var.admin_password}@${var.release_name}.${kubernetes_namespace_v1.postgresql.metadata[0].name}.svc.cluster.local:5432/${var.default_database}"
  }
  sensitive = true
}

# -----------------------------------------------------------------------------
# External access — only populated if enable_tailscale_exposure=true
# -----------------------------------------------------------------------------

output "tailscale_host" {
  description = "Fully-qualified external hostname when exposure is enabled (e.g. 'postgresql.YOUR_EXTERNAL_DOMAIN.example.com'). Empty string when not exposed."
  value       = var.enable_tailscale_exposure && var.tailscale_domain != "" ? "${var.tailscale_hostname}.${var.tailscale_domain}" : ""
}

output "tailscale_psql_url" {
  description = "psql connection string for laptop/external use via the external proxy. Empty when not exposed."
  value       = var.enable_tailscale_exposure && var.tailscale_domain != "" ? "postgresql://${var.admin_user}:${var.admin_password}@${var.tailscale_hostname}.${var.tailscale_domain}:5432/${var.default_database}" : ""
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Dependency signal
# -----------------------------------------------------------------------------

output "ready" {
  description = "Helm release status string ('deployed' on success). Use as marker for downstream `dependency` blocks."
  value       = helm_release.postgresql.status
}
