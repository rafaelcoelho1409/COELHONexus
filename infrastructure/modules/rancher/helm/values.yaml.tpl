# =============================================================================
# Rancher Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Variables interpolated by templatefile():
#   ${hostname}, ${bootstrap_password}, ${rancher_image_tag}
#   ${replicas}, ${audit_log_level}
#   ${cpu_request}, ${memory_request}, ${memory_limit}
#   ${enable_prometheus_metrics}
# =============================================================================

# Hostname — must match what the Tailscale Ingress will route.
hostname: "${hostname}"

# Single replica for homelab.
replicas: ${replicas}

# Initial admin password. Rancher forces a change on first login.
bootstrapPassword: "${bootstrap_password}"

# TLS source — `external` on COELHO Cloud (Tailscale operator terminates TLS);
# `rancher` on COELHONexus standalone (no proxy, Rancher self-signs and serves
# HTTPS natively on port 443). Driven by the module's `tls_source` input.
tls: ${tls_source}

# Disable the chart's own Ingress — we create a Tailscale-flavored Ingress
# separately via kubernetes_manifest in main.tf.
ingress:
  enabled: false

# Service for the Ingress to point at — ClusterIP only (no NodePort/LB).
service:
  type: ClusterIP

# Pin the image tag to match the chart version.
rancherImage: rancher/rancher
rancherImageTag: "${rancher_image_tag}"
rancherImagePullPolicy: IfNotPresent

# Resource sizing — tuned in v1 from real measurements (~13m CPU, ~936Mi RAM).
# memory_limit must be ≥1.5Gi or startup OOMKills.
resources:
  requests:
    cpu: "${cpu_request}"
    memory: "${memory_request}"
  limits:
    memory: "${memory_limit}"

# Startup probe — Rancher needs 5-10 minutes to apply all embedded CRDs
# (~70+ resources) on first boot AND after node restart cascades when the
# k8s API is contended by every other recovering pod. The chart default
# `failureThreshold: 12 × periodSeconds: 10s = 120s` is too tight — pod
# gets SIGTERMed mid-CRD-apply and crashloops indefinitely. Bump to 10 min.
# Once startup passes, the chart-default liveness/readiness probes take over.
startupProbe:
  httpGet:
    path: /healthz
    port: 80
    scheme: HTTP
  failureThreshold: 60   # 60 × 10s = 600s = 10 min budget
  periodSeconds: 10
  timeoutSeconds: 5
  successThreshold: 1

# Audit logging level (0 keeps it disabled / metadata-only).
auditLog:
  enabled: ${audit_log_level > 0}
  level: ${audit_log_level}

# Rancher feature flags. Disabling these reduces the controller load inside
# the rancher pod AND removes auto-installed Deployments (gitjob).
# See variables.tf for the full rationale. Comma-separated key=value.
features: "${features}"

# Antiaffinity — chart default is "preferred", which is safe with replicas=1.
# Explicit here to defend against future chart-default changes that might flip
# to "required" (which would wedge upgrades on a single-node cluster).
antiAffinity: preferred

# extraEnv: env vars applied to every Rancher pod container.
# Always-present block (no conditional wrapper) because we always want at least
# the Go-runtime + Cattle worker tuning. Prometheus metrics is one item among many.
extraEnv:
  # Go runtime: GOMEMLIMIT is a SOFT memory ceiling — Go GC fires aggressively
  # as it approaches this value, well before the K8s hard memory_limit triggers
  # an OOMKill. This is the OOM-cascade fix (rancher was Exit 137 4× in 22h).
  - name: GOMEMLIMIT
    value: "${gomemlimit}"
  - name: GOGC
    value: "${gogc}"

  # Cattle controller workload: ONE local cluster, ONE worker per controller
  # is plenty. Default 5 workers × ~30 controllers = 150 concurrent reconciles
  # on idle — a major contributor to baseline RSS.
  - name: CATTLE_WORKER_COUNT
    value: "${cattle_worker_count}"
  - name: CATTLE_RESYNC_DEFAULT
    value: "${cattle_resync_seconds}"

%{ if enable_prometheus_metrics ~}
  - name: CATTLE_PROMETHEUS_METRICS
    value: "true"
%{ endif ~}
