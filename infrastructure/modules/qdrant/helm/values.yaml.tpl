# =============================================================================
# Qdrant Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: qdrant/qdrant v1.17.1 (appVersion v1.17.1)
# Repo:  https://qdrant.github.io/qdrant-helm
#
# All variables interpolated as SCALARS (per memory feedback_yamlencode_helm_values).
# =============================================================================

replicaCount: ${replicas}

# -----------------------------------------------------------------------------
# Persistence — separate PVCs for data + snapshots
# -----------------------------------------------------------------------------
persistence:
  size: ${storage_size}
  storageClassName: ${storage_class}
  accessModes:
    - ReadWriteOnce

snapshotPersistence:
  enabled: true
  size: ${snapshot_storage_size}
  storageClassName: ${storage_class}

# -----------------------------------------------------------------------------
# Resources
# -----------------------------------------------------------------------------
resources:
  requests:
    cpu: ${cpu_request}
    memory: ${memory_request}
  limits:
    memory: ${memory_limit}

# -----------------------------------------------------------------------------
# Service — ClusterIP. External Ingress provides external access.
# -----------------------------------------------------------------------------
service:
  type: ClusterIP

# -----------------------------------------------------------------------------
# Qdrant runtime config
# -----------------------------------------------------------------------------
config:
  cluster:
    enabled: false # single-replica, no cluster

# -----------------------------------------------------------------------------
# API key — chart reads from a pre-existing Secret (key `api-key`).
# Use the chart's documented `apiKey.valueFrom.secretKeyRef` shape so the
# runtime key is deterministic and matches the app-side `QDRANT_API_KEY`.
# -----------------------------------------------------------------------------
apiKey:
  valueFrom:
    secretKeyRef:
      name: ${api_key_secret}
      key: api-key

# -----------------------------------------------------------------------------
# ServiceMonitor — Alloy auto-discovers via prometheus.operator
# -----------------------------------------------------------------------------
metrics:
  serviceMonitor:
    enabled: ${service_monitor_enabled}
    scrapeInterval: 30s
    scrapeTimeout: 10s

# -----------------------------------------------------------------------------
# Security — non-root, fsGroup 1000 (chart default user)
# -----------------------------------------------------------------------------
podSecurityContext:
  fsGroup: 1000

containerSecurityContext:
  runAsUser: 1000
  runAsNonRoot: true

# -----------------------------------------------------------------------------
# Probes — chart defaults are sane; v1 customized timing slightly. Setting
# the FULL block (not partial — per memory feedback_helm_partial_map_override).
# -----------------------------------------------------------------------------
livenessProbe:
  enabled: true
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 6
  successThreshold: 1

readinessProbe:
  enabled: true
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 6
  successThreshold: 1
