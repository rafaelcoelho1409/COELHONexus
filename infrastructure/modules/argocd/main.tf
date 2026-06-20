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
#        - server runs in --insecure mode (TLS terminated at Tailscale proxy)
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
#   5. Tailscale Ingress for the web UI (Homepage tile in Development group)
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
# Tailscale Ingress — Web UI
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.argocd.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
    tailscale_domain   = var.tailscale_domain
    ingress_class_name = var.tailscale_ingress_class
  }))

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
