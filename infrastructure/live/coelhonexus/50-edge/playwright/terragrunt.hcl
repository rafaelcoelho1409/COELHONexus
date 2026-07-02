# =============================================================================
# Leaf — playwright (coelhonexus standalone, 50-edge layer)
# =============================================================================
# Dual-mode browser pool for YCS transcript scraping:
#   - playwright-headed   (3-container pod): chromium + novnc + cdp-fwd
#                         CDP at :9222 (used by YCS for transcript DOM scrape)
#   - playwright-headless (1-container): chromedp/headless-shell
#                         CDP at :9224 (used by Crawl4AI bulk crawls)
#
# YCS values.yaml:
#   playwright.cdp_headed:   http://playwright-headed.playwright.svc.cluster.local:9222
#   playwright.cdp_headless: http://playwright-headless.playwright.svc.cluster.local:9224
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP dependency "tailscale_operator"
#   - DUMMY tailscale_* (Ingress resources for noVNC + CDP are inert)
#   - vnc_password from env.hcl `demo` map
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  # `//playwright` (not a trailing path) tells Terragrunt to copy the WHOLE
  # infrastructure/modules/ tree into its cache, then cd into playwright/ —
  # needed because main.tf's `module "k3d_expose_headed"` /
  # `"k3d_expose_headless"` reference a SIBLING module via a relative path
  # (same fix as every other local-expose-enabled leaf).
  source = "${get_repo_root()}/infrastructure/modules//playwright"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

generate "providers" {
  path      = "providers.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "kubernetes" {
      config_path = "${dependency.k3d.outputs.kubeconfig_path}"
    }
  EOF
}

inputs = {
  tailscale_domain        = "tailscale.local"
  tailscale_ingress_class = "tailscale"

  vnc_password = include.root.locals.env.demo.playwright_vnc_password

  # Defaults from variables.tf are appropriate:
  #   - chromium  v1.59.1-jammy
  #   - novnc     theasp/novnc:latest
  #   - cdp-proxy nginx:1.27-alpine-slim
  #   - headless  chromedp/headless-shell:latest
  #   - Headed pod resources: chromium 768Mi/2Gi + novnc 64Mi/256Mi + nginx tiny
  #   - Headless pod resources: 384Mi/1500Mi
  #   - /dev/shm: 1Gi per pod

  # Local access (k3d only) — noVNC 30482->23018, headed CDP 30483->23019,
  # headless CDP 30484->23020, mapped via:
  # `k3d cluster edit coelhonexus --port-add "23018:30482@loadbalancer"`
  # `k3d cluster edit coelhonexus --port-add "23019:30483@loadbalancer"`
  # `k3d cluster edit coelhonexus --port-add "23020:30484@loadbalancer"`
  # (run manually — not a Terraform resource, see infra/modules/k3d_expose).
  # playwright-server (Open WebUI's WS backend) deliberately NOT exposed —
  # same "no external access for UI-less backends" policy as Tailscale.
  enable_local_expose        = true
  k3d_novnc_node_port        = 30482
  k3d_cdp_headed_node_port   = 30483
  k3d_cdp_headless_node_port = 30484
}
