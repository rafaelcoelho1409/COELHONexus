# =============================================================================
# alloy module — Grafana Alloy (LGTM telemetry collector)
# =============================================================================
#
# Deploys:
#   1. alloy namespace
#   2. grafana/alloy Helm release (chart 1.8.0, appVersion v1.16.0):
#        - controller.type: deployment, replicas: 1 (homelab — DaemonSet would
#          mean N pods on N nodes; loki.source.kubernetes goes through the
#          Kubelet API so a single Deployment can collect cluster-wide logs)
#        - Inline River config defining 4 pipelines:
#            - OTLP receiver (gRPC 4317 + HTTP 4318) → fan-out to Tempo / Mimir / Loki
#            - K8s pod log discovery + tail → Loki
#            - prometheus.operator.servicemonitors / podmonitors → Mimir
#            - self-scrape Alloy's own /metrics → Mimir
#        - ServiceMonitor on for Prometheus-Operator-style discovery
#        - extraPorts for OTLP (chart's Service template doesn't include them
#          by default since OTLP is config-driven, not chart-config-driven)
#
# No external Tailscale Ingress / Homepage tile (per memory:
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
      alloy_log_namespaces_json  = jsonencode(var.alloy_log_namespaces)
      alloy_enable_otlp_receiver = var.alloy_enable_otlp_receiver
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

# -----------------------------------------------------------------------------
# Tailscale Ingress — OTLP HTTP (port 4318) only
# -----------------------------------------------------------------------------
# Why HTTP-only (no gRPC Ingress):
#   - OTLP HTTP works fine through Tailscale's HTTPS-terminating proxy.
#   - OTLP gRPC needs HTTP/2 streaming end-to-end which Tailscale Ingress'
#     operator-managed proxy doesn't pass cleanly. If gRPC ingest from
#     outside the cluster becomes a real need, add a separate Service with
#     `loadBalancerClass: tailscale` for raw TCP passthrough on 4317 (same
#     pattern v2 Postgres uses).
#
# No Homepage tile — Alloy is a data ingest endpoint, not a UI (per memory:
# feedback_no_external_ingress_for_uiless_backends; this is the documented
# "external client need" exception).
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "ingress" {
  count = var.expose_otlp_http_via_tailscale && var.tailscale_domain != "" ? 1 : 0

  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.alloy.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
    ingress_class_name = var.tailscale_ingress_class
  }))

  depends_on = [helm_release.alloy]
}
