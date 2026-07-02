# =============================================================================
# argocd module — inputs
# =============================================================================
# Charts deployed by this module:
#   - argo/argo-cd v9.4.17 (appVersion v3.3.6)
#   - argo/argocd-image-updater v1.1.4 (appVersion v1.1.1) — when enable_image_updater=true
# Repo: https://argoproj.github.io/argo-helm
#
# Auto-deploy flow (the user's primary requirement):
#   GitLab CI builds image → pushes to registry (GitLab Registry OR k3d
#   registry) → Image Updater polls registries every check_interval, detects
#   new digest → patches the ArgoCD Application directly (write-back-method:
#   argocd) → ArgoCD reconciles → new Pod rolls out.
# =============================================================================

variable "chart_version" {
  description = "argo/argo-cd Helm chart version. Latest: 9.4.17 (appVersion v3.3.6)."
  type        = string
  default     = "9.4.17"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.chart_version))
    error_message = "chart_version must be SemVer like '9.4.17'."
  }
}

variable "image_updater_chart_version" {
  description = "argo/argocd-image-updater Helm chart version. Latest: 1.1.4 (appVersion v1.1.1)."
  type        = string
  default     = "1.1.4"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.image_updater_chart_version))
    error_message = "image_updater_chart_version must be SemVer like '1.1.4'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for ArgoCD."
  type        = string
  default     = "argocd"
}

variable "release_name" {
  description = "Helm release name. Used as prefix for ArgoCD component Service names (server, controller, repo-server, etc.)."
  type        = string
  default     = "argocd"
}

variable "image_updater_release_name" {
  description = "Helm release name for the Image Updater. Lives in the same namespace as ArgoCD."
  type        = string
  default     = "argocd-image-updater"
}

# -----------------------------------------------------------------------------
# Network exposure (Tailscale)
# -----------------------------------------------------------------------------

variable "tailscale_hostname" {
  description = "Short tailnet hostname for the ArgoCD web UI (e.g. 'argocd' → argocd.<domain>.ts.net)."
  type        = string
  default     = "argocd"
}

variable "tailscale_domain" {
  description = "Tailnet domain (e.g. 'YOUR_TAILNET_DOMAIN.ts.net'). Comes from env.hcl."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "IngressClass name from the tailscale-operator unit."
  type        = string
  default     = "tailscale"
}

# -----------------------------------------------------------------------------
# GitLab integration — repo credentials for cloning manifest repos
# -----------------------------------------------------------------------------
# After GitLab is up, log in as root → User Settings → Access Tokens →
# create a PAT with `read_repository` + `read_registry` scopes → drop into
# SOPS as `gitlab.argocd_token`. Allows ArgoCD to clone private repos and
# allows Image Updater to authenticate against the GitLab Registry.
# -----------------------------------------------------------------------------

variable "gitlab_url" {
  description = "GitLab base URL (in-cluster preferred for ArgoCD repo cloning to bypass Tailscale TLS hop). Empty string skips GitLab repo creds Secret."
  type        = string
  default     = "http://gitlab-webservice-default.gitlab.svc.cluster.local:8181"
}

variable "gitlab_username" {
  description = "GitLab user for ArgoCD's repo clones. 'root' is the built-in admin."
  type        = string
  default     = "root"
}

variable "gitlab_token" {
  description = "GitLab Personal Access Token with read_repository + read_registry scopes. Two-step bootstrap: first apply with empty token (only public repos work) → log in to GitLab → create PAT → SOPS in → re-apply."
  type        = string
  sensitive   = true
  default     = ""
}

# -----------------------------------------------------------------------------
# v2 baseline Redis (per memory feedback_default_to_v2_baseline_dbs)
# -----------------------------------------------------------------------------
# ArgoCD uses Redis for caching (manifests, repository info, OIDC sessions).
# Per the v2 baseline rule, point at central Redis instead of running a
# per-app bundled Redis. Logical DB index 2 (Open WebUI=0, GitLab=1, ArgoCD=2).
# -----------------------------------------------------------------------------

variable "redis_host" {
  description = "Redis host (in-cluster DNS)."
  type        = string
  default     = "redis-master.redis.svc.cluster.local"
}

variable "redis_port" {
  description = "Redis port."
  type        = number
  default     = 6379
}

variable "redis_password" {
  description = "Redis password (from redis module via dependency block)."
  type        = string
  sensitive   = true
}

variable "redis_db" {
  description = "Redis logical DB index. Open WebUI=0, GitLab=1, ArgoCD=2, Langfuse=3, etc."
  type        = number
  default     = 2
}

# -----------------------------------------------------------------------------
# Image Updater — auto-detect new image digests in registries
# -----------------------------------------------------------------------------

variable "enable_image_updater" {
  description = "Deploy ArgoCD Image Updater alongside ArgoCD. Required for the user's auto-deploy story (CI pushes new image → Image Updater detects → ArgoCD redeploys)."
  type        = bool
  default     = true
}

variable "image_updater_check_interval" {
  description = "How often Image Updater polls registries for new digests. v1 used 2m; tighten to 1m for faster CI→deploy latency or relax to 5m to reduce registry load."
  type        = string
  default     = "2m"
}

variable "k3d_registry_endpoint" {
  description = "k3d in-cluster registry hostname:port — Image Updater's primary registry target. v2 cluster's k3d registry is `coelho-cloud-registry:5000` (created by the k3d module's --registry-create flag)."
  type        = string
  default     = "coelho-cloud-registry:5000"
}

variable "gitlab_registry_endpoint" {
  description = "GitLab Container Registry hostname:port for Image Updater. Empty string skips this registry config — useful while waiting on the JWT-auth issue documented in docs/gitlab_registry_fallback.md. Once registry is verified working, set to 'registry.YOUR_TAILNET_DOMAIN.ts.net'."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Resource sizing — based on v1's actual measurements (kubectl top pods)
# -----------------------------------------------------------------------------
# v1 observed (idle):
#   server 1m/51Mi, controller 1m/39Mi, repo 1m/44Mi, appset 1m/41Mi,
#   notifications 1m/86Mi, redis 6m/10Mi, image-updater n/a
# Below requests are 2-3× observed (safety margin), limits are 4-5×.
# EXCEPTION — controller: idle "kubectl top" is misleading. The controller
# caches every live cluster object in memory; the 256Mi limit OOMKilled it
# (exit 137) during cache sync and broke all deploys. Sized for real load
# (512Mi req / 1Gi limit) + GOMEMLIMIT, not for idle. See controller_* below.
# -----------------------------------------------------------------------------

variable "server_memory_request" {
  type    = string
  default = "128Mi"
}
variable "server_memory_limit" {
  type    = string
  default = "256Mi"
}
variable "controller_memory_request" {
  type    = string
  default = "512Mi" # Controller caches all live cluster + app state in memory
}
variable "controller_memory_limit" {
  type    = string
  default = "1Gi" # SOTA min for a small install; scale ~+1Gi per ~100 apps. 256Mi OOMKilled it.
}
variable "controller_gomemlimit" {
  type    = string
  default = "900MiB" # Soft Go-GC ceiling (~90% of controller_memory_limit); prevents OOMKill before GC. argo-cd#26225.
}
variable "repo_server_memory_request" {
  type    = string
  default = "128Mi"
}
variable "repo_server_memory_limit" {
  type    = string
  default = "256Mi"
}
variable "image_updater_memory_request" {
  type    = string
  default = "64Mi"
}
variable "image_updater_memory_limit" {
  type    = string
  default = "128Mi"
}

# -----------------------------------------------------------------------------
# Optional features
# -----------------------------------------------------------------------------

variable "enable_notifications" {
  description = "Enable ArgoCD Notifications controller (Slack/email/webhooks on Application status changes). Off in v2 — saves ~192Mi RAM. Enable later if you wire up alerting."
  type        = bool
  default     = false
}

variable "enable_dex" {
  description = "Enable Dex (federated SSO bridge). Off — ArgoCD has its own admin user; SSO via GitLab OIDC can be wired directly through configs.cm without Dex."
  type        = bool
  default     = false
}

variable "enable_applicationset" {
  description = "Enable ApplicationSet controller (templated Application generation across environments). On for the App-of-Apps GitOps pattern."
  type        = bool
  default     = true
}

variable "service_monitor_enabled" {
  description = "Create ServiceMonitors for Alloy/Mimir scraping (one per ArgoCD component)."
  type        = bool
  default     = true
}

variable "rbac_default_policy" {
  description = "Default RBAC role for unauthenticated/non-mapped users. 'role:readonly' is safe; 'role:admin' is convenient for single-operator homelab."
  type        = string
  default     = "role:admin"
}

# -----------------------------------------------------------------------------
# Local access (k3d dev clusters only — e.g. coelhonexus standalone)
# -----------------------------------------------------------------------------
# Opt-in NodePort Service for localhost access via k3d's loadbalancer port
# mapping. Leave `enable_local_expose` unset (default false) on any
# environment where Tailscale Ingress already provides access (e.g. COELHO
# Cloud) — the module below is never instantiated in that case. See
# infrastructure/modules/k3d_expose/.
#
# NOTE: ArgoCD already has a working localhost path via
# scripts/standalone-port-forward.sh (23007->80). This NodePort is a second,
# independent mechanism to REACH the UI. Both are plain HTTP — the server
# runs in --insecure mode (see main.tf), so there's no cert to accept either
# way.

variable "enable_local_expose" {
  description = "Create a NodePort Service for localhost access via k3d's loadbalancer port mapping. Only meaningful on k3d-based dev clusters."
  type        = bool
  default     = false
}

variable "k3d_node_port" {
  description = "NodePort for local ArgoCD UI access (target port 8080, the server's --insecure HTTP port). Required only when enable_local_expose = true; must be unique across the whole cluster."
  type        = number
  default     = null
}

# -----------------------------------------------------------------------------
# Deterministic admin password (optional)
# -----------------------------------------------------------------------------
# By default the chart auto-generates a random password in
# `argocd-initial-admin-secret` at install time. Setting `admin_password`
# forces it to a known value via a post-install Job — mirroring
# infrastructure/modules/grafana's `sync_admin_password` Job, which exists
# for the identical reason: setting the Helm value alone
# (configs.secret.argocdServerAdminPassword) is documented as unreliable
# (argo-helm#1407 — the auto-generated initial-admin-secret can still win).
# Leave unset (default "") to keep the chart's default random-password
# behavior.
# -----------------------------------------------------------------------------

variable "admin_password" {
  description = "Optional deterministic admin password. Empty string = keep the chart's auto-generated random password (read via argocd-initial-admin-secret)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "argocd_cli_image" {
  description = "Image used by the admin-password sync Job's bcrypt step. Keep aligned with chart_version's appVersion unless intentionally decoupled."
  type        = string
  default     = "quay.io/argoproj/argocd:v3.3.6"
}
