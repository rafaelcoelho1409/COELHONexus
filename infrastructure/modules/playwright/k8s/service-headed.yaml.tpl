# =============================================================================
# Service — playwright-headed (CDP endpoint, in-cluster)
# =============================================================================
# In-cluster DNS: playwright-headed.playwright.svc.cluster.local:9222
# targetPort 9220 hits the cdp-proxy sidecar (nginx-alpine) in the pod,
# which HTTP-reverse-proxies to chromium's 127.0.0.1:9222 AND rewrites the
# Host header to "localhost:9222" so Chrome M113+'s DNS-rebinding check
# accepts the request. External port stays 9222 so consumers (Nexus
# YouTube Ask, Browser Use, Crawl4AI undetected) don't change their config.
# =============================================================================

apiVersion: v1
kind: Service
metadata:
  name: playwright-headed
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headed
    app.kubernetes.io/managed-by: terraform
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headed
  ports:
    - name: cdp
      port: 9222
      targetPort: 9220
      protocol: TCP
