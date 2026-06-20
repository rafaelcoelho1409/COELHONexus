# =============================================================================
# playwright module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where the two Playwright pods live."
  value       = kubernetes_namespace_v1.playwright.metadata[0].name
}

output "headed_image" {
  description = "Headed pod's chromium image (official Playwright)."
  value       = var.chromium_image
}

output "headless_image" {
  description = "Headless pod's image (chromedp/headless-shell)."
  value       = var.headless_image
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — for downstream apps (Nexus, etc.)
# -----------------------------------------------------------------------------

output "cdp_headed_url" {
  description = "Headed CDP endpoint, in-cluster. For YouTube Ask, Browser Use, Crawl4AI undetected. Use with playwright.chromium.connect_over_cdp(...)."
  value       = "http://playwright-headed.${var.namespace}.svc.cluster.local:9222"
}

output "cdp_headless_url" {
  description = "Headless CDP endpoint, in-cluster. For benign bulk crawling (Crawl4AI default, Knowledge Distiller, generic web fetch)."
  value       = "http://playwright-headless.${var.namespace}.svc.cluster.local:9224"
}

output "ws_server_url" {
  description = "Playwright WS-protocol endpoint, in-cluster. For Open WebUI's web-loader engine (PLAYWRIGHT_WS_URL=ws://...:3000). NOT CDP — Playwright's native protocol."
  value       = "ws://playwright-server.${var.namespace}.svc.cluster.local:3000"
}

output "ws_server_playwright_version" {
  description = "Playwright npm version running on the WS server. Clients (e.g. Open WebUI's playwright==X.Y.Z) MUST match exactly or the protocol handshake fails."
  value       = var.server_playwright_version
}

output "novnc_url_in_cluster" {
  description = "In-cluster noVNC URL (mostly for sanity checks; humans should use the Tailscale URL)."
  value       = "http://playwright-novnc.${var.namespace}.svc.cluster.local:6080"
}

# -----------------------------------------------------------------------------
# External (Tailnet) endpoints
# -----------------------------------------------------------------------------

output "novnc_url_external" {
  description = "noVNC web UI URL (HTTPS via Tailscale Ingress). Append `/vnc.html?autoconnect=1&resize=remote` for direct viewer."
  value       = "https://${var.tailscale_hostname_novnc}.${var.tailscale_domain}"
}

output "cdp_headed_url_external" {
  description = "Headed CDP endpoint over Tailscale (LE TLS). For laptop dev with Browser Use / Crawl4AI undetected."
  value       = "https://${var.tailscale_hostname_cdp_headed}.${var.tailscale_domain}"
}

output "cdp_headless_url_external" {
  description = "Headless CDP endpoint over Tailscale (LE TLS). For Knowledge Distiller laptop crawlers / Crawl4AI default bulk mode."
  value       = "https://${var.tailscale_hostname_cdp_headless}.${var.tailscale_domain}"
}
