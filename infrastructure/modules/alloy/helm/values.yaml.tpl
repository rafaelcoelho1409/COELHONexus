# =============================================================================
# Alloy Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana/alloy v1.8.0 (appVersion v1.16.0 chart default; we pin image
# to v1.16.1 below for CVE fixes)
# Repo:  https://grafana.github.io/helm-charts
#
# Variables interpolated as SCALARS (per memory feedback_yamlencode_helm_values):
#   cluster_label, mimir_remote_write_url, loki_push_url,
#   tempo_otlp_grpc_endpoint, cpu_request, memory_request, memory_limit,
#   alloy_image_tag, alloy_gomemlimit, alloy_gogc,
#   alloy_log_namespaces_json (jsonencode'd list), alloy_enable_otlp_receiver (bool).
#
# The alloy.configMap.content block holds Alloy's River config; templatefile
# interpolation runs over the whole file, so $${...} escape stays as a
# literal dollar-curly while $${var} resolves to the variable.
#
# === Memory layout (Tier 1, applied 2026-05-25) ===========================
# Previously: `resources:` at TOP LEVEL of this file → chart SILENTLY DROPPED
# it (correct path is `alloy.resources`). Pod ran in BestEffort QoS with no
# memory budget signal → Go runtime never trimmed → 1.18 GiB unbounded RSS.
# Fix: moved under `alloy.resources` + GOMEMLIMIT env + namespace allowlist
# + WAL trim + OTLP receiver toggle. See docs/alloy_optimization.md.
# =============================================================================

# Chart's CRDs section installs PodLogs CRDs etc. We use Prometheus-Operator
# CRDs from the monitoring-crds module instead — keep the alloy chart's CRDs
# off to avoid duplicate installs.
crds:
  create: false

# Image pin — TOP-LEVEL key per chart schema (line 143 of upstream values.yaml).
# NOT under `alloy:` (silently dropped — verified by deploy showing v1.16.0
# despite setting alloy.image.tag=v1.16.1 in first attempt).
image:
  registry: docker.io
  repository: grafana/alloy
  tag: "${alloy_image_tag}"
  pullPolicy: IfNotPresent

alloy:
  # Pin to GA stability — `experimental` enables livedebugging + extra components
  # with their own in-memory ring buffers; we don't need that on this homelab.
  stabilityLevel: generally-available

  # Single replica: clustering is gossip overhead with no HA value at this scale.
  clustering:
    enabled: false

  # NOTE: image config is at TOP LEVEL of this file (see below the `alloy:` block).
  # Putting `image:` here under `alloy:` is silently dropped by the chart — same
  # bug class as the original `resources:` mistake. Verified against chart schema
  # values.yaml line 143 (top-level `image:`).

  # Resources — MUST live under `alloy:` (chart schema path).
  # Burstable QoS (request < limit) gives the JVM... wait no, Alloy is Go.
  # GOMEMLIMIT env (below) tells the Go runtime to GC aggressively as RSS
  # approaches the cgroup limit, so the hard limit becomes a soft target.
  resources:
    requests:
      cpu: "${cpu_request}"
      memory: "${memory_request}"
    limits:
      memory: "${memory_limit}"

  # Go runtime memory pressure — was missing entirely; cause of the 1.18 GiB.
  extraEnv:
    - name: GOMEMLIMIT
      value: "${alloy_gomemlimit}"
    - name: GOGC
      value: "${alloy_gogc}"

  # OTLP-receiver ports exposed by the Service. The OTLP listeners themselves
  # are configured by the River config below; this just opens the Service ports.
  # Harmless to leave when OTLP is disabled (Service port exists but unbacked).
  extraPorts:
    - name: otlp-grpc
      port: 4317
      targetPort: 4317
      protocol: TCP
    - name: otlp-http
      port: 4318
      targetPort: 4318
      protocol: TCP

  configMap:
    create: true
    content: |
      // ====================================================================
      // Alloy River config — metrics + OTLP gateway (alloy-metrics instance)
      // ====================================================================
      // Pipelines:
      //   1. ServiceMonitor + PodMonitor scraping → Mimir
      //   2. Kubelet + cAdvisor scraping (with relabel drop of high-card series) → Mimir
      //   3. Alloy self-scrape → Mimir
      //   4. (CONDITIONAL) OTLP gRPC/HTTP receiver → batch → Tempo/Mimir/Loki
      //
      // Kubernetes pod log tailing lives in the SEPARATE `alloy-logs` DaemonSet
      // release (helm_release.alloy_logs in main.tf) — split out because
      // `loki.source.kubernetes` (API-based tailing) never garbage-collects
      // tailers for deleted pods, so under frequent redeploys zombie tailer
      // retries accumulate until this Deployment CPU-starves and fails its
      // own readiness probe — taking the OTLP receiver below down with it
      // (Service loses all ready endpoints). alloy-logs uses file-based
      // tailing instead (fsnotify drops a watch cleanly when the log file is
      // removed — no API-retry loop). Ported from the COELHO Cloud fix,
      // 2026-09-23 (see that repo's docs/alloy_optimization.md).
      // ====================================================================

      logging {
        level  = "info"
        format = "logfmt"
      }

      // ----- ALWAYS-ON: remote_write to Mimir (used by ALL scrape paths) ---
      // Tuned for in-cluster Mimir (sub-ms latency): smaller queue + shorter WAL.
      prometheus.remote_write "mimir" {
        endpoint {
          url = "${mimir_remote_write_url}"

          queue_config {
            capacity              = 2500   // default 10000
            max_shards            = 10     // default 50
            min_shards            = 1
            max_samples_per_send  = 500    // default 2000
            batch_send_deadline   = "5s"
            // Fail fast on stale samples (ported from COELHO Cloud's
            // 2026-09-05 fix: WAL-replay ancients got 400
            // err-mimir-sample-timestamp-too-old + EOF retry storms).
            // Homelab-style deployments prefer gaps over retry spirals.
            sample_age_limit      = "10m"
          }
        }

        // WAL — Mimir is in-cluster so multi-hour retention is wasted RAM.
        wal {
          truncate_frequency = "15m"       // default 2h
          min_keepalive_time = "1h"
          max_keepalive_time = "2h"        // default 8h
        }

        external_labels = {
          cluster = "${cluster_label}",
        }
      }

      // ----- ALWAYS-ON: write to Loki (used by pod log tailing) ------------
      loki.write "default" {
        endpoint {
          url = "${loki_push_url}"
        }
        external_labels = {
          cluster = "${cluster_label}",
        }
      }

%{ if alloy_enable_otlp_receiver ~}
      // ====================================================================
      // OTLP RECEPTION (enabled — set var.alloy_enable_otlp_receiver=false
      // to remove this block entirely and save ~30-50 MiB of receiver buffer pools).
      // ====================================================================
      otelcol.receiver.otlp "default" {
        grpc {
          endpoint = "0.0.0.0:4317"
        }
        http {
          endpoint = "0.0.0.0:4318"
          cors {
            allowed_origins = ["*"]
          }
        }

        output {
          traces  = [otelcol.processor.k8sattributes.otlp.input]
          metrics = [otelcol.processor.k8sattributes.otlp.input]
          logs    = [otelcol.processor.k8sattributes.otlp.input]
        }
      }

      otelcol.processor.k8sattributes "otlp" {
        extract {
          metadata = [
            "k8s.namespace.name",
            "k8s.pod.name",
            "k8s.node.name",
            "k8s.deployment.name",
            "k8s.statefulset.name",
            "service.name",
            "service.namespace",
            "service.version",
          ]
          deployment_name_from_replicaset = true
        }

        output {
          traces = [
            otelcol.connector.spanmetrics.default.input,
            otelcol.connector.servicegraph.default.input,
            otelcol.processor.batch.traces.input,
          ]
          metrics = [otelcol.processor.batch.metrics.input]
          logs    = [otelcol.processor.batch.logs.input]
        }
      }

      otelcol.connector.spanmetrics "default" {
        aggregation_temporality       = "DELTA"
        aggregation_cardinality_limit = 10000
        resource_metrics_key_attributes = [
          "service.name",
          "service.namespace",
          "deployment.environment",
        ]

        dimension {
          name = "deployment.environment"
          default = "unknown"
        }

        dimension {
          name = "service.namespace"
          default = "default"
        }

        histogram {
          explicit {
            buckets = ["5ms", "10ms", "25ms", "50ms", "100ms", "250ms", "500ms", "1s", "2s", "5s", "10s", "30s"]
          }
        }

        exemplars {
          enabled = true
        }

        output {
          metrics = [otelcol.exporter.prometheus.mimir.input]
        }
      }

      otelcol.connector.servicegraph "default" {
        dimensions = ["deployment.environment", "k8s.namespace.name"]

        store {
          ttl = "10s"
          max_items = 5000
        }

        output {
          metrics = [otelcol.exporter.prometheus.mimir.input]
        }
      }

      // ----- OTLP TRACES → Tempo -------------------------------------------
      otelcol.processor.batch "traces" {
        timeout             = "2s"
        send_batch_size     = 1000
        send_batch_max_size = 2000
        output {
          traces = [otelcol.exporter.otlp.tempo.input]
        }
      }

      otelcol.exporter.otlp "tempo" {
        client {
          endpoint = "${tempo_otlp_grpc_endpoint}"
          tls {
            insecure = true
          }
        }

        // Bound the blast radius of a slow Tempo (ported from COELHO Cloud's
        // 2026-09-05 fix: endless DeadlineExceeded retries → queue growth →
        // OOMKill). Drop after ~2m instead of retrying forever; keep the
        // queue small (docs warn a very high queue_size causes OOM kills).
        retry_on_failure {
          enabled           = true
          initial_interval  = "5s"
          max_interval      = "30s"
          max_elapsed_time  = "2m"
        }

        sending_queue {
          enabled       = true
          num_consumers = 2
          queue_size    = 100
        }
      }

      // ----- OTLP METRICS → Mimir (via the unconditional remote_write above)
      otelcol.processor.batch "metrics" {
        timeout             = "5s"
        send_batch_size     = 2000
        send_batch_max_size = 4000
        output {
          metrics = [otelcol.exporter.prometheus.mimir.input]
        }
      }

      otelcol.exporter.prometheus "mimir" {
        forward_to          = [prometheus.remote_write.mimir.receiver]
        add_metric_suffixes = true
      }

      // ----- OTLP LOGS → Loki (via the unconditional loki.write above) -----
      otelcol.processor.batch "logs" {
        timeout             = "2s"
        send_batch_size     = 1000
        send_batch_max_size = 2000
        output {
          logs = [otelcol.exporter.loki.default.input]
        }
      }

      otelcol.exporter.loki "default" {
        forward_to = [loki.write.default.receiver]
      }
%{ endif ~}

      // ====================================================================
      // PROMETHEUS-OPERATOR DISCOVERY
      // ====================================================================
      // Scrape every ServiceMonitor + PodMonitor in the cluster. The CR-level
      // namespace selectors on each SM/PM still apply, so this stays cheap.
      prometheus.operator.servicemonitors "default" {
        forward_to = [prometheus.remote_write.mimir.receiver]
        namespaces = []
      }

      prometheus.operator.podmonitors "default" {
        forward_to = [prometheus.remote_write.mimir.receiver]
        namespaces = []
      }

      // ====================================================================
      // KUBELET cAdvisor SCRAPING (with high-card label/series drops)
      // ====================================================================
      discovery.kubernetes "nodes" {
        role = "node"
      }

      discovery.relabel "kubelet_cadvisor" {
        targets = discovery.kubernetes.nodes.targets
        rule {
          target_label = "__address__"
          replacement  = "kubernetes.default.svc.cluster.local:443"
        }
        rule {
          source_labels = ["__meta_kubernetes_node_name"]
          regex         = "(.+)"
          target_label  = "__metrics_path__"
          replacement   = "/api/v1/nodes/$${1}/proxy/metrics/cadvisor"
        }
        rule {
          source_labels = ["__meta_kubernetes_node_name"]
          target_label  = "node"
        }
      }

      prometheus.scrape "kubelet_cadvisor" {
        targets         = discovery.relabel.kubelet_cadvisor.output
        forward_to      = [prometheus.relabel.cadvisor.receiver]
        scheme          = "https"
        scrape_interval = "60s"
        bearer_token_file = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        tls_config {
          ca_file              = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
          insecure_skip_verify = true
        }
        job_name = "kubelet-cadvisor"
      }

      // cAdvisor cardinality killer — drop high-card per-pod-per-interface
      // network series + useless labels. Saves both Alloy WAL memory AND
      // Mimir ingester memory downstream.
      prometheus.relabel "cadvisor" {
        forward_to = [prometheus.remote_write.mimir.receiver]
        rule {
          source_labels = ["__name__"]
          // Extended with container_blkio_.*|container_sockets 2026-09-23,
          // ported from COELHO Cloud's 2026-09-05 fix (blkio series with
          // device/major/minor labels seen rejected downstream).
          regex         = "container_network_.*|container_tasks_state|container_memory_failures_total|container_fs_(reads|writes)_(bytes_)?total|container_blkio_.*|container_sockets"
          action        = "drop"
        }
        // IMPORTANT (fixed 2026-05-29): do NOT add `id` back to this labeldrop.
        // `id` (cgroup path) is cAdvisor's unique per-series key. Dropping it
        // collapses the pod-root and pause/sandbox cgroup series (both with an
        // empty container label, differing only by id/name) into one; cAdvisor
        // stamps each with its own timestamp, so the merged series triggers
        // err-mimir-sample-out-of-order in Mimir (HTTP 400, ~500 samples/batch
        // every 60s). Keep `id` so every cgroup series stays distinct.
        rule {
          regex  = "image|name|pod_uid"
          action = "labeldrop"
        }
      }

      // Companion: kubelet's own /metrics endpoint (workqueue depths, runtime
      // stats). Cheap, useful for cluster-health dashboards.
      discovery.relabel "kubelet" {
        targets = discovery.kubernetes.nodes.targets
        rule {
          target_label = "__address__"
          replacement  = "kubernetes.default.svc.cluster.local:443"
        }
        rule {
          source_labels = ["__meta_kubernetes_node_name"]
          regex         = "(.+)"
          target_label  = "__metrics_path__"
          replacement   = "/api/v1/nodes/$${1}/proxy/metrics"
        }
        rule {
          source_labels = ["__meta_kubernetes_node_name"]
          target_label  = "node"
        }
      }

      prometheus.scrape "kubelet" {
        targets         = discovery.relabel.kubelet.output
        forward_to      = [prometheus.remote_write.mimir.receiver]
        scheme          = "https"
        scrape_interval = "60s"
        bearer_token_file = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        tls_config {
          ca_file              = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
          insecure_skip_verify = true
        }
        job_name = "kubelet"
      }

      // ====================================================================
      // ALLOY SELF-SCRAPE
      // ====================================================================
      prometheus.scrape "alloy_self" {
        targets = [{
          __address__ = "localhost:12345",
          job         = "alloy",
        }]
        forward_to = [prometheus.remote_write.mimir.receiver]
      }

# -----------------------------------------------------------------------------
# Deployment mode — single replica. This instance no longer tails pod logs
# (moved to the alloy-logs DaemonSet, see River config header above), so it
# has no reason to run per-node: OTLP receipt, ServiceMonitor/PodMonitor
# discovery and cAdvisor scraping are all inherently cluster-wide from 1 pod.
# -----------------------------------------------------------------------------
controller:
  type: deployment
  replicas: 1
  # Toleration so Alloy can schedule on the control-plane node too (k3d default
  # has no taint here, but keeps us safe if the user adds one later).
  tolerations:
    - operator: Exists

# -----------------------------------------------------------------------------
# Service — ClusterIP. OTLP listeners are added via alloy.extraPorts above.
# -----------------------------------------------------------------------------
service:
  enabled: true
  type: ClusterIP

# -----------------------------------------------------------------------------
# ServiceMonitor — Alloy publishes its own /metrics on port 12345 (built-in
# debug + flow stats). Mimir/Alloy can scrape it via this CR.
# -----------------------------------------------------------------------------
serviceMonitor:
  enabled: true
  interval: 30s
  metricRelabelings: []

# -----------------------------------------------------------------------------
# RBAC — chart creates ClusterRole + Binding for Alloy to read
# Pods/Endpoints/ServiceMonitors/PodMonitors. Required.
# -----------------------------------------------------------------------------
rbac:
  create: true
