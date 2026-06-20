# =============================================================================
# minio module — outputs
# =============================================================================
# Downstream modules (Mimir, Loki, Tempo, GitLab, MLflow, Langfuse, backup
# CronJobs) read these to wire up S3 connectivity. Sensitive outputs are
# marked accordingly so they don't leak into plan/apply logs.
# =============================================================================

output "namespace" {
  description = "Namespace where MinIO is installed."
  value       = kubernetes_namespace_v1.minio.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.minio.version
}

output "app_version" {
  description = "MinIO app version (matches the chart's appVersion)."
  value       = helm_release.minio.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster access — what other modules use to talk to MinIO
# -----------------------------------------------------------------------------

output "service_name" {
  description = "Helm release Service name (ClusterIP). Same name handles both API (9000) and Console (9001)."
  value       = var.release_name
}

output "host" {
  description = "In-cluster DNS name for the MinIO Service."
  value       = "${var.release_name}.${kubernetes_namespace_v1.minio.metadata[0].name}.svc.cluster.local"
}

output "api_endpoint" {
  description = "In-cluster S3 endpoint URL (HTTP — TLS is at the Tailscale proxy, not internal)."
  value       = "http://${var.release_name}.${kubernetes_namespace_v1.minio.metadata[0].name}.svc.cluster.local:9000"
}

output "api_port" {
  description = "S3 API port."
  value       = 9000
}

output "console_port" {
  description = "Console UI port."
  value       = 9001
}

# -----------------------------------------------------------------------------
# External (Tailnet) URLs
# -----------------------------------------------------------------------------

output "console_url" {
  description = "MinIO Console UI URL (via Tailscale)."
  value       = "https://${var.tailscale_hostname_console}.${var.tailscale_domain}"
}

output "api_url" {
  description = "MinIO S3 API URL (via Tailscale). Used by external clients (mc CLI, S3 SDKs from non-cluster machines)."
  value       = "https://${var.tailscale_hostname_api}.${var.tailscale_domain}"
}

output "tailscale_hostname_console" {
  description = "Short tailnet hostname for the Console (cutover marker: 'minio-v2' → 'minio')."
  value       = var.tailscale_hostname_console
}

output "tailscale_hostname_api" {
  description = "Short tailnet hostname for the S3 API (cutover marker: 'minio-api-v2' → 'minio-api')."
  value       = var.tailscale_hostname_api
}

# -----------------------------------------------------------------------------
# Credentials (sensitive — flow into downstream modules' Secret/env config)
# -----------------------------------------------------------------------------

output "access_key" {
  description = "S3 access key (= root_user). Same value as v1, reused during migration."
  value       = var.root_user
}

output "secret_key" {
  description = "S3 secret key (= root_password). Reused from v1 SOPS during migration."
  value       = var.root_password
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Convenience block — full S3 config for downstream `dependency` consumers
# -----------------------------------------------------------------------------
# Pattern: a downstream module can do
#   dependency "minio" { config_path = "../minio" }
#   inputs = { s3 = dependency.minio.outputs.s3_config }
# instead of stitching access/secret/endpoint manually.
# -----------------------------------------------------------------------------

output "s3_config" {
  description = "Bundled S3 connection block for downstream modules (Mimir, Loki, Tempo, MLflow, etc.)."
  value = {
    endpoint   = "http://${var.release_name}.${kubernetes_namespace_v1.minio.metadata[0].name}.svc.cluster.local:9000"
    access_key = var.root_user
    secret_key = var.root_password
    region     = "us-east-1" # MinIO ignores region but S3 SDKs require something
  }
  sensitive = true
}

# -----------------------------------------------------------------------------
# Dependency signal
# -----------------------------------------------------------------------------

output "ready" {
  description = "Helm release status string ('deployed' on success). Use as marker for downstream `dependency` blocks."
  value       = helm_release.minio.status
}
