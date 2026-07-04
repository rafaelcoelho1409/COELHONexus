# =============================================================================
# Grafana Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana-community/grafana v12.3.0 (appVersion 13.0.1)
# Repo:  https://grafana-community.github.io/helm-charts
#
# Variables interpolated (all SCALARS — per memory feedback_yamlencode_helm_values):
#   ${admin_secret_name}, ${db_secret_name}
#   ${postgres_host}, ${postgres_port}, ${grafana_db_name}, ${grafana_db_user}
#   ${replicas}, ${cpu_request}, ${memory_request}, ${memory_limit}
#   ${persistence_enabled}, ${storage_class}, ${storage_size}
#   ${sidecar_search_namespace}, ${service_monitor_enabled}, ${domain}
#
# v2 baseline DBs (per memory feedback_default_to_v2_baseline_dbs):
#   - DATABASE_URL → v2 Postgres (chart's `grafana.ini.database` block)
#   - No bundled DB sub-chart to disable (Grafana chart doesn't ship one)
# =============================================================================

# Pod count. Postgres backend would allow >1 replica, but homelab keeps 1.
replicas: ${replicas}

# Use the chart's appVersion-pinned image (do NOT override tag).
image:
  repository: grafana/grafana
  pullPolicy: IfNotPresent

# -----------------------------------------------------------------------------
# Admin user — sourced from a Secret created by Terraform
# -----------------------------------------------------------------------------
# `existingSecret` makes the chart wire `GF_SECURITY_ADMIN_USER` /
# `GF_SECURITY_ADMIN_PASSWORD` env vars from the named Secret. Avoids putting
# the password in the rendered values ConfigMap.
# -----------------------------------------------------------------------------
admin:
  existingSecret: "${admin_secret_name}"
  userKey: admin-user
  passwordKey: admin-password

# -----------------------------------------------------------------------------
# DB password — surfaced as $GF_DATABASE_PASSWORD env var inside the pod
# -----------------------------------------------------------------------------
# `grafana.ini.database.password` references this env var via Grafana's
# `$__env{NAME}` substitution syntax — keeps the password out of the rendered
# config ConfigMap and out of `kubectl describe`.
# -----------------------------------------------------------------------------
envFromSecrets:
  - name: ${db_secret_name}
    optional: false

# -----------------------------------------------------------------------------
# Grafana INI — server + database backend + alerting
# -----------------------------------------------------------------------------
grafana.ini:
  server:
    domain: "${domain}"
    root_url: "${root_url}"
    serve_from_sub_path: false
  database:
    type: postgres
    host: "${postgres_host}:${postgres_port}"
    name: "${grafana_db_name}"
    user: "${grafana_db_user}"
    password: $__env{GF_DATABASE_PASSWORD}
    ssl_mode: disable
  analytics:
    check_for_updates: true
    reporting_enabled: false
  log:
    mode: console
    level: info
  auth.anonymous:
    enabled: false
  # Unified alerting (replaces legacy Alertmanager). On by default in Grafana
  # 9+, but pinning explicitly so future chart defaults can't flip it.
  unified_alerting:
    enabled: true
  alerting:
    enabled: false

# -----------------------------------------------------------------------------
# Service — ClusterIP. External Ingress provides external access.
# -----------------------------------------------------------------------------
service:
  enabled: true
  type: ClusterIP
  port: 80
  targetPort: 3000

# -----------------------------------------------------------------------------
# Ingress — chart's built-in is DISABLED. We create a custom external-facing
# Ingress separately via kubernetes_manifest in main.tf.
# -----------------------------------------------------------------------------
ingress:
  enabled: false

# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------
# Disabled by default (per variables.tf default). When DB is external, only
# plugins + BLEVE search index would land here, both regeneratable on restart.
# -----------------------------------------------------------------------------
persistence:
  type: pvc
  enabled: ${persistence_enabled}
  storageClassName: "${storage_class}"
  size: ${storage_size}
  accessModes:
    - ReadWriteOnce

# Skip the chmod-on-PVC init container (we run as fsGroup, no chown needed).
initChownData:
  enabled: false

# -----------------------------------------------------------------------------
# Resources
# -----------------------------------------------------------------------------
resources:
  requests:
    cpu: "${cpu_request}"
    memory: "${memory_request}"
  limits:
    memory: "${memory_limit}"

# -----------------------------------------------------------------------------
# Sidecar — auto-discovery of datasources + dashboards
# -----------------------------------------------------------------------------
# This is the integration contract for the LGTM stack:
#   - Mimir/Loki/Tempo modules each create a ConfigMap with label
#     `grafana_datasource: "1"`. The sidecar imports it on the fly.
#   - Dashboards modules create ConfigMaps with `grafana_dashboard: "1"`.
#
# `searchNamespace: ALL` lets us split datasources/dashboards across their
# own namespaces (mimir/, loki/, etc.) instead of dumping them in grafana/.
# -----------------------------------------------------------------------------
sidecar:
  datasources:
    enabled: true
    searchNamespace: ${sidecar_search_namespace}
    label: grafana_datasource
    labelValue: "1"
    # initDatasources MUST stay off: when true, the chart spawns an init
    # container running k8s-sidecar with METHOD=WATCH (inherited from
    # watchMethod below) — but k8s-sidecar in WATCH mode is a long-running
    # process that never exits, so the Pod gets stuck in PodInitializing
    # forever. The regular sidecar in WATCH mode picks up datasource
    # ConfigMaps as Mimir/Loki/Tempo register them at runtime; no boot-time
    # preload is needed since the first apply has zero datasource CMs anyway.
    initDatasources: false
    watchMethod: WATCH
    skipReload: false
  dashboards:
    enabled: true
    searchNamespace: ${sidecar_search_namespace}
    label: grafana_dashboard
    labelValue: "1"
    folderAnnotation: grafana_folder
    provider:
      foldersFromFilesStructure: true
    skipReload: true
  alerts:
    enabled: false
  notifiers:
    enabled: false
  plugins:
    enabled: false

# -----------------------------------------------------------------------------
# ServiceMonitor — Prometheus-Operator-style scrape target for Alloy/Mimir
# -----------------------------------------------------------------------------
# Costs nothing if no scraper is online yet (CR sits idle). When the LGTM
# stack comes up, scrape Grafana's own /metrics for self-observability.
# -----------------------------------------------------------------------------
serviceMonitor:
  enabled: ${service_monitor_enabled}
  interval: 30s
  scrapeTimeout: 10s

# -----------------------------------------------------------------------------
# Service Account
# -----------------------------------------------------------------------------
serviceAccount:
  create: true

# -----------------------------------------------------------------------------
# Security — non-root, drop everything we don't need
# -----------------------------------------------------------------------------
podSecurityContext:
  fsGroup: 472
  runAsGroup: 472
  runAsNonRoot: true
  runAsUser: 472

containerSecurityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault

# -----------------------------------------------------------------------------
# Probes — chart defaults are sane. Do NOT override partially (Helm replaces
# the whole map; per memory feedback_helm_partial_map_override).
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Test pod — disabled (no value in homelab; just bloats `helm install`)
# -----------------------------------------------------------------------------
testFramework:
  enabled: false
