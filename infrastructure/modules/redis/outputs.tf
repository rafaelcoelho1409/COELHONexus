# =============================================================================
# redis module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Redis is installed."
  value       = kubernetes_namespace_v1.redis.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.redis.version
}

output "app_version" {
  description = "Redis app version (matches chart's appVersion — but image is overridden to redis-stack-server)."
  value       = helm_release.redis.metadata.app_version
}

output "modules_loaded" {
  description = "Redis Stack modules available in this deployment."
  value       = ["RediSearch", "RedisJSON", "RedisTimeSeries", "RedisBloom"]
}

# -----------------------------------------------------------------------------
# In-cluster connection info
# -----------------------------------------------------------------------------

output "service_name" {
  description = "Master Service name (Bitnami chart convention: <release>-master)."
  value       = "${var.release_name}-master"
}

output "host" {
  description = "In-cluster DNS name for the Redis master."
  value       = "${var.release_name}-master.${kubernetes_namespace_v1.redis.metadata[0].name}.svc.cluster.local"
}

output "port" {
  description = "Redis listen port."
  value       = 6379
}

# -----------------------------------------------------------------------------
# Credentials (sensitive)
# -----------------------------------------------------------------------------

output "password" {
  description = "Redis password."
  value       = var.redis_password
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Convenience block for downstream `dependency` consumers
# -----------------------------------------------------------------------------

output "connection" {
  description = "Bundled in-cluster Redis connection block."
  value = {
    host     = "${var.release_name}-master.${kubernetes_namespace_v1.redis.metadata[0].name}.svc.cluster.local"
    port     = 6379
    password = var.redis_password
    url      = "redis://:${var.redis_password}@${var.release_name}-master.${kubernetes_namespace_v1.redis.metadata[0].name}.svc.cluster.local:6379"
  }
  sensitive = true
}

# -----------------------------------------------------------------------------
# External (tailnet) access — only when enable_tailscale_exposure=true
# -----------------------------------------------------------------------------

output "tailscale_host" {
  description = "Tailnet hostname for redis-cli access. Empty when not exposed."
  value       = var.enable_tailscale_exposure && var.tailscale_domain != "" ? "${var.tailscale_hostname}.${var.tailscale_domain}" : ""
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.redis.status
}
