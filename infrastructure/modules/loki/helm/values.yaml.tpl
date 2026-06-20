# =============================================================================
# Loki Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana-community/loki v13.5.0 (appVersion 3.7.1)
# Repo:  https://grafana-community.github.io/helm-charts
#
# All interpolated variables are SCALARS (per memory feedback_yamlencode_helm_values).
#
# Mode: Monolithic / SingleBinary — one StatefulSet runs all components.
# =============================================================================

deploymentMode: SingleBinary

# -----------------------------------------------------------------------------
# Loki runtime config
# -----------------------------------------------------------------------------
loki:
  auth_enabled: false # single-tenant homelab

  commonConfig:
    replication_factor: 1

  # Schema v13 + TSDB store. v13 is the latest schema; TSDB replaces the
  # deprecated BoltDB-shipper.
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: s3
        schema: v13
        index:
          prefix: loki_index_
          period: 24h

  # S3 backend = v2 MinIO. accessKeyId/secretAccessKey come from envFrom
  # (the loki-minio-credentials Secret), keeping rendered ConfigMaps clean.
  storage:
    type: s3
    bucketNames:
      chunks: ${chunks_bucket}
      ruler: ${ruler_bucket}
    s3:
      endpoint: ${minio_endpoint}
      s3ForcePathStyle: true
      insecure: true

  limits_config:
    ingestion_rate_mb: ${ingestion_rate_mb}
    ingestion_burst_size_mb: ${ingestion_burst_size_mb}
    per_stream_rate_limit: 5MB
    per_stream_rate_limit_burst: 15MB
    max_query_parallelism: 16
    max_query_series: 10000
    retention_period: ${retention_period}

  compactor:
    retention_enabled: true
    delete_request_store: s3

  rulerConfig:
    enable_api: true
    storage:
      type: s3

# -----------------------------------------------------------------------------
# SingleBinary — the only running pod in Monolithic mode
# -----------------------------------------------------------------------------
singleBinary:
  replicas: 1
  extraEnvFrom:
    - secretRef:
        name: ${minio_credentials_secret}
  resources:
    requests:
      cpu: ${cpu_request}
      memory: ${memory_request}
    limits:
      memory: ${memory_limit}
  persistence:
    enabled: true
    size: ${storage_size}
    storageClass: ${storage_class}

# -----------------------------------------------------------------------------
# Disabled components — keep the homelab footprint tight
# -----------------------------------------------------------------------------

# SimpleScalable's three components — replicas=0 so they don't render.
write:
  replicas: 0
read:
  replicas: 0
backend:
  replicas: 0

# Gateway — nginx in front of Loki for path-based routing. Not needed when
# Grafana queries the singleBinary Service directly.
gateway:
  enabled: false

# Bundled MinIO sub-chart — we use central v2 MinIO.
minio:
  enabled: false

# Memcached caches — singleBinary handles caching internally.
chunksCache:
  enabled: false
resultsCache:
  enabled: false

# Test framework + canary — homelab noise.
test:
  enabled: false
lokiCanary:
  enabled: false

# -----------------------------------------------------------------------------
# Self-monitoring — ServiceMonitor + dashboards
# -----------------------------------------------------------------------------
monitoring:
  dashboards:
    enabled: true
    labels:
      grafana_dashboard: "1"
  serviceMonitor:
    enabled: true
    interval: 30s
    relabelings:
      - targetLabel: cluster
        replacement: coelho-cloud
  rules:
    enabled: false # Loki recording/alerting rules off in homelab — Grafana's unified alerting handles routing.
  selfMonitoring:
    enabled: false # Avoid bundled Grafana Agent — we'll wire Alloy as the global collector.
    grafanaAgent:
      installOperator: false
  lokiCanary:
    enabled: false
