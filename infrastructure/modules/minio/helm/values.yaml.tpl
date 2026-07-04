# =============================================================================
# MinIO Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: https://charts.min.io/ minio v5.4.0 (archived 2026-04-25 but still
# pulls cleanly). API surface differs from Bitnami's chart — see notes below.
#
# Variables interpolated:
#   ${root_user}, ${root_password}
#   ${storage_class}, ${storage_size}
#   ${cpu_request}, ${memory_request}, ${memory_limit}, ${gomemlimit}
#   ${replicas}
# Default buckets are inlined as literal YAML below — NOT interpolated.
#
# API notes (charts.min.io vs Bitnami):
#   - rootUser / rootPassword at top level (NOT auth.rootUser/rootPassword)
#   - buckets is a YAML list of objects (NOT comma-separated string)
#   - service is API (port 9000); consoleService is UI (port 9001) — TWO Services
#   - ServiceMonitor template missing — we create it manually in main.tf
#   - Pod labels are legacy `app=minio` (NOT app.kubernetes.io/name=...)
#     This is why ingress annotations include gethomepage.dev/pod-selector.
# =============================================================================

# Standalone mode — 1 pod, 1 PVC. Switch to "distributed" for HA (≥4 nodes).
mode: standalone

# Pod count. Standalone honors this only if =1.
replicas: ${replicas}

# -----------------------------------------------------------------------------
# Auth — credentials injected by templatefile() from SOPS, never written
# unencrypted to disk. Chart writes them to a Secret on apply.
# -----------------------------------------------------------------------------
rootUser: "${root_user}"
rootPassword: "${root_password}"

# -----------------------------------------------------------------------------
# Service — TWO Services (API on 9000, Console on 9001). External Ingresses
# point at the appropriate one. ClusterIP only (external ingress controller handles external).
# -----------------------------------------------------------------------------
service:
  type: ClusterIP
  port: 9000

consoleService:
  type: ClusterIP
  port: 9001

# Disable chart's built-in Ingress; external Ingresses are created
# separately via kubernetes_manifest in main.tf.
ingress:
  enabled: false

consoleIngress:
  enabled: false

# -----------------------------------------------------------------------------
# Persistence — single PVC for standalone mode. v2 starts at 15Gi.
# local-path provisioner uses host path mounted from data_path.
# -----------------------------------------------------------------------------
persistence:
  enabled: true
  storageClass: "${storage_class}"
  size: ${storage_size}
  accessMode: ReadWriteOnce

# -----------------------------------------------------------------------------
# Resources — kept from v1's measured tuning (real usage 2m CPU, 145Mi RAM).
# GOMEMLIMIT lets Go GC stay efficient under the memory limit.
# -----------------------------------------------------------------------------
resources:
  requests:
    cpu: "${cpu_request}"
    memory: "${memory_request}"
  limits:
    memory: "${memory_limit}"

extraEnvVars:
  - name: GOMEMLIMIT
    value: "${gomemlimit}"
  # Single-drive xl-single mode has no parity → healing can't repair anything,
  # and no bucket uses versioning or ILM rules → no lifecycle work to schedule.
  # `default` speed was burning 2.65 cores on pure bit-rot detection. `slowest`
  # drops scanner CPU ~99%. Re-evaluate if lifecycle rules are ever added.
  - name: MINIO_SCANNER_SPEED
    value: "slowest"

# -----------------------------------------------------------------------------
# Security context — non-root, fsGroup for PVC ownership.
# -----------------------------------------------------------------------------
securityContext:
  enabled: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000

# -----------------------------------------------------------------------------
# Metrics — chart 5.4.0 doesn't ship a ServiceMonitor template even with
# this flag. We create it manually in main.tf via kubernetes_manifest.
# -----------------------------------------------------------------------------
metrics:
  serviceMonitor:
    enabled: false

# -----------------------------------------------------------------------------
# Default buckets — created on first install, idempotent on re-apply.
# Literal YAML (NOT templatefile()-interpolated). Avoids the yamlencode bug
# where multi-line list output got merged in a way Helm's parser dropped
# everything else in the values file (resulting in chart defaults taking
# over: mode=distributed, replicas=16). Verified 2026-05-02.
# -----------------------------------------------------------------------------
buckets:
  - name: backups
    policy: none
    purge: false

# No additional policies or users at install time. Manage via console or mc admin.
policies: []
users: []

# Service Account
serviceAccount:
  create: true
  name: "minio-sa"
