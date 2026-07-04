# =============================================================================
# playwright module — dual-mode browser pool (headed + headless)
# =============================================================================
#
# ZERO custom Dockerfile. All pre-built images:
#
#   playwright-headed (3-container pod):
#     - chromium  : mcr.microsoft.com/playwright:v1.59.1-jammy (MIT, official)
#     - novnc     : theasp/novnc:latest                        (MIT, bundles Xvfb+x11vnc+noVNC+websockify+Fluxbox)
#     - cdp-proxy : nginx:1.27-alpine-slim                     (BSD-2, ~12 MB)
#     Containers share /tmp/.X11-unix and /dev/shm via emptyDir.
#     Chromium connects to Xvfb (:0) provided by novnc sidecar via shared socket.
#     cdp-proxy reverse-proxies 0.0.0.0:9220 → 127.0.0.1:9222 with a
#     `Host: localhost:9222` rewrite, bypassing both Chrome M113+ lockdowns
#     (localhost-only bind + DNS-rebinding Host check). v1 used alpine/socat
#     here, which solved only the bind lockdown.
#
#   playwright-headless (1-container pod):
#     - chromedp/headless-shell:latest (MIT, CDP-native, no apt-get install needed)
#
# Apply order:
#   1. Namespace + VNC password Secret
#   2. Two Deployments + three Services (depend on Secret)
#   3. Three external Ingresses (noVNC + headed CDP + headless CDP)
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "playwright" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "playwright"
      "app.kubernetes.io/component"  = "automation"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# VNC password Secret — consumed only by the headed pod's noVNC sidecar
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "vnc_password" {
  metadata {
    name      = "playwright-vnc"
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "playwright"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    VNC_PASSWORD = var.vnc_password
  }

  depends_on = [kubernetes_namespace_v1.playwright]
}

# -----------------------------------------------------------------------------
# Deployments
# -----------------------------------------------------------------------------
# ConfigMap — nginx.conf for the cdp-proxy sidecar (Host-header rewrite)
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "configmap_cdp_proxy" {
  manifest = yamldecode(templatefile("${path.module}/k8s/configmap-cdp-proxy.yaml.tpl", {
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
  }))

  depends_on = [kubernetes_namespace_v1.playwright]
}

resource "kubernetes_manifest" "deployment_headed" {
  manifest = yamldecode(templatefile("${path.module}/k8s/deployment-headed.yaml.tpl", {
    namespace               = kubernetes_namespace_v1.playwright.metadata[0].name
    chromium_image          = var.chromium_image
    novnc_image             = var.novnc_image
    cdp_proxy_image         = var.cdp_proxy_image
    vnc_secret_name         = kubernetes_secret_v1.vnc_password.metadata[0].name
    chromium_cpu_request    = var.headed_chromium_cpu_request
    chromium_cpu_limit      = var.headed_chromium_cpu_limit
    chromium_memory_request = var.headed_chromium_memory_request
    chromium_memory_limit   = var.headed_chromium_memory_limit
    novnc_memory_request    = var.headed_novnc_memory_request
    novnc_memory_limit      = var.headed_novnc_memory_limit
    shm_size                = var.shm_size
  }))

  depends_on = [
    kubernetes_secret_v1.vnc_password,
    kubernetes_manifest.configmap_cdp_proxy,
  ]
}

resource "kubernetes_manifest" "deployment_headless" {
  manifest = yamldecode(templatefile("${path.module}/k8s/deployment-headless.yaml.tpl", {
    namespace       = kubernetes_namespace_v1.playwright.metadata[0].name
    image           = var.headless_image
    cdp_proxy_image = var.cdp_proxy_image
    cpu_request     = var.headless_cpu_request
    cpu_limit       = var.headless_cpu_limit
    memory_request  = var.headless_memory_request
    memory_limit    = var.headless_memory_limit
    shm_size        = var.shm_size
  }))

  depends_on = [
    kubernetes_namespace_v1.playwright,
    kubernetes_manifest.configmap_cdp_proxy,
  ]
}

# -----------------------------------------------------------------------------
# Deployment — playwright-server (Playwright WS-protocol mode for Open WebUI)
# -----------------------------------------------------------------------------
# Distinct from headed/headless (CDP) — speaks Playwright's NATIVE WebSocket
# protocol on port 3000. Consumed by Open WebUI's web-loader engine
# (WEB_LOADER_ENGINE=playwright, PLAYWRIGHT_WS_URL=ws://playwright-server...).
#
# No cdp-proxy sidecar (run-server doesn't expose CDP), no external Ingress
# (internal-only API, no UI — per memory feedback_no_external_ingress_for_uiless_backends).
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "deployment_server" {
  manifest = yamldecode(templatefile("${path.module}/k8s/deployment-server.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.playwright.metadata[0].name
    image              = var.server_image
    playwright_version = var.server_playwright_version
    cpu_request        = var.server_cpu_request
    cpu_limit          = var.server_cpu_limit
    memory_request     = var.server_memory_request
    memory_limit       = var.server_memory_limit
    shm_size           = var.shm_size
  }))

  depends_on = [kubernetes_namespace_v1.playwright]
}

# -----------------------------------------------------------------------------
# Services
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "service_headed" {
  manifest = yamldecode(templatefile("${path.module}/k8s/service-headed.yaml.tpl", {
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
  }))

  depends_on = [kubernetes_manifest.deployment_headed]
}

resource "kubernetes_manifest" "service_headless" {
  manifest = yamldecode(templatefile("${path.module}/k8s/service-headless.yaml.tpl", {
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
  }))

  depends_on = [kubernetes_manifest.deployment_headless]
}

resource "kubernetes_manifest" "service_novnc" {
  manifest = yamldecode(templatefile("${path.module}/k8s/service-novnc.yaml.tpl", {
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
  }))

  depends_on = [kubernetes_manifest.deployment_headed]
}

resource "kubernetes_manifest" "service_server" {
  manifest = yamldecode(templatefile("${path.module}/k8s/service-server.yaml.tpl", {
    namespace = kubernetes_namespace_v1.playwright.metadata[0].name
  }))

  depends_on = [kubernetes_manifest.deployment_server]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Services, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the external Ingresses above — those stay unconditional and
# work as-is on any environment with a real external ingress controller. This is for
# k3d standalone dev clusters. noVNC and headed-CDP share one Service since
# both live on the SAME "headed" pod (verified via `kubectl get svc -n
# playwright playwright-novnc/-headed -o yaml` — identical selector, just
# different ports); headless-CDP is a genuinely separate pod/selector.
# -----------------------------------------------------------------------------
module "k3d_expose_headed" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.playwright.metadata[0].name
  service_name = "playwright-headed"
  pod_selector = {
    "app.kubernetes.io/component" = "headed"
    "app.kubernetes.io/name"      = "playwright"
  }
  ports = [
    { name = "novnc", target_port = 8080, node_port = var.k3d_novnc_node_port },
    { name = "cdp", target_port = 9220, node_port = var.k3d_cdp_headed_node_port },
  ]

  depends_on = [
    kubernetes_manifest.deployment_headed,
    kubernetes_manifest.service_headed,
    kubernetes_manifest.service_novnc,
  ]
}

module "k3d_expose_headless" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.playwright.metadata[0].name
  service_name = "playwright-headless"
  pod_selector = {
    "app.kubernetes.io/component" = "headless"
    "app.kubernetes.io/name"      = "playwright"
  }
  ports = [
    { name = "cdp", target_port = 9220, node_port = var.k3d_cdp_headless_node_port },
  ]

  depends_on = [
    kubernetes_manifest.deployment_headless,
    kubernetes_manifest.service_headless,
  ]
}
