# =============================================================================
# argocd module — GitOps continuous delivery + Image Updater (auto-deploy)
# =============================================================================
#
# Deploys:
#   1. argocd namespace
#   2. argo/argo-cd Helm release (chart 9.4.17, appVersion v3.3.6)
#        - server, controller, repo-server, applicationset (4 core pods)
#        - bundled redis (~10Mi — too small to bother externalizing)
#        - dex disabled (no Federated SSO; admin user is enough for homelab)
#        - notifications disabled (saves ~192Mi; enable when you wire alerts)
#        - server runs in --insecure mode (TLS terminated at external proxy)
#        - ServiceMonitors per component (Alloy auto-scrapes via prometheus.operator)
#   3. (Optional) GitLab repo creds Secret labeled `argocd.argoproj.io/secret-type: repo-creds`
#        - Enables ArgoCD to clone private GitLab repos
#        - Two-step: first apply with empty token → public repos only;
#          create PAT in GitLab → SOPS in → re-apply
#   4. (Optional) argo/argocd-image-updater Helm release (chart 1.1.4)
#        - Polls k3d-registry + GitLab Registry every check_interval
#        - Detects new digests on tags like :latest or :CI_COMMIT_SHA
#        - Patches the ArgoCD Application directly (write-back-method: argocd)
#        - This is what gives us the "CI builds image → ArgoCD redeploys" loop
#   5. External Ingress for the web UI (Homepage tile in Development group)
#
# THE AUTO-DEPLOY FLOW (the user's primary requirement):
#   - Developer pushes code to GitLab
#   - GitLab CI builds + pushes image to coelho-cloud-registry:5000 (k3d
#     registry; or to GitLab Registry once the JWT issue is resolved)
#   - Image Updater polls registries on `check_interval` (default 2 min),
#     detects the new digest
#   - Image Updater patches the targeted Application's
#     spec.source.helm.parameters or .kustomize.images entry (depending on
#     manifest type) with the new digest
#   - ArgoCD reconciles → rolls the new image into the cluster
#
# Requires Application annotations on each managed app, e.g.:
#   argocd-image-updater.argoproj.io/image-list: app=coelho-cloud-registry:5000/coelhonexus-fastapi
#   argocd-image-updater.argoproj.io/app.update-strategy: digest
#   argocd-image-updater.argoproj.io/write-back-method: argocd
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "argocd" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "argocd"
      "app.kubernetes.io/component"  = "gitops"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# v2 Redis password Secret — chart reads via externalRedis.existingSecret
# -----------------------------------------------------------------------------
# Key MUST be `redis-password` (chart's hard-coded expectation). The chart's
# redisSecretInit Job is disabled in values — we provide this Secret pre-install.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "redis_password" {
  metadata {
    # Distinct from the chart's bundled-redis-flow Secret (`<release>-redis`,
    # created by redisSecretInit). Using a separate name avoids collision
    # with leftover Secrets from prior chart revisions.
    name      = "${var.release_name}-redis-password"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "argocd"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    redis-password = var.redis_password
  }
}

# -----------------------------------------------------------------------------
# Helm release — argo/argo-cd
# -----------------------------------------------------------------------------
resource "helm_release" "argocd" {
  name       = var.release_name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      release_name        = var.release_name
      tailscale_hostname  = var.tailscale_hostname
      tailscale_domain    = var.tailscale_domain
      gitlab_url          = var.gitlab_url
      rbac_default_policy = var.rbac_default_policy

      enable_notifications    = var.enable_notifications ? "true" : "false"
      enable_dex              = var.enable_dex ? "true" : "false"
      enable_applicationset   = var.enable_applicationset ? "true" : "false"
      service_monitor_enabled = var.service_monitor_enabled ? "true" : "false"

      server_memory_request      = var.server_memory_request
      server_memory_limit        = var.server_memory_limit
      controller_memory_request  = var.controller_memory_request
      controller_memory_limit    = var.controller_memory_limit
      controller_gomemlimit      = var.controller_gomemlimit
      repo_server_memory_request = var.repo_server_memory_request
      repo_server_memory_limit   = var.repo_server_memory_limit

      # v2 baseline Redis (DB index 2)
      redis_host   = var.redis_host
      redis_port   = var.redis_port
      redis_db     = var.redis_db
      redis_secret = kubernetes_secret_v1.redis_password.metadata[0].name
    })
  ]

  wait    = true
  timeout = 600

  depends_on = [
    kubernetes_namespace_v1.argocd,
    kubernetes_secret_v1.redis_password,
  ]
}

# -----------------------------------------------------------------------------
# GitLab repo credentials Secret (optional)
# -----------------------------------------------------------------------------
# Labeled Secret pattern is how ArgoCD picks up cluster-config. The secret-type
# label tells ArgoCD this is a repo-creds entry. Empty token = unauthenticated
# (works for public repos only).
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "gitlab_repo_creds" {
  count = var.gitlab_url != "" && var.gitlab_token != "" ? 1 : 0

  metadata {
    name      = "${var.release_name}-gitlab-repo-creds"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "argocd.argoproj.io/secret-type" = "repo-creds"
      "app.kubernetes.io/name"         = "argocd"
      "app.kubernetes.io/instance"     = var.release_name
      "app.kubernetes.io/managed-by"   = "terraform"
    }
  }

  data = {
    type     = "git"
    url      = var.gitlab_url
    username = var.gitlab_username
    password = var.gitlab_token
  }

  depends_on = [helm_release.argocd]
}

# -----------------------------------------------------------------------------
# Helm release — argo/argocd-image-updater (auto-deploy on new image digests)
# -----------------------------------------------------------------------------
resource "helm_release" "image_updater" {
  count = var.enable_image_updater ? 1 : 0

  name       = var.image_updater_release_name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-image-updater"
  version    = var.image_updater_chart_version
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name

  values = [
    templatefile("${path.module}/helm/image-updater-values.yaml.tpl", {
      argocd_server_url        = "${var.release_name}-server.${var.namespace}.svc.cluster.local"
      check_interval           = var.image_updater_check_interval
      k3d_registry_endpoint    = var.k3d_registry_endpoint
      gitlab_registry_endpoint = var.gitlab_registry_endpoint
      memory_request           = var.image_updater_memory_request
      memory_limit             = var.image_updater_memory_limit
      service_monitor_enabled  = var.service_monitor_enabled ? "true" : "false"
    })
  ]

  wait    = true
  timeout = 300

  depends_on = [helm_release.argocd]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Service, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the external Ingress above — that stays unconditional and
# works as-is on any environment with a real external ingress controller. This is for
# k3d standalone dev clusters. Selector matches the chart's `argocd-server`
# Service (`app.kubernetes.io/instance: argocd, app.kubernetes.io/name:
# argocd-server`), verified via `kubectl get svc argocd-server -n argocd -o
# yaml` against the live cluster. target_port 8080 is the container's
# --insecure HTTP port — both the Service's http and https ports already
# point at the same 8080 target, so there's no separate TLS port to expose.
# -----------------------------------------------------------------------------
module "k3d_expose" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.argocd.metadata[0].name
  service_name = "${var.release_name}-server"
  pod_selector = {
    "app.kubernetes.io/instance" = var.release_name
    "app.kubernetes.io/name"     = "argocd-server"
  }
  ports = [
    { name = "http", target_port = 8080, node_port = var.k3d_node_port },
  ]

  depends_on = [helm_release.argocd]
}

# -----------------------------------------------------------------------------
# Deterministic admin password — post-install sync Job (opt-in)
# -----------------------------------------------------------------------------
# Why not just the Helm value: configs.secret.argocdServerAdminPassword is
# documented as unreliable at install time (argo-helm#1407) — the chart's
# own auto-generated argocd-initial-admin-secret can still win the race.
# Patching argocd-secret directly, after the chart is already up, always wins
# — same reasoning as grafana's sync_admin_password Job.
#
# Two containers because bcrypt hashing and Secret-patching need different
# tools that don't coexist in one image: `argocd account bcrypt` is a local,
# offline computation (no server/config needed — verified against the CLI
# docs) using the exact algorithm ArgoCD itself validates against; kubectl
# then patches the Secret over the K8s API. Init container computes the hash
# onto a shared emptyDir; main container reads it and patches.
#
# Job name carries a content hash of the password so a password change
# forces a fresh Job (Job specs are immutable post-creation — same pattern
# as grafana's admin-sync Job).
# -----------------------------------------------------------------------------

resource "kubernetes_service_account_v1" "admin_password_sync" {
  count = var.admin_password != "" ? 1 : 0

  metadata {
    name      = "${var.release_name}-admin-pw-sync"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "argocd"
      "app.kubernetes.io/component"  = "admin-password-sync"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

resource "kubernetes_role_v1" "admin_password_sync" {
  count = var.admin_password != "" ? 1 : 0

  metadata {
    name      = "${var.release_name}-admin-pw-sync"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
  }

  # Scoped to exactly one named Secret — least privilege.
  rule {
    api_groups     = [""]
    resources      = ["secrets"]
    resource_names = ["${var.release_name}-secret"]
    verbs          = ["get", "patch"]
  }
}

resource "kubernetes_role_binding_v1" "admin_password_sync" {
  count = var.admin_password != "" ? 1 : 0

  metadata {
    name      = "${var.release_name}-admin-pw-sync"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.admin_password_sync[0].metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.admin_password_sync[0].metadata[0].name
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
  }
}

locals {
  admin_password_hash = var.admin_password != "" ? substr(sha256(var.admin_password), 0, 8) : ""
}

resource "kubernetes_job_v1" "sync_admin_password" {
  count = var.admin_password != "" ? 1 : 0

  metadata {
    name      = "${var.release_name}-admin-pw-sync-${local.admin_password_hash}"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "argocd"
      "app.kubernetes.io/component"  = "admin-password-sync"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    backoff_limit              = 3
    ttl_seconds_after_finished = 300

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name"      = "argocd"
          "app.kubernetes.io/component" = "admin-password-sync"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.admin_password_sync[0].metadata[0].name
        restart_policy       = "OnFailure"

        security_context {
          run_as_non_root = true
          run_as_user     = 999
          run_as_group    = 999
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        init_container {
          name    = "bcrypt"
          image   = var.argocd_cli_image
          command = ["/bin/sh", "-c"]
          args    = ["argocd account bcrypt --password \"$ADMIN_PASSWORD\" > /shared/hash.txt"]

          env {
            name  = "ADMIN_PASSWORD"
            value = var.admin_password
          }

          volume_mount {
            name       = "shared"
            mount_path = "/shared"
          }

          security_context {
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
        }

        container {
          name    = "patch-secret"
          image   = "bitnami/kubectl:latest"
          command = ["/bin/sh", "-c"]
          args = [
            <<-EOT
            set -e
            HASH=$(cat /shared/hash.txt)
            MTIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
            kubectl patch secret ${var.release_name}-secret \
              -n ${kubernetes_namespace_v1.argocd.metadata[0].name} \
              --type merge \
              -p "{\"stringData\":{\"admin.password\":\"$HASH\",\"admin.passwordMtime\":\"$MTIME\"}}"
            EOT
          ]

          volume_mount {
            name       = "shared"
            mount_path = "/shared"
          }

          security_context {
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
        }

        volume {
          name = "shared"
          empty_dir {}
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "2m"
    update = "2m"
  }

  depends_on = [
    helm_release.argocd,
    kubernetes_role_binding_v1.admin_password_sync,
  ]
}
