# =============================================================================
# ArgoCD Image Updater Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: argo/argocd-image-updater v1.1.4 (appVersion v1.1.1)
#
# How auto-deploy works:
#   1. Image Updater polls each registry on `check_interval`.
#   2. For every Application annotated with image-updater rules, it queries
#      the registry's manifest API and compares digests.
#   3. On a digest change, it patches the Application directly (write-back-method:
#      argocd) — no git commit needed for `argocd` write-back.
#   4. ArgoCD reconciles and rolls the new digest.
#
# Required per-Application annotations (set in your manifests/Helm chart):
#   argocd-image-updater.argoproj.io/image-list: app=coelho-cloud-registry:5000/coelhonexus-fastapi
#   argocd-image-updater.argoproj.io/app.update-strategy: digest
#   argocd-image-updater.argoproj.io/write-back-method: argocd
# =============================================================================

replicaCount: 1

image:
  repository: quay.io/argoprojlabs/argocd-image-updater
  pullPolicy: IfNotPresent

# -----------------------------------------------------------------------------
# Connection to ArgoCD's gRPC API (in-cluster)
# -----------------------------------------------------------------------------
config:
  argocd:
    grpcWeb: true
    serverAddress: "${argocd_server_url}"
    insecure: true # in-cluster
    plaintext: false

  logLevel: info

  # ---------------------------------------------------------------------------
  # Registry list — Image Updater polls each one for tag updates.
  # ---------------------------------------------------------------------------
  # k3d registry FIRST (no auth, fastest, used by GitLab CI per
  # docs/gitlab_registry_fallback.md).
  # GitLab Registry SECOND (only when var.gitlab_registry_endpoint is set —
  # behind the JWT-auth fallback decision; activate after smoke-testing).
  # ---------------------------------------------------------------------------
  registries:
    - name: k3d
      api_url: http://${k3d_registry_endpoint}
      prefix: ${k3d_registry_endpoint}
      insecure: yes
      default: true
%{ if gitlab_registry_endpoint != "" ~}
    - name: gitlab
      api_url: https://${gitlab_registry_endpoint}
      prefix: ${gitlab_registry_endpoint}
      ping: yes
      credentials: pullsecret:argocd/argocd-gitlab-registry-creds
%{ endif ~}

# Update check interval (passed to the binary).
extraArgs:
  - --interval=${check_interval}

# -----------------------------------------------------------------------------
# Resources
# -----------------------------------------------------------------------------
resources:
  requests:
    cpu: 10m
    memory: ${memory_request}
  limits:
    memory: ${memory_limit}

# -----------------------------------------------------------------------------
# Self-monitoring
# -----------------------------------------------------------------------------
metrics:
  enabled: ${service_monitor_enabled}
  serviceMonitor:
    enabled: ${service_monitor_enabled}

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

podSecurityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault

serviceAccount:
  create: true
  name: argocd-image-updater

rbac:
  enabled: true
