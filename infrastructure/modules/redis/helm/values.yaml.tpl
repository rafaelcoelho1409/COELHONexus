# =============================================================================
# Bitnami Redis Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: bitnami/redis v25.4.1
# Image: redis/redis-stack-server:7.4.0-v8 (NOT bitnami's redis image — we
#   override to get RediSearch/RedisJSON/RedisTimeSeries/RedisBloom modules)
#
# Variables interpolated (all SCALARS):
#   ${redis_stack_version}, ${redis_password}
#   ${storage_class}, ${storage_size}, ${maxmemory}
#   ${cpu_request}, ${memory_request}, ${memory_limit}
#   ${enable_servicemonitor}
# =============================================================================

architecture: standalone

# Allow non-Bitnami image (chart enforces signed Bitnami images by default).
global:
  security:
    allowInsecureImages: true

# Override default Bitnami redis image with redis-stack-server.
image:
  registry: docker.io
  repository: redis/redis-stack-server
  tag: "${redis_stack_version}"
  pullPolicy: IfNotPresent

# Chart's auth handling — password injected as a Secret + REDIS_PASSWORD env.
auth:
  enabled: true
  password: "${redis_password}"

# -----------------------------------------------------------------------------
# Master pod (the only pod in standalone mode)
# -----------------------------------------------------------------------------
master:
  service:
    type: ClusterIP
    ports:
      redis: 6379

  persistence:
    enabled: true
    storageClass: "${storage_class}"
    size: ${storage_size}
    accessModes:
      - ReadWriteOnce

  resources:
    requests:
      cpu: "${cpu_request}"
      memory: "${memory_request}"
    limits:
      memory: "${memory_limit}"

  # Bypass Bitnami's startup scripts — redis-stack-server has its own
  # entrypoint config at /etc/redis-stack.conf that loads the modules.
  command:
    - redis-server
  args:
    - /etc/redis-stack.conf
    - --requirepass
    - "${redis_password}"
    - --appendonly
    - "yes"
    - --maxmemory
    - "${maxmemory}"
    - --maxmemory-policy
    - allkeys-lru
    - --notify-keyspace-events
    - Ex
    - --protected-mode
    - "no"
    - --dir
    - /data

# Replicas disabled — standalone topology.
replica:
  replicaCount: 0

# -----------------------------------------------------------------------------
# Metrics — Bitnami chart's built-in redis_exporter sub-component
# -----------------------------------------------------------------------------
metrics:
  enabled: true
  resources:
    requests:
      cpu: 5m
      memory: 32Mi
    limits:
      memory: 48Mi
  service:
    type: ClusterIP
  serviceMonitor:
    enabled: ${enable_servicemonitor}
    labels:
      release: kube-prometheus-stack
    interval: 30s

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------
podSecurityContext:
  enabled: true
  fsGroup: 1001

containerSecurityContext:
  enabled: true
  runAsUser: 1001
  runAsNonRoot: true

# Homelab single-tenant — no NetworkPolicy.
networkPolicy:
  enabled: false

# No TLS — Redis is in-cluster only (or via Tailscale tunnel which encrypts).
tls:
  enabled: false
