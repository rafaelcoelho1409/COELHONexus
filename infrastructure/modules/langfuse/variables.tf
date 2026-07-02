# =============================================================================
# langfuse module — variables
# =============================================================================
# Reuses v1 langfuse_* tfvars (per memory feedback_secrets_reuse). Fresh
# vars: redis_password (uses v2 baseline Redis, not v1's bundled Valkey).
# =============================================================================

# -----------------------------------------------------------------------------
# Identity
# -----------------------------------------------------------------------------
variable "namespace" {
  description = "Kubernetes namespace for Langfuse."
  type        = string
  default     = "langfuse"
}

variable "release_name" {
  description = "Helm release name (also used as the Service name prefix)."
  type        = string
  default     = "langfuse"
}

variable "chart_version" {
  description = "langfuse/langfuse Helm chart version (pinned for reproducibility)."
  type        = string
  default     = "1.5.31" # appVersion 3.174.1 (2026-05-18). Identical values.yaml schema vs 1.5.29 — verified via diff.
}

# -----------------------------------------------------------------------------
# Postgres — v2 baseline (per memory feedback_default_to_v2_baseline_dbs)
# -----------------------------------------------------------------------------
variable "postgres_host" {
  description = "Postgres host (in-cluster, e.g. postgresql.postgresql.svc.cluster.local)."
  type        = string
}

variable "postgres_port" {
  description = "Postgres port."
  type        = number
  default     = 5432
}

variable "postgres_database" {
  description = "Postgres database name for Langfuse."
  type        = string
  default     = "langfuse"
}

variable "postgres_user" {
  description = "Postgres role for Langfuse (owns its DB)."
  type        = string
  default     = "langfuse"
}

variable "postgres_password" {
  description = "Postgres password for the langfuse role. URL-safe characters only — Langfuse builds DSNs from this."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.postgres_password))
    error_message = "postgres_password must be URL-safe (alphanumeric + '_.-' only)."
  }
}

variable "postgres_admin_user" {
  description = "Postgres admin role used by the bootstrap Job (creates langfuse role + db)."
  type        = string
  default     = "postgres"
}

variable "postgres_admin_password" {
  description = "Postgres admin password (from postgresql dependency output)."
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Redis — v2 baseline (DB index 3 — OpenWebUI=0, GitLab=1, ArgoCD=2)
# -----------------------------------------------------------------------------
variable "redis_host" {
  description = "Redis host (in-cluster, e.g. redis-master.redis.svc.cluster.local)."
  type        = string
}

variable "redis_port" {
  description = "Redis port."
  type        = number
  default     = 6379
}

variable "redis_password" {
  description = "Shared v2 Redis password (from redis dependency output)."
  type        = string
  sensitive   = true
}

variable "redis_db" {
  description = "Redis logical DB index for Langfuse. Note: namespacing only — Redis is single-threaded, so this does NOT isolate I/O. Acceptable for homelab single-user load."
  type        = number
  default     = 3
}

# -----------------------------------------------------------------------------
# MinIO / S3 — v2 baseline (shared `backups` bucket, `langfuse/` prefix)
# -----------------------------------------------------------------------------
variable "minio_endpoint" {
  description = "MinIO S3 endpoint (in-cluster, e.g. http://minio.minio.svc.cluster.local:9000)."
  type        = string
}

variable "minio_access_key" {
  description = "MinIO access key (from minio dependency)."
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key (from minio dependency)."
  type        = string
  sensitive   = true
}

variable "artifacts_bucket" {
  description = "MinIO bucket holding Langfuse blobs (events, media, exports). Reuses the cluster-wide `backups` bucket."
  type        = string
  default     = "backups"
}

variable "artifacts_prefix" {
  description = "Prefix inside the bucket for Langfuse blobs."
  type        = string
  default     = "langfuse"
}

# -----------------------------------------------------------------------------
# ClickHouse — bundled (no v2 baseline; exception per feedback_default_to_v2_baseline_dbs)
# -----------------------------------------------------------------------------
variable "clickhouse_password" {
  description = "ClickHouse default-user password. URL-safe — go-migrate builds DSN URLs."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.clickhouse_password))
    error_message = "clickhouse_password must be URL-safe (alphanumeric + '_.-' only)."
  }
}

variable "clickhouse_storage_size" {
  description = "ClickHouse PVC size. v1 used 20Gi; trimmed for homelab. Grow when trace volume demands."
  type        = string
  default     = "10Gi"
}

variable "clickhouse_memory_request" {
  description = "ClickHouse memory request. Actual RSS ~555 MiB on a 1.09 GiB DB."
  type        = string
  default     = "768Mi"
}

variable "clickhouse_memory_limit" {
  description = "ClickHouse memory limit. Tier 1 (2026-05-25) dropped 3Gi→1.5Gi; max_server_memory_usage XML hard-caps at 1.2Gi inside container."
  type        = string
  default     = "1536Mi"
}

# -----------------------------------------------------------------------------
# Langfuse application secrets (reused from v1 — see feedback_secrets_reuse)
# -----------------------------------------------------------------------------
variable "salt" {
  description = "Langfuse SALT — random seed used for hashing API keys. Stable across restarts."
  type        = string
  sensitive   = true
}

variable "encryption_key" {
  description = "Langfuse ENCRYPTION_KEY — 256-bit hex (64 chars). Encrypts API keys at rest. Stable across restarts."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[0-9a-fA-F]{64}$", var.encryption_key))
    error_message = "encryption_key must be 64 hex characters (256-bit)."
  }
}

variable "nextauth_secret" {
  description = "NextAuth.js session-cookie signing secret."
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Headless initialization — seeds org/project/keys/user on first start
# Idempotent: only seeds when the resource doesn't already exist.
# Docs: https://langfuse.com/self-hosting/administration/headless-initialization
# -----------------------------------------------------------------------------
variable "init_org_id" {
  description = "Stable org slug. Primary key — do not change after first init."
  type        = string
  default     = "coelho"
}

variable "init_project_id" {
  description = "Stable project slug. Primary key — do not change after first init."
  type        = string
  default     = "coelhocloud"
}

variable "init_project_public_key" {
  description = "Project public API key. Must start with 'lf_pk_'."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^lf_pk_[A-Za-z0-9_-]+$", var.init_project_public_key))
    error_message = "init_project_public_key must start with 'lf_pk_'."
  }
}

variable "init_project_secret_key" {
  description = "Project secret API key. Must start with 'lf_sk_'."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^lf_sk_[A-Za-z0-9_-]+$", var.init_project_secret_key))
    error_message = "init_project_secret_key must start with 'lf_sk_'."
  }
}

variable "init_user_email" {
  description = "Email for the seeded admin user."
  type        = string
}

variable "init_user_password" {
  description = "Password for the seeded admin user."
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Resources (homelab-tuned, NOT langfuse-recommended production budgets)
# -----------------------------------------------------------------------------
variable "web_cpu_request" {
  type    = string
  default = "200m"
}
variable "web_memory_request" {
  type    = string
  default = "640Mi"
}
variable "web_memory_limit" {
  type    = string
  default = "768Mi"
}
variable "worker_cpu_request" {
  type    = string
  default = "200m"
}
variable "worker_memory_request" {
  type    = string
  default = "640Mi"
}
variable "worker_memory_limit" {
  type    = string
  default = "768Mi"
}

# -----------------------------------------------------------------------------
# Node.js / V8 tuning — Tier 1 RAM optimization
# -----------------------------------------------------------------------------
variable "node_max_old_space_size_mb" {
  description = "V8 max-old-space-size for web + worker. ~80% of memory limit so JVM-style heap cap doesn't fight cgroup."
  type        = number
  default     = 640
}

variable "log_level" {
  description = "Langfuse log level. 'warn' drops idle info noise; errors still surface."
  type        = string
  default     = "warn"
}

variable "redis_blocking_socket_timeout_ms" {
  description = "BullMQ blocking-pop idle timeout. 30s default fires every 30s on idle queues → log spam; 5min cuts noise ~10x without hiding real Redis failures."
  type        = string
  default     = "300000"
}

variable "s3_concurrent_reads" {
  description = "Worker S3 GET concurrency. Chart default 50 — homelab moves <1 file/s."
  type        = string
  default     = "4"
}

variable "s3_concurrent_writes" {
  description = "Worker S3 PUT concurrency. Chart default 50 — homelab moves <1 file/s."
  type        = string
  default     = "4"
}

variable "clickhouse_write_interval_ms" {
  description = "Worker batch-write interval to ClickHouse. 5000 = 5x fewer write QPS vs 1000 default for low-throughput."
  type        = string
  default     = "5000"
}

variable "clickhouse_max_concurrent_queries" {
  description = "ClickHouse server-wide concurrent-query cap. Module default 20 was too low — when the langfuse-worker drains its OTel/ingestion backlog it fires bursts of 30+ concurrent ClickHouse writes, breaching 20 and surfacing as 'Too many simultaneous queries' errors + traces.byId tRPC 500s in the UI (rows partial). Raise to the ClickHouse upstream default (100) for headroom; bundled CH is sized to handle it on this host."
  type        = number
  default     = 100
}

variable "ingestion_queue_concurrency" {
  description = "Worker BullMQ concurrency for `ingestion-queue` (LANGFUSE_INGESTION_QUEUE_PROCESSING_CONCURRENCY). Matches LangFuse's documented SOTA target of `20 per worker per queue` (no sharding). Chart default 50 → too high for the bundled CH; this stays comfortably under clickhouse_max_concurrent_queries (100) leaving headroom for trace-upsert + otel-ingestion + UI reads."
  type        = number
  default     = 20
}

variable "trace_upsert_worker_concurrency" {
  description = "Worker concurrency for `trace-upsert-queue` (LANGFUSE_TRACE_UPSERT_WORKER_CONCURRENCY). Half of LangFuse's `20 per worker` target — trace-upsert is single-table writes vs. ingestion's multi-table fan-out, so it can be a bit lighter without slowing the drain. Tunable up to 20 if CH headroom allows."
  type        = number
  default     = 10
}

variable "otel_ingestion_queue_concurrency" {
  description = "Worker BullMQ concurrency for `otel-ingestion-queue` (LANGFUSE_OTEL_INGESTION_QUEUE_PROCESSING_CONCURRENCY). Pinned to LangFuse's `20 per worker` SOTA target so the COELHO Nexus OTLP push path has matching throughput to the SDK ingestion path. Without this env, the queue inherits the chart's silent default which drifts across chart versions."
  type        = number
  default     = 20
}

# -----------------------------------------------------------------------------
# Feature queue toggles — disable unused subsystems
# -----------------------------------------------------------------------------
variable "enable_otel_ingestion" {
  description = "OTLP trace ingestion queue. Disabled by default — homelab pushes traces via SDK, not OTLP."
  type        = bool
  default     = false
}

variable "enable_posthog_integration" {
  description = "PostHog integration worker queue. Disabled by default — no PostHog upstream."
  type        = bool
  default     = false
}

variable "enable_mixpanel_integration" {
  description = "Mixpanel integration worker queue. Disabled by default — no Mixpanel upstream."
  type        = bool
  default     = false
}

variable "enable_notification_queue" {
  description = "Notification (email/webhook) worker queue. Disabled by default — no SMTP configured."
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Tailscale Ingress
# -----------------------------------------------------------------------------
variable "tailscale_hostname" {
  description = "Tailnet hostname for the Langfuse UI (e.g. 'langfuse-v2' or 'langfuse')."
  type        = string
  default     = "langfuse"
}

variable "tailscale_domain" {
  description = "Tailnet base domain (e.g. YOUR_TAILNET_DOMAIN.ts.net)."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "Tailscale operator's IngressClass name."
  type        = string
  default     = "tailscale"
}

variable "public_url" {
  description = "Optional browser URL for Langfuse. Set this for localhost port-forward deployments; otherwise the module derives the Tailscale URL."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Backup
# -----------------------------------------------------------------------------
variable "backup_schedule" {
  description = "Cron schedule for the Langfuse pg_dump backup (UTC). Offset from MLflow (04:30) and Qdrant (every 6h)."
  type        = string
  default     = "15 3 * * *" # 03:15 UTC daily
}

variable "backup_retention_days" {
  description = "Days to keep daily pg_dump backups in MinIO."
  type        = number
  default     = 14
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Opt-in NodePort Service for localhost access via k3d's loadbalancer port
# mapping. Leave `enable_local_expose` unset (default false) on any
# environment where Tailscale Ingress already provides access (e.g. COELHO
# Cloud) — the module below is never instantiated in that case. See
# infrastructure/modules/k3d_expose/.
#
# NOTE: Langfuse already has a working localhost path via
# scripts/standalone-port-forward.sh (23006->3000), and `public_url` above is
# still pinned to that address for links Langfuse generates internally
# (shareable trace URLs, etc). This NodePort is a second, independent
# mechanism to REACH the UI — it doesn't change what Langfuse thinks its own
# public URL is.

variable "enable_local_expose" {
  description = "Create a NodePort Service for localhost access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_web_node_port" {
  description = "NodePort for local Langfuse UI access (target port 3000). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}
