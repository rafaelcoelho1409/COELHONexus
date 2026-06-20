# =============================================================================
# redis module — provider requirements
# =============================================================================
#
# Three providers needed:
#   - helm: deploy bitnami/redis chart with redis-stack-server image
#   - kubernetes: namespace + Secret for backup CronJob + the CronJob
#   - random: not used (admin password from SOPS); kept for symmetry / future use
#
# Chart provenance:
#   v1 used bitnami/redis (latest at the time) with the redis-stack-server
#   image override (7.4.0-v8). v2 uses bitnami chart 25.4.1 with the same
#   image override pattern. RediSearch + RedisJSON + RedisTimeSeries +
#   RedisBloom modules ship in redis-stack-server — needed by future
#   LangGraph apps.
# =============================================================================

terraform {
  required_version = ">= 1.10"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
  }
}
