# =============================================================================
# Service — playwright-headless (CDP endpoint, in-cluster + external)
# =============================================================================
# In-cluster DNS: playwright-headless.playwright.svc.cluster.local:9224
# Backend cdp-proxy sidecar (nginx) listens on 9220 and HTTP-reverse-proxies
# to chromedp/headless-shell at 127.0.0.1:9222 with `Host: localhost` rewrite
# AND `sub_filter` rewriting `webSocketDebuggerUrl` in JSON responses to
# point back at the client's original URL. Service maps external 9224 (kept
# for Nexus's existing PLAYWRIGHT_CDP_HEADLESS env) → cdp-proxy's 9220.
# Used for benign bulk crawling (Crawl4AI default, Knowledge Distiller,
# generic web fetch).
# =============================================================================

apiVersion: v1
kind: Service
metadata:
  name: playwright-headless
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headless
    app.kubernetes.io/managed-by: terraform
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headless
  ports:
    - name: cdp
      port: 9224
      targetPort: 9220
      protocol: TCP
