# =============================================================================
# monitoring-crds module — Prometheus Operator CRDs (CRDs only, no controller)
# =============================================================================
#
# What this module installs:
#   The CRD definitions used by metrics-emitting workloads to declare scrape
#   targets ("kind: ServiceMonitor", "kind: PodMonitor", etc.).
#
# What this module does NOT install:
#   - Prometheus itself (we use Mimir for long-term metrics storage)
#   - The prometheus-operator controller (we use Alloy as the collector)
#   - Alertmanager
#
# CRDs installed by the chart (~10):
#   ServiceMonitor, PodMonitor, Probe, PrometheusRule, ScrapeConfig,
#   Alertmanager, AlertmanagerConfig, Prometheus, PrometheusAgent, ThanosRuler
#
# Cost: essentially zero. CRDs are just type definitions stored in the K8s API
# server — no controller, no RAM, no CPU. They sit dormant until something
# (Alloy, in our case, deployed in stage 7) starts watching for them.
#
# Why install early: every downstream module that emits metrics declares a
# `kind: ServiceMonitor` resource. Those resources require these CRDs to exist
# at apply time. Installing CRDs early eliminates `monitoring_enabled` flags
# and re-apply passes that v1 needed.
# =============================================================================

resource "helm_release" "crds" {
  name       = "prometheus-operator-crds"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "prometheus-operator-crds"
  version    = var.chart_version

  namespace        = var.namespace
  create_namespace = true # the Helm release metadata namespace; not the CRDs themselves (which are cluster-scoped)

  # Adopt CRDs that may already exist (e.g., from a prior kube-prometheus-stack
  # installation). Requires the helm provider ≥3.0. Costs nothing if no
  # pre-existing CRDs — just acts as a safety net.
  take_ownership = true

  # Wait until all CRDs are Established. The chart applies fast, but we
  # explicitly wait so downstream `kubernetes_manifest` resources don't race.
  wait    = true
  timeout = var.helm_timeout
}
