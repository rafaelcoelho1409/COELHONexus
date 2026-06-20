# =============================================================================
# playwright module — inputs
# =============================================================================
# Multi-container pod design — zero custom Dockerfile, zero build time.
#
# Headed pod: 3 pre-built containers (chromium + theasp/novnc + nginx-alpine)
#             share /tmp/.X11-unix via emptyDir. theasp/novnc runs Xvfb on :0,
#             chromium connects via shared X socket, the nginx cdp-proxy
#             sidecar (formerly socat) handles BOTH Chrome M113+ lockdowns:
#             (a) the localhost-only bind via reverse-proxy from 0.0.0.0:9220
#                 to 127.0.0.1:9222, and
#             (b) the Host-header DNS-rebinding check via
#                 `proxy_set_header Host localhost:9222`, so consumers can
#                 reach CDP through any k8s Service / Tailscale Ingress
#                 hostname without Chrome rejecting them.
#
# Headless pod: single chromedp/headless-shell container. CDP-native, MIT,
#               built-in handling of the M113+ lockdown.
# =============================================================================

variable "namespace" {
  description = "Kubernetes namespace for the two Playwright pods."
  type        = string
  default     = "playwright"
}

# -----------------------------------------------------------------------------
# Image tags — all pre-built, pinned for reproducibility
# -----------------------------------------------------------------------------

variable "chromium_image" {
  description = "Headed-mode Chromium base image. Official Playwright image with all browser system deps. `jammy` (Ubuntu 22.04) preferred over `noble` for mirror reliability."
  type        = string
  default     = "mcr.microsoft.com/playwright:v1.59.1-jammy"
}

variable "novnc_image" {
  description = "noVNC sidecar image. Bundles Xvfb + x11vnc + noVNC + websockify + Fluxbox. Owns the X server on display :0."
  type        = string
  default     = "theasp/novnc:latest"
}

variable "cdp_proxy_image" {
  description = "HTTP-aware reverse proxy sidecar that bridges 0.0.0.0:9220 → 127.0.0.1:9222 AND rewrites the Host header to 'localhost'. Replaces v1's alpine/socat (TCP-only — didn't fix Chrome M113+'s DNS-rebinding check on /json/*, which rejects any Host header that isn't localhost or a literal IP)."
  type        = string
  default     = "nginx:1.27-alpine-slim"
}

variable "headless_image" {
  description = "Headless pod image — chromedp's purpose-built headless-shell. Pre-built, MIT, CDP-native on port 9222."
  type        = string
  default     = "chromedp/headless-shell:latest"
}

# -----------------------------------------------------------------------------
# Playwright WS-server pod (third execution mode, Open WebUI integration)
# -----------------------------------------------------------------------------
# Open WebUI's Python backend pins `playwright==1.58.0`. Its web-loader
# (WEB_LOADER_ENGINE=playwright) connects via Playwright's NATIVE WebSocket
# protocol — NOT CDP. Client and server versions MUST match exactly; the
# protocol handshake fails on any mismatch.
#
# Bump these together when Open WebUI bumps its Playwright pin:
#   1. server_image              -> mcr.microsoft.com/playwright:vX.Y.Z-noble
#   2. server_playwright_version -> "X.Y.Z"
# Cross-check Open WebUI's pin at:
#   https://github.com/open-webui/open-webui/blob/main/backend/requirements.txt
# -----------------------------------------------------------------------------

variable "server_image" {
  description = "Playwright WS-server image. Pin to the same X.Y.Z as server_playwright_version."
  type        = string
  default     = "mcr.microsoft.com/playwright:v1.58.0-noble"
}

variable "server_playwright_version" {
  description = "Playwright npm package version passed to `npx playwright@X.Y.Z run-server`. MUST equal server_image's tag AND match Open WebUI's playwright==X.Y.Z pin."
  type        = string
  default     = "1.58.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.server_playwright_version))
    error_message = "server_playwright_version must be SemVer like '1.58.0' (no 'v' prefix)."
  }
}

variable "server_cpu_request" {
  type    = string
  default = "100m"
}

variable "server_cpu_limit" {
  description = "CPU limit. Use canonical form '1' (not '1000m') — K8s normalizes 1000m → 1, which trips kubernetes_manifest's consistency check (same trap as headless_cpu_limit)."
  type        = string
  default     = "1"
}

variable "server_memory_request" {
  description = "Idle ~150-200 MiB (Node.js + Playwright). Each spawned browser adds ~250-500 MiB; Open WebUI fetches one page at a time so single-browser headroom is enough."
  type        = string
  default     = "256Mi"
}

variable "server_memory_limit" {
  type    = string
  default = "1500Mi"
}

# -----------------------------------------------------------------------------
# Resource sizing
# -----------------------------------------------------------------------------
# Headed pod runs 3 containers — combined floor is what matters:
#   chromium  ~ 1Gi req / 4Gi limit
#   novnc     ~  64Mi req / 256Mi limit
#   cdp-proxy ~  16Mi req / 64Mi limit  (nginx-alpine; was 8Mi/32Mi for socat)
#   Total     ~ 1.08Gi req / 4.32Gi limit (matches v1's 1Gi/4Gi single-pod)
# Headless pod (single container): 384Mi req / 1.5Gi limit.
# /dev/shm: 2Gi per pod. Chrome IPC default 64MB is too small for any nontrivial page.
#
# 2026-06-07 bump (2Gi → 4Gi chromium limit): COELHO Nexus YCS runs the
# Playwright transcript service at MAX_CONCURRENT=5 simultaneous YouTube
# watch pages. Each tab peaks ~600-900 MiB (player + DOM + JS heap); at
# 5×, the prior 2Gi limit OOMKilled (Exit 137) mid-eval, producing a
# cascade of `Target page, context or browser has been closed` and a
# ~30s ECONNREFUSED window while Chromium restarted. 4Gi (≈ 5 × 800Mi
# + baseline) holds the budget with ~25% headroom.
# -----------------------------------------------------------------------------

variable "headed_chromium_memory_request" {
  description = "Bumped 768Mi → 1Gi alongside the limit so the scheduler reserves room for 5 concurrent YouTube tabs (COELHO Nexus YCS extract task)."
  type        = string
  default     = "1Gi"
}

variable "headed_chromium_memory_limit" {
  description = "Bumped 2Gi → 4Gi on 2026-06-07 to fit MAX_CONCURRENT=5 YouTube watch pages (~600-900 MiB peak each). See block-comment above."
  type        = string
  default     = "4Gi"
}

variable "headed_chromium_cpu_request" {
  type    = string
  default = "200m"
}

variable "headed_chromium_cpu_limit" {
  description = "Bumped 1500m → 2 (canonical form — K8s normalizes 2000m → 2, which trips kubernetes_manifest's consistency check; use '2'). 5 concurrent Chrome tabs at peak deserve 2 cores."
  type        = string
  default     = "2"
}

variable "headed_novnc_memory_request" {
  type    = string
  default = "64Mi"
}

variable "headed_novnc_memory_limit" {
  type    = string
  default = "256Mi"
}

variable "headless_memory_request" {
  type    = string
  default = "384Mi"
}

variable "headless_memory_limit" {
  type    = string
  default = "1500Mi"
}

variable "headless_cpu_request" {
  type    = string
  default = "200m"
}

variable "headless_cpu_limit" {
  description = "Use the canonical form '1' (not '1000m') — K8s normalizes 1000m → 1, which breaks the kubernetes_manifest provider's apply-time consistency check."
  type        = string
  default     = "1"
}

variable "shm_size" {
  description = "/dev/shm size (RAM-backed emptyDir). Chrome IPC default 64MB causes crashes on nontrivial pages. Bumped 1Gi → 2Gi on 2026-06-07 to match v1 (and to track the chromium memory bump for 5 concurrent YouTube tabs)."
  type        = string
  default     = "2Gi"
}

# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------

variable "vnc_password" {
  description = "Password for the noVNC web interface. From SOPS bundle. Reused from v1 for continuity."
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Tailscale exposure
# -----------------------------------------------------------------------------

variable "tailscale_hostname_novnc" {
  description = "Short tailnet hostname for the noVNC web UI."
  type        = string
  default     = "playwright-vnc"
}

variable "tailscale_hostname_cdp_headed" {
  description = "Short tailnet hostname for the headed CDP endpoint (laptop dev with Browser Use, Crawl4AI undetected mode)."
  type        = string
  default     = "playwright-cdp"
}

variable "tailscale_hostname_cdp_headless" {
  description = "Short tailnet hostname for the headless CDP endpoint (Knowledge Distiller laptop crawlers, Crawl4AI default bulk mode)."
  type        = string
  default     = "playwright-cdp-headless"
}

variable "tailscale_domain" {
  description = "Tailnet domain. Comes from env.hcl."
  type        = string
}

variable "tailscale_ingress_class" {
  description = "IngressClass name from the tailscale-operator unit."
  type        = string
  default     = "tailscale"
}
