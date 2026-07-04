# =============================================================================
# Mimir Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana/mimir-distributed v6.0.6 (appVersion 3.0.4)
# Repo:  https://grafana.github.io/helm-charts
#
# All interpolated variables are SCALARS (per memory feedback_yamlencode_helm_values).
#
# v2 baseline DBs:
#   - chart's bundled MinIO sub-chart DISABLED  → use central MinIO at ${minio_endpoint}
#   - chart's bundled Memcached caches DISABLED → trade RAM, slower cold queries
# =============================================================================

# -----------------------------------------------------------------------------
# Global pod security
# -----------------------------------------------------------------------------
global:
  podSecurityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001

# -----------------------------------------------------------------------------
# Mimir runtime config
# -----------------------------------------------------------------------------
mimir:
  structuredConfig:
    multitenancy_enabled: false

    # Limits — tuned for ~50K active series initial baseline, generous burst.
    # Ingestion rate bumped 4× from defaults (25000/50000) after observing
    # rejections during cluster restart cascades — every recovering pod
    # re-emits backlogged metrics simultaneously. Homelab capacity easily
    # handles 100k items/s; rate limit is per-tenant and we have one tenant
    # ("anonymous"), so isolation isn't a concern.
    limits:
      ingestion_rate: 100000
      ingestion_burst_size: 200000
      max_global_series_per_user: 500000
      max_global_series_per_metric: 50000
      max_fetched_series_per_query: 100000
      max_fetched_chunks_per_query: 2000000
      compactor_blocks_retention_period: ${retention_period}

    server:
      log_level: warn
      grpc_server_max_recv_msg_size: 104857600
      grpc_server_max_send_msg_size: 104857600

    # Single-replica homelab → RF=1.
    ingester:
      ring:
        replication_factor: 1
      # Re-enable Push gRPC API (chart disables it when ingest_storage is on;
      # we keep ingest_storage off, so we need this back).
      push_grpc_method_enabled: true

    # Disable Kafka-based ingest storage (using classic remote_write only).
    ingest_storage:
      enabled: false

    frontend:
      parallelize_shardable_queries: true

    query_scheduler:
      max_outstanding_requests_per_tenant: 1000

    activity_tracker:
      filepath: /active-query-tracker/activity.log

    ruler:
      enable_api: true
      rule_path: /data

    # -------------------------------------------------------------------------
    # S3/MinIO backend — single common block, all components inherit it
    # -------------------------------------------------------------------------
    common:
      storage:
        backend: s3
        s3:
          endpoint: "${minio_endpoint}"
          access_key_id: "${minio_access_key}"
          secret_access_key: "${minio_secret_key}"
          insecure: true
          http:
            insecure_skip_verify: true

    blocks_storage:
      backend: s3
      s3:
        bucket_name: ${blocks_bucket}
      tsdb:
        dir: /data/ingester
        retention_period: 24h
        block_ranges_period: [2h]
        ship_interval: 1m
      bucket_store:
        sync_dir: /data/tsdb-sync

    ruler_storage:
      backend: s3
      s3:
        bucket_name: ${ruler_bucket}

    alertmanager_storage:
      backend: s3
      s3:
        bucket_name: ${alertmanager_bucket}

# -----------------------------------------------------------------------------
# Components (each at replicas=1)
# -----------------------------------------------------------------------------

ingester:
  replicas: 1
  resources:
    requests:
      cpu: ${ingester_cpu_request}
      memory: ${ingester_memory_request}
    limits:
      memory: ${ingester_memory_limit}
  persistentVolume:
    enabled: true
    size: ${ingester_pvc_size}
    storageClass: ${storage_class}
  zoneAwareReplication:
    enabled: false
  topologySpreadConstraints: {}
  affinity: {}

distributor:
  replicas: 1
  resources:
    requests:
      cpu: 50m
      memory: ${distributor_memory_request}
    limits:
      memory: ${distributor_memory_limit}

querier:
  replicas: 1
  resources:
    requests:
      cpu: 50m
      memory: ${querier_memory_request}
    limits:
      memory: ${querier_memory_limit}

query_frontend:
  replicas: 1
  resources:
    requests:
      cpu: 25m
      memory: ${query_frontend_memory_request}
    limits:
      memory: ${query_frontend_memory_limit}

store_gateway:
  replicas: 1
  resources:
    requests:
      cpu: 50m
      memory: ${store_gateway_memory_request}
    limits:
      memory: ${store_gateway_memory_limit}
  persistentVolume:
    enabled: true
    size: ${store_gateway_pvc_size}
    storageClass: ${storage_class}
  zoneAwareReplication:
    enabled: false
  topologySpreadConstraints: {}
  affinity: {}

compactor:
  replicas: 1
  resources:
    requests:
      cpu: 50m
      memory: ${compactor_memory_request}
    limits:
      memory: ${compactor_memory_limit}
  persistentVolume:
    enabled: true
    size: ${compactor_pvc_size}
    storageClass: ${storage_class}

ruler:
  enabled: true
  replicas: 1
  resources:
    requests:
      cpu: 25m
      memory: ${ruler_memory_request}
    limits:
      memory: ${ruler_memory_limit}

query_scheduler:
  enabled: true
  replicas: 1
  resources:
    requests:
      cpu: 25m
      memory: ${query_scheduler_memory_request}
    limits:
      memory: ${query_scheduler_memory_limit}

gateway:
  enabled: true
  replicas: 1
  resources:
    requests:
      cpu: 25m
      memory: ${gateway_memory_request}
    limits:
      memory: ${gateway_memory_limit}

rollout_operator:
  enabled: true
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      memory: 64Mi

# -----------------------------------------------------------------------------
# Disabled components — keep RAM and disk lean for homelab
# -----------------------------------------------------------------------------

# Alertmanager — Grafana's unified alerting handles routing; Mimir's ruler
# evaluates recording rules but doesn't need its own alertmanager.
alertmanager:
  enabled: false

# Per-component caches — memcached pods would add ~512Mi+ RAM for small wins
# at homelab scale. Re-enable if cold queries become painful.
chunks-cache:
  enabled: false
index-cache:
  enabled: false
metadata-cache:
  enabled: false
results-cache:
  enabled: false

# We use central v2 MinIO; the bundled MinIO sub-chart would be a duplicate.
minio:
  enabled: false

# Kafka-based ingest storage path — keep classic remote_write.
kafka:
  enabled: false

# Optional self-test/CI pods — never useful in prod.
continuous_test:
  enabled: false
smoke_test:
  enabled: false

# Override stats exporter — niche, off.
overrides_exporter:
  enabled: false

# -----------------------------------------------------------------------------
# Self-monitoring — ServiceMonitor + recording rules + dashboards out of the box
# -----------------------------------------------------------------------------
metaMonitoring:
  dashboards:
    enabled: true
    labels:
      grafana_dashboard: "1"
  serviceMonitor:
    enabled: true
    interval: 30s
    relabelings:
      - targetLabel: cluster
        replacement: ${cluster_name}
  prometheusRule:
    enabled: true
    mimirAlerts: false # Disable noisy alerts in homelab; turn on later if desired.
    mimirRules: true
