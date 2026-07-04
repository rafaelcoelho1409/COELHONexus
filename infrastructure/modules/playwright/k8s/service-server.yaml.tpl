# =============================================================================
# Service — playwright-server (Playwright WS protocol endpoint, in-cluster)
# =============================================================================
# In-cluster DNS: playwright-server.playwright.svc.cluster.local:3000
#
# Open WebUI consumer config:
#   WEB_LOADER_ENGINE=playwright
#   PLAYWRIGHT_WS_URL=ws://playwright-server.playwright.svc.cluster.local:3000
#
# No external Ingress — internal-only API, no UI. Per memory
# feedback_no_external_ingress_for_uiless_backends.
# =============================================================================

apiVersion: v1
kind: Service
metadata:
  name: playwright-server
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: server
    app.kubernetes.io/managed-by: terraform
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: server
  ports:
    - name: ws
      port: 3000
      targetPort: 3000
      protocol: TCP
