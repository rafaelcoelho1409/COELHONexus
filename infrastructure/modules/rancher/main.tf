# =============================================================================
# rancher module — Rancher Server (cluster UI)
# =============================================================================
#
# Deploys:
#   1. cattle-system namespace
#   2. Rancher Helm chart (chart 2.14.1, app v2.14.1)
#   3. Tailscale Ingress → exposes Rancher at https://<hostname>.<domain>
#
# Architecture:
#   - chart's own Ingress: DISABLED (chart's Ingress is for nginx/etc.)
#   - chart's TLS: 'external' (Tailscale operator's proxy terminates TLS)
#   - Tailscale Ingress points at the rancher Service (ClusterIP, port 443)
#   - tailscale-operator (deployed earlier) spawns a proxy pod that joins
#     the tailnet as the configured hostname
#
# v1 compatibility: identical functional behavior, with chart bumped to 2.14.1.
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "rancher" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "rancher"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Rancher Helm release
# -----------------------------------------------------------------------------
# Values come from helm/values.yaml.tpl, rendered with our inputs. This is
# the v2 pattern — clean separation of "what's tunable" (variables.tf) from
# "what gets templated into the chart" (helm/values.yaml.tpl).
# -----------------------------------------------------------------------------
resource "helm_release" "rancher" {
  name       = var.release_name
  repository = "https://releases.rancher.com/server-charts/stable"
  chart      = "rancher"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.rancher.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      hostname                  = var.hostname_override != "" ? var.hostname_override : "${var.tailscale_hostname}.${var.tailscale_domain}"
      tls_source                = var.tls_source
      bootstrap_password        = var.bootstrap_password
      rancher_image_tag         = var.rancher_image_tag
      replicas                  = var.replicas
      audit_log_level           = var.audit_log_level
      enable_prometheus_metrics = var.enable_prometheus_metrics
      cpu_request               = var.cpu_request
      memory_request            = var.memory_request
      memory_limit              = var.memory_limit
      gomemlimit                = var.gomemlimit
      gogc                      = var.gogc
      cattle_worker_count       = var.cattle_worker_count
      cattle_resync_seconds     = var.cattle_resync_seconds
      features                  = var.rancher_features
    })
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 600 # Rancher takes a few minutes to fully come up
}

# -----------------------------------------------------------------------------
# Cleanup #1 — disable system-upgrade-controller (scale to 0)
# -----------------------------------------------------------------------------
# Rancher considers SUC a mandatory managed component (`apps.cattle.io/managed-
# system-upgrade-controller=true` annotation) and reinstalls it within seconds
# of any `helm uninstall`, regardless of the rancher_features `rke2=false,
# provisioningv2=false` flags. So uninstalling is futile.
#
# Instead: keep the helm release installed (so Rancher's reconciler is
# happy) but scale the Deployment to 0 replicas. The helm-managed spec stays
# intact; scaling a Deployment's replicas is NOT something the chart re-applies
# on every reconcile, so the scale=0 sticks until the chart itself upgrades.
#
# Idempotent (kubectl scale --replicas=0 is a no-op if already 0).
# -----------------------------------------------------------------------------
resource "null_resource" "uninstall_system_upgrade_controller" {
  count = var.enable_system_upgrade_controller ? 0 : 1

  triggers = {
    enabled  = tostring(var.enable_system_upgrade_controller)
    approach = "scale-to-zero" # bump when the command logic changes (was: "helm-uninstall")
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -e
      if kubectl -n cattle-system get deploy system-upgrade-controller >/dev/null 2>&1; then
        echo ">> Scaling system-upgrade-controller to 0 (Rancher reinstalls on uninstall — scale-down is the workaround)"
        kubectl -n cattle-system scale deploy system-upgrade-controller --replicas=0
      else
        echo ">> system-upgrade-controller Deployment not present — no-op"
      fi
    EOT
  }

  depends_on = [helm_release.rancher]
}

# -----------------------------------------------------------------------------
# Cleanup #2 — patch rancher-webhook with explicit memory limit
# -----------------------------------------------------------------------------
# Chart leaves rancher-webhook in Burstable QoS without a memory limit. Adding
# a strategic-merge limit prevents the webhook from creeping up under sustained
# admission load. Replicas already defaults to 1 in current chart so not patched.
# Patch is idempotent (kubectl patch with same value is a no-op).
# -----------------------------------------------------------------------------
resource "null_resource" "tune_rancher_webhook" {
  triggers = {
    memory_limit = var.rancher_webhook_memory_limit
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -e
      # Rancher installs rancher-webhook as a SEPARATE Helm release AFTER its main
      # pod is healthy — can take 3-8 min on a fresh cluster. Wait for it before
      # patching; if it never appears, exit cleanly so terragrunt apply succeeds
      # (re-run apply later to retry the patch — it's idempotent).
      echo ">> Waiting for rancher-webhook Deployment (up to 8 min on fresh installs)..."
      for i in $(seq 1 48); do
        if kubectl -n cattle-system get deploy rancher-webhook >/dev/null 2>&1; then
          echo ">> rancher-webhook found at attempt $i — patching..."
          kubectl -n cattle-system patch deploy rancher-webhook --type=strategic -p '{
            "spec": {
              "template": {
                "spec": {
                  "containers": [{
                    "name": "rancher-webhook",
                    "resources": {
                      "requests": {"cpu":"50m","memory":"64Mi"},
                      "limits":   {"memory":"${var.rancher_webhook_memory_limit}"}
                    }
                  }]
                }
              }
            }
          }'
          exit 0
        fi
        sleep 10
      done
      echo ">> rancher-webhook did not appear after 8 min — skipping patch (re-run \`terragrunt apply\` once webhook is up)"
      exit 0
    EOT
  }

  depends_on = [helm_release.rancher]
}

# -----------------------------------------------------------------------------
# Cleanup #3 — starve Rancher Turtles' CAPI providers
# -----------------------------------------------------------------------------
# Turtles is MANDATORY on Rancher 2.14 (the embedded-cluster-api feature flag
# was removed). Can't uninstall.
#
# IMPORTANT — `embedded-capi` is a HELM CHART VALUE (rancherTurtles.features.
# embedded-capi.disabled=true), NOT a CLI `--feature-gates` flag. The Turtles
# binary REJECTS it as an unrecognized feature gate (verified by attempted
# patch: `unrecognized feature gate: embedded-capi`). Without re-upgrading the
# chart with the value (impractical — Rancher's bundled chart source isn't a
# public helm repo), we can't disable embedded-capi at the controller level.
#
# What we CAN do:
#   1. Switch the controller's --leader-elect from true to false (single replica,
#      no need for the lease overhead). Preserves all valid existing feature-gates.
#   2. Scale capi-controller-manager to 0. The cluster-api-operator isn't a
#      separate Deployment in this install, so nothing will reconcile it back up.
#      If Turtles re-creates it later (it shouldn't at the chart-default sync),
#      next `terragrunt apply` re-scales it.
#
# Patches are idempotent — `kubectl patch --type=json replace` with the same
# value is a no-op; same for scale to 0.
# -----------------------------------------------------------------------------
resource "null_resource" "starve_turtles_capi" {
  count = var.enable_turtles_capi ? 0 : 1

  triggers = {
    enabled        = tostring(var.enable_turtles_capi)
    args_signature = "leader-elect=false;feature-gates=agent-tls-mode,no-cert-manager,use-rancher-default-registry,use-caapf=false"
    # Bump args_signature when the JSON patch contents change so the resource re-fires.
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -e
      # Rancher installs rancher-turtles as a SEPARATE Helm release AFTER its main
      # pod is healthy — can take 3-8 min on a fresh cluster. Wait for it before
      # patching; if it never appears, exit cleanly so terragrunt apply succeeds.
      echo ">> Waiting for rancher-turtles-controller-manager Deployment (up to 8 min on fresh installs)..."
      for i in $(seq 1 48); do
        if kubectl -n cattle-turtles-system get deploy rancher-turtles-controller-manager >/dev/null 2>&1; then
          echo ">> rancher-turtles found at attempt $i — patching args..."
          kubectl -n cattle-turtles-system patch deploy rancher-turtles-controller-manager --type=json -p='[
            {
              "op": "replace",
              "path": "/spec/template/spec/containers/0/args",
              "value": [
                "--leader-elect=false",
                "--feature-gates=agent-tls-mode=true,no-cert-manager=true,use-rancher-default-registry=true,use-caapf=false"
              ]
            }
          ]'

          echo ">> Scaling capi-controller-manager to 0"
          if kubectl -n cattle-capi-system get deploy capi-controller-manager >/dev/null 2>&1; then
            kubectl -n cattle-capi-system scale deploy capi-controller-manager --replicas=0
          fi
          exit 0
        fi
        sleep 10
      done
      echo ">> rancher-turtles did not appear after 8 min — skipping patch (re-run \`terragrunt apply\` once turtles is up)"
      exit 0
    EOT
  }

  depends_on = [helm_release.rancher]
}

# -----------------------------------------------------------------------------
# Tailscale Ingress
# -----------------------------------------------------------------------------
# Built-in Ingress kind — works with plain kubernetes_manifest (no
# kubectl_manifest needed; the Ingress CRD is part of every cluster).
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "rancher_ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.rancher.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
    tailscale_domain   = var.tailscale_domain
    ingress_class_name = var.tailscale_ingress_class
  }))

  depends_on = [helm_release.rancher]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Service, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the Tailscale Ingress above — that stays unconditional and
# works as-is on any environment with a real Tailscale operator. This is for
# k3d standalone dev clusters. Selector matches the chart's `rancher` Service
# (`app: rancher`), verified via `kubectl get svc rancher -n cattle-system -o
# yaml` against the live cluster.
# -----------------------------------------------------------------------------
module "k3d_expose" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.rancher.metadata[0].name
  service_name = var.release_name
  pod_selector = {
    "app" = "rancher"
  }
  ports = [
    { name = "https", target_port = 443, node_port = var.k3d_https_node_port },
  ]

  depends_on = [helm_release.rancher]
}
