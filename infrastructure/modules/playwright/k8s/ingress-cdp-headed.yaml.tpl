# =============================================================================
# Tailscale Ingress — Headed CDP (NO Homepage tile, data-plane endpoint)
# =============================================================================
# Backend: Service `playwright-headed:9222`. Tailscale terminates LE TLS;
# backend is plain HTTP — port name `cdp`, the proxy speaks HTTP+WS upgrade.
#
# Use case: laptop scripts (Browser Use, Crawl4AI undetected) connecting to
# the in-cluster headed Chrome from outside the cluster:
#
#   from playwright.async_api import async_playwright
#   async with async_playwright() as p:
#       browser = await p.chromium.connect_over_cdp(
#           "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
#       )
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: playwright-cdp-headed-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: cdp-headed
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
                name: playwright-headed
                port:
                  name: cdp
  tls:
    - hosts:
        - ${tailscale_hostname}
