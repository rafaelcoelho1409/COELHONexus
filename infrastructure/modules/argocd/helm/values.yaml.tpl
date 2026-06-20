# =============================================================================
# ArgoCD Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: argo/argo-cd v9.4.17 (appVersion v3.3.6)
# Repo:  https://argoproj.github.io/argo-helm
#
# All variables interpolated as SCALARS (per memory feedback_yamlencode_helm_values).
#
# v2 baseline DBs (per memory feedback_default_to_v2_baseline_dbs):
#   - Bundled redis + redis-ha sub-charts DISABLED
#   - externalRedis points at v2 Redis (DB index 2; Open WebUI=0, GitLab=1, ArgoCD=2)
#   - REDIS_DB env var injected on every component
# =============================================================================

global:
  domain: ${tailscale_hostname}.${tailscale_domain}

# -----------------------------------------------------------------------------
# Server — UI + API. Run in --insecure mode (Tailscale proxy terminates TLS).
# -----------------------------------------------------------------------------
server:
  extraArgs:
    - --insecure

  env:
    - name: REDIS_DB
      value: "${redis_db}"

  resources:
    requests:
      cpu: 10m
      memory: ${server_memory_request}
    limits:
      cpu: 100m
      memory: ${server_memory_limit}

  metrics:
    enabled: true
    service:
      type: ClusterIP
      servicePort: 8083
    serviceMonitor:
      enabled: ${service_monitor_enabled}

# -----------------------------------------------------------------------------
# Controller — runs the reconciliation loop (compares desired vs live state)
# -----------------------------------------------------------------------------
controller:
  # The application-controller caches ALL live cluster objects + desired
  # manifests in memory; usage scales with object count, not idle apps. The
  # upstream chart sets NO limit here by default. We keep a generous limit AND
  # set GOMEMLIMIT (~90% of the limit) so the Go runtime GCs *before* the cgroup
  # OOM-kills the pod. (256Mi was far too low → exit 137 / CrashLoopBackOff
  # during initial cache sync, which silently blocks every Application sync.)
  # Refs: argo-cd HA docs; github.com/argoproj/argo-cd/issues/26225
  env:
    - name: REDIS_DB
      value: "${redis_db}"
    # Soft memory ceiling for the Go runtime — keep at ~90% of controller_memory_limit.
    - name: GOMEMLIMIT
      value: "${controller_gomemlimit}"

  resources:
    requests:
      cpu: 250m
      memory: ${controller_memory_request}
    limits:
      cpu: "1"
      memory: ${controller_memory_limit}

  metrics:
    enabled: true
    service:
      type: ClusterIP
      servicePort: 8082
    serviceMonitor:
      enabled: ${service_monitor_enabled}

# -----------------------------------------------------------------------------
# Repo Server — clones git repos, renders Helm/Kustomize/plain manifests
# -----------------------------------------------------------------------------
repoServer:
  env:
    - name: ARGOCD_EXEC_TIMEOUT
      value: "300s"
    - name: REDIS_DB
      value: "${redis_db}"

  resources:
    requests:
      cpu: 10m
      memory: ${repo_server_memory_request}
    limits:
      cpu: 100m
      memory: ${repo_server_memory_limit}

  metrics:
    enabled: true
    service:
      type: ClusterIP
      servicePort: 8084
    serviceMonitor:
      enabled: ${service_monitor_enabled}

# -----------------------------------------------------------------------------
# ApplicationSet Controller — templated Application generation
# -----------------------------------------------------------------------------
applicationSet:
  enabled: ${enable_applicationset}

  env:
    - name: REDIS_DB
      value: "${redis_db}"

  resources:
    requests:
      cpu: 10m
      memory: 128Mi
    limits:
      cpu: 50m
      memory: 256Mi

  metrics:
    enabled: true
    service:
      type: ClusterIP
      servicePort: 8085
    serviceMonitor:
      enabled: ${service_monitor_enabled}

# -----------------------------------------------------------------------------
# Notifications Controller — Slack/email/webhooks on Application status
# -----------------------------------------------------------------------------
notifications:
  enabled: ${enable_notifications}

  resources:
    requests:
      cpu: 10m
      memory: 192Mi
    limits:
      cpu: 50m
      memory: 384Mi

  metrics:
    enabled: true
    service:
      type: ClusterIP
      servicePort: 9001

# -----------------------------------------------------------------------------
# Redis — bundled DISABLED, externalRedis pointed at v2 baseline
# -----------------------------------------------------------------------------
redis:
  enabled: false

redis-ha:
  enabled: false

externalRedis:
  host: ${redis_host}
  port: ${redis_port}
  # Chart reads `redis-password` key from this Secret (hard-coded by chart).
  # Secret is pre-created by Terraform; password sourced from SOPS via the
  # redis module's dependency output.
  existingSecret: ${redis_secret}

# Chart's bundled Redis secret-init Job is disabled — we provide the Secret
# directly. With redis.enabled=false, the init Job has no role anyway.
redisSecretInit:
  enabled: false

# -----------------------------------------------------------------------------
# Dex — disabled (no federated SSO; admin user is sufficient for homelab)
# -----------------------------------------------------------------------------
dex:
  enabled: ${enable_dex}

# -----------------------------------------------------------------------------
# Configs — chart's argocd-cm + argocd-rbac-cm + repository config
# -----------------------------------------------------------------------------
configs:
  params:
    server.insecure: true
    controller.repo.server.timeout.seconds: "300"

  rbac:
    policy.default: ${rbac_default_policy}

%{ if gitlab_url != "" ~}
  # Pre-register GitLab as a known repository server so ArgoCD's UI shows it.
  # Per-repo credentials are wired via the labeled Secret in main.tf.
  repositories:
    gitlab:
      url: ${gitlab_url}
      name: gitlab
      type: git
%{ endif ~}

# -----------------------------------------------------------------------------
# Ingress — disabled. Tailscale Ingress comes from k8s/ingress.yaml.tpl in main.tf.
# -----------------------------------------------------------------------------
ingress:
  enabled: false
