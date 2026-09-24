# =============================================================================
# alloy module — Grafana Alloy (LGTM telemetry collector)
# =============================================================================
#
# Deploys:
#   1. alloy namespace (shared by both releases below)
#   2. helm_release.alloy — metrics + OTLP gateway (chart 1.8.0, appVersion
#      v1.16.0):
#        - controller.type: deployment, replicas: 1 — inherently cluster-wide
#          from 1 pod (no per-node state)
#        - Inline River config defining 3 pipelines:
#            - OTLP receiver (gRPC 4317 + HTTP 4318) → fan-out to Tempo / Mimir / Loki
#            - prometheus.operator.servicemonitors / podmonitors → Mimir
#            - self-scrape Alloy's own /metrics → Mimir
#        - ServiceMonitor on for Prometheus-Operator-style discovery
#        - extraPorts for OTLP (chart's Service template doesn't include them
#          by default since OTLP is config-driven, not chart-config-driven)
#   3. helm_release.alloy_logs — Kubernetes pod log tailing (same chart, own
#      release), as a DaemonSet with file-based tailing (`loki.source.file`
#      over a host `/var/log` mount). Split out from helm_release.alloy
#      2026-09-23 (ported from the COELHO Cloud fix): that release's
#      PREVIOUS log pipeline used `loki.source.kubernetes` (API-based
#      tailing), which leaks retrying tailers for deleted pods under
#      frequent redeploys — confirmed no upstream fix through Alloy
#      v1.20.0-rc.0 (grafana/alloy#7192, #3829). The leak CPU-starves the
#      whole Deployment, including its OTLP receiver. File-based tailing
#      doesn't have this failure mode: fsnotify just drops the watch when
#      kubelet removes the log file.
#
# No external Ingress / Homepage tile (per memory:
# feedback_no_external_ingress_for_uiless_backends). For external apps that
# need to push telemetry from outside the cluster, expose later via a
# separate Ingress + LoadBalancer (gRPC) — out of scope for this initial
# install.
#
# Alloy IS the chicken-and-egg starter: its own ServiceMonitor exists but
# Alloy is the thing that scrapes ServiceMonitors. The inline config also
# does a self-scrape directly via prometheus.scrape, so the metrics flow
# even before its own ServiceMonitor is reconciled.
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "alloy" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "alloy"
      "app.kubernetes.io/component"  = "telemetry-collector"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Helm release — grafana/alloy
# -----------------------------------------------------------------------------
resource "helm_release" "alloy" {
  name       = var.release_name
  repository = "https://grafana.github.io/helm-charts"
  chart      = "alloy"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.alloy.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      cluster_label            = var.cluster_label
      mimir_remote_write_url   = var.mimir_remote_write_url
      loki_push_url            = var.loki_push_url
      tempo_otlp_grpc_endpoint = var.tempo_otlp_grpc_endpoint

      cpu_request    = var.cpu_request
      memory_request = var.memory_request
      memory_limit   = var.memory_limit

      # Tier 1 (2026-05-25) — see docs/alloy_optimization.md
      alloy_image_tag            = var.alloy_image_tag
      alloy_gomemlimit           = var.alloy_gomemlimit
      alloy_gogc                 = var.alloy_gogc
      alloy_enable_otlp_receiver = var.alloy_enable_otlp_receiver
    })
  ]

  wait    = true
  timeout = 600

  depends_on = [kubernetes_namespace_v1.alloy]
}

# -----------------------------------------------------------------------------
# Helm release — grafana/alloy, log-tailing DaemonSet (see module header)
# -----------------------------------------------------------------------------
resource "helm_release" "alloy_logs" {
  name       = var.logs_release_name
  repository = "https://grafana.github.io/helm-charts"
  chart      = "alloy"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.alloy.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values-logs.yaml.tpl", {
      cluster_label                = var.cluster_label
      loki_push_url                = var.loki_push_url
      alloy_log_namespace_denylist = var.alloy_log_namespace_denylist

      cpu_request    = var.logs_cpu_request
      memory_request = var.logs_memory_request
      memory_limit   = var.logs_memory_limit

      alloy_image_tag  = var.alloy_image_tag
      alloy_gomemlimit = var.logs_gomemlimit
      alloy_gogc       = var.alloy_gogc
    })
  ]

  wait    = true
  timeout = 600

  depends_on = [kubernetes_namespace_v1.alloy]
}

# -----------------------------------------------------------------------------
# Extra ClusterRole — kubelet/cAdvisor scrape access
# -----------------------------------------------------------------------------
# Chart's default rbac.rules don't include nodes/nodes/proxy. Without these,
# our prometheus.scrape.kubelet{,_cadvisor} scrapes get 403 from the API
# server when proxying to kubelet, and Mimir gets no container_* metrics.
# Added as a separate ClusterRole+Binding rather than overriding the chart's
# rules list (avoids Helm map-replace footgun).
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "clusterrole_kubelet" {
  manifest = yamldecode(templatefile("${path.module}/k8s/clusterrole-kubelet.yaml.tpl", {
    name = "${var.release_name}-kubelet-scrape"
  }))

  depends_on = [helm_release.alloy]
}

resource "kubernetes_manifest" "clusterrolebinding_kubelet" {
  manifest = yamldecode(templatefile("${path.module}/k8s/clusterrolebinding-kubelet.yaml.tpl", {
    name                 = "${var.release_name}-kubelet-scrape"
    role_name            = "${var.release_name}-kubelet-scrape"
    service_account_name = var.release_name # chart's default SA name = release name
    namespace            = kubernetes_namespace_v1.alloy.metadata[0].name
  }))

  depends_on = [kubernetes_manifest.clusterrole_kubelet]
}
