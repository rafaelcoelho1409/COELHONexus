# =============================================================================
# cert-manager module — TLS certificate provisioner for the standalone cluster
# =============================================================================
#
# What this module installs:
#   - The jetstack/cert-manager Helm chart with bundled CRDs
#     (Issuer, ClusterIssuer, Certificate, CertificateRequest, Order, Challenge,
#      CertificateSigningRequest — 7 CRDs total)
#   - Three Deployments: cert-manager controller, webhook, cainjector
#
# Why we need this on COELHONexus (and NOT on COELHO Cloud):
#   - Rancher's chart with `tls: rancher` creates a `cert-manager.io/v1 Issuer`
#     resource that asks cert-manager to issue a self-signed cert.
#   - On COELHO Cloud, Rancher uses `tls: external` — the tailscale-operator's
#     proxy terminates TLS using Tailscale's auto-provisioned certs. No
#     cert-manager needed.
#   - We deliberately dropped tailscale-operator from the standalone install
#     (no external Tailscale dependency). Without an external TLS terminator,
#     Rancher must self-sign — which means cert-manager.
#
# This is the canonical Rancher install path per the official docs:
#   https://ranchermanager.docs.rancher.com/getting-started/installation-and-upgrade/install-upgrade-on-a-kubernetes-cluster
#
# Cost: ~140 MiB total (controller + webhook + cainjector). Cluster-scoped
# CRDs sit dormant when no Certificate/Issuer resources reference them.
# =============================================================================

resource "helm_release" "cert_manager" {
  name       = var.release_name
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = var.chart_version

  namespace        = var.namespace
  create_namespace = true

  # As of cert-manager v1.15+, the chart uses `crds.enabled` (the old
  # `installCRDs` was deprecated). Letting the chart manage CRDs is the
  # simpler pattern here — cert-manager's CRDs are version-coupled with
  # the controller, so installing them out-of-band creates upgrade pain.
  set = [
    {
      name  = "crds.enabled"
      value = "true"
    },
    # Keep the CRDs in etcd even if the helm release is uninstalled —
    # prevents accidental data loss on cert-manager re-installs.
    {
      name  = "crds.keep"
      value = "true"
    },
  ]

  # Wait until the webhook + controller are Ready before returning. Without
  # this, downstream Helm releases (Rancher with `tls: rancher`) race against
  # cert-manager's validating webhook and get rejected.
  wait    = true
  timeout = var.helm_timeout
}
