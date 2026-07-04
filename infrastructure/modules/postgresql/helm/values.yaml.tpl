# =============================================================================
# Bitnami PostgreSQL Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: bitnami/postgresql v18.6.2 (deploys PostgreSQL 18)
# Repo:  oci://registry-1.docker.io/bitnamicharts (or older
#        https://charts.bitnami.com/bitnami — both work)
#
# Variables interpolated (all SCALARS — no yamlencode interpolation per the
# MinIO bug we hit earlier):
#   ${admin_user}, ${admin_password}, ${default_database}
#   ${storage_class}, ${storage_size}
#   ${cpu_request}, ${memory_request}, ${memory_limit}
#   ${max_connections}, ${shared_buffers}, ${effective_cache_size}
#   ${enable_servicemonitor}
# =============================================================================

# Standalone — single primary, no replication. v1 ran the same.
architecture: standalone

# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------
# Bitnami uses BOTH global.postgresql.auth and auth — we set both for safety
# (some sub-keys read from each in different chart versions).
# -----------------------------------------------------------------------------
global:
  postgresql:
    auth:
      postgresPassword: "${admin_password}"
      username: "${admin_user}"
      password: "${admin_password}"
      database: "${default_database}"

auth:
  postgresPassword: "${admin_password}"
  username: "${admin_user}"
  password: "${admin_password}"
  database: "${default_database}"

# -----------------------------------------------------------------------------
# Primary pod configuration
# -----------------------------------------------------------------------------
primary:
  service:
    type: ClusterIP
    ports:
      postgresql: 5432

  persistence:
    enabled: true
    storageClass: "${storage_class}"
    size: ${storage_size}
    accessModes:
      - ReadWriteOnce

  # Real measured: 20m CPU, 172Mi RAM. Below leaves ~2x headroom.
  resources:
    requests:
      cpu: "${cpu_request}"
      memory: "${memory_request}"
    limits:
      memory: "${memory_limit}"

  # /var/run/postgresql needs writable storage; chart's default tmpfs sometimes
  # collides with read-only-root-fs container settings.
  extraVolumes:
    - name: run-postgresql
      emptyDir: {}
  extraVolumeMounts:
    - name: run-postgresql
      mountPath: /var/run/postgresql

  # postgresql.conf overrides — tuned for homelab workload + 384Mi mem limit.
  # Inlined as literal YAML (multi-line string). No yamlencode interpolation.
  extendedConfiguration: |
    max_connections = ${max_connections}
    shared_buffers = ${shared_buffers}
    effective_cache_size = ${effective_cache_size}
    maintenance_work_mem = 64MB
    checkpoint_completion_target = 0.9
    wal_buffers = 8MB
    default_statistics_target = 100
    random_page_cost = 1.1
    effective_io_concurrency = 200
    work_mem = 2621kB
    min_wal_size = 512MB
    max_wal_size = 2GB
    log_statement = 'ddl'
    log_connections = on
    log_disconnections = on

# -----------------------------------------------------------------------------
# Metrics — chart's built-in postgres_exporter + ServiceMonitor
# -----------------------------------------------------------------------------
metrics:
  enabled: true
  resources:
    requests:
      cpu: 5m
      memory: 32Mi
    limits:
      memory: 64Mi
  service:
    type: ClusterIP
  serviceMonitor:
    enabled: ${enable_servicemonitor}
    labels:
      release: kube-prometheus-stack
    interval: 30s

# -----------------------------------------------------------------------------
# Volume permissions — chart's helper init container chowns the PVC mount
# to the postgres UID. Required on local-path / k3d (filesystem starts owned
# by root and we run postgres as 1001).
# -----------------------------------------------------------------------------
volumePermissions:
  enabled: true
  securityContext:
    runAsUser: 0

# -----------------------------------------------------------------------------
# Don't deploy chart's built-in NetworkPolicy — homelab is single-tenant.
# Don't disable resource preset overrides — our explicit resources block above
# fully overrides the preset.
# -----------------------------------------------------------------------------
networkPolicy:
  enabled: false

# We're not using TLS on Postgres — it's cluster-internal, no external exposure.
tls:
  enabled: false
