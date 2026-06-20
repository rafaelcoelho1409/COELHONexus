# =============================================================================
# playwright module — provider requirements
# =============================================================================
# No docker provider needed: all images are pre-built (chromedp/headless-shell,
# mcr.microsoft.com/playwright, theasp/novnc, nginx-alpine). The pods compose
# them directly — zero custom Dockerfile, zero build time.
# =============================================================================

terraform {
  required_version = ">= 1.10"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
  }
}
