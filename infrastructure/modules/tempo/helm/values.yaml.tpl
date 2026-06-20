# =============================================================================
# Tempo Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana-community/tempo v2.1.0 (appVersion 2.10.1)
# Repo:  https://grafana-community.github.io/helm-charts
#
# All interpolated variables are SCALARS (per memory feedback_yamlencode_helm_values).
#
# Mode: monolithic single-binary StatefulSet. One Pod runs ingester, querier,
# query-frontend, compactor, and metrics-generator (if enabled).
# =============================================================================

# -----------------------------------------------------------------------------
# Tempo runtime config (passed verbatim into Tempo's YAML)
# -----------------------------------------------------------------------------
tempo:
  # Tempo image — chart will pin tag to appVersion automatically.
  registry: docker.io
  repository: grafana/tempo
  pullPolicy: IfNotPresent

  multitenancyEnabled: false
  reportingEnabled: false # disable anonymous usage telemetry

  # Pod resources for the main Tempo container.
  resources:
    requests:
      cpu: ${cpu_request}
      memory: ${memory_request}
    limits:
      memory: ${memory_limit}

  # Block retention — chart's top-level `tempo.retention` field IS the source
  # of truth (gets rendered into `compactor.compaction.block_retention` via
  # the chart's config template). Setting `tempo.compactor.compaction.*`
  # directly is a no-op — chart ignores it.
  retention: ${retention_period}

  # OpenTelemetry receivers — apps and Alloy push traces here.
  # KNOWN CHART LIMITATION: the chart's Service template (`_ports.tpl`)
  # hard-references `tempo.receivers.jaeger.protocols.thrift_compact` etc.
  # to render the Service ports list. Setting `jaeger: null` breaks the
  # chart with a nil-pointer error. So we keep the chart's full default
  # receivers map (jaeger + opencensus + otlp). Tempo listens on all the
  # extra ports but no traffic flows through Jaeger/OpenCensus in practice
  # (Alloy and modern OTel SDKs all speak OTLP). Idle listeners cost ~0.
  #
  # The `enable_legacy_receivers` variable is intentionally NOT consumed
  # here for that reason — the chart forces Jaeger on regardless. Kept
  # in variables.tf so a future chart version (or distributed chart) can
  # honor it without re-introducing the variable.
  receivers:
    jaeger:
      protocols:
        grpc:
          endpoint: 0.0.0.0:14250
        thrift_binary:
          endpoint: 0.0.0.0:6832
        thrift_compact:
          endpoint: 0.0.0.0:6831
        thrift_http:
          endpoint: 0.0.0.0:14268
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
    zipkin:
      endpoint: 0.0.0.0:9411

  # Storage backend — v2 MinIO. Credentials come from the envFrom secret.
  storage:
    trace:
      backend: s3
      s3:
        bucket: ${traces_bucket}
        endpoint: ${minio_endpoint}
        insecure: true
      wal:
        path: /var/tempo/wal

  # Single-binary ring — RF 1 for homelab.
  ingester:
    lifecycler:
      ring:
        replication_factor: 1

  # Tempo metrics generator (service graphs, span metrics → Mimir). Off by
  # default to keep the homelab footprint small; when enabled it remote_writes
  # synthetic metrics into Mimir (`/api/v1/push`). Toggle later if you want
  # service-graph dashboards out of the box.
  metricsGenerator:
    enabled: false

  # Mount the MinIO creds Secret as env so Tempo's S3 client picks up
  # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY without the values ConfigMap
  # holding plaintext.
  extraEnvFrom:
    - secretRef:
        name: ${minio_credentials_secret}

# -----------------------------------------------------------------------------
# Persistence — WAL + recent blocks before S3 ship-out
# -----------------------------------------------------------------------------
persistence:
  enabled: true
  size: ${storage_size}
  storageClassName: ${storage_class}

# -----------------------------------------------------------------------------
# Service — ClusterIP only (no external Ingress per the rule)
# -----------------------------------------------------------------------------
service:
  type: ClusterIP

# -----------------------------------------------------------------------------
# ServiceMonitor — Prometheus-Operator-style scrape target
# -----------------------------------------------------------------------------
serviceMonitor:
  enabled: true
  interval: 30s
  scrapeTimeout: 10s

# -----------------------------------------------------------------------------
# tempo-query (Jaeger UI compatibility) — DISABLED, Grafana is the UI
# -----------------------------------------------------------------------------
tempoQuery:
  enabled: false
