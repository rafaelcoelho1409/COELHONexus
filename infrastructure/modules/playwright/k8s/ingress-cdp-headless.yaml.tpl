# =============================================================================
# Tailscale Ingress — Headless CDP (NO Homepage tile, data-plane endpoint)
# =============================================================================
# Backend: Service `playwright-headless:9224`. Tailscale terminates LE TLS;
# backend is plain HTTP — port name `cdp`, the proxy speaks HTTP+WS upgrade.
#
# Use case: Knowledge Distiller crawlers (and other laptop-side bulk crawl
# tooling) connecting to the in-cluster headless Chrome from outside the
# cluster. Crawl4AI default mode also lands here.
#
#   from playwright.async_api import async_playwright
#   async with async_playwright() as p:
#       browser = await p.chromium.connect_over_cdp(
#           "https://playwright-cdp-headless.YOUR_TAILNET_DOMAIN.ts.net"
#       )
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: playwright-cdp-headless-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: cdp-headless
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: playwright-headless
                port:
                  name: cdp
  tls:
    - hosts:
        - ${tailscale_hostname}
