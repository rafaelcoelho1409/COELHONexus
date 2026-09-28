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

# NOTE (ported from COELHO Cloud, 2026-09-28): this chart (charts.min.io/minio
# v5.4.0) has NO `extraEnvVars` key in its schema — the previous version of
# this file used that key and it was silently ignored by Helm (unknown values
# keys don't error). Confirmed against the upstream chart source
# (github.com/minio/minio helm/minio/values.yaml): the real key is
# `environment:` (a map, not a list). Neither GOMEMLIMIT nor
# MINIO_SCANNER_SPEED below were ever actually applied before this fix.
environment:
  GOMEMLIMIT: "${gomemlimit}"
  # Single-drive xl-single mode has no parity → healing can't repair anything,
  # and no bucket uses versioning or ILM rules → no lifecycle work to schedule.
  # `default` speed was intended to drop scanner CPU ~99% (never actually
  # active before this fix — see note above).
  MINIO_SCANNER_SPEED: "slowest"
  # Single-drive/single-node — MinIO's built-in disk-writability probe
  # (cmd/xl-storage-disk-id-check.go, ~30s threshold) periodically takes the
  # drive "offline" under local-path host I/O contention even though the
  # drive is fine (self-heals within the same 30-40s every time). With 1
  # drive there's no quorum to fall back on, so each false positive is a full
  # outage — this is exactly Nexus's topology (single-node, local-path PVC)
  # and would manifest as MinIO reads/writes hanging mid-Synth-chapter and
  # FastAPI's ensure_bucket() hanging on lifespan startup. Undocumented but
  # community-confirmed knob (github.com/minio/minio/discussions/18909).
  # Acceptable here — dev/homelab single-node instance, no other drives to
  # protect via failover anyway.
  _MINIO_DRIVE_ACTIVE_MONITORING: "off"

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
