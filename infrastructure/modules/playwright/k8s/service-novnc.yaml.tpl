# =============================================================================
# Service — playwright-novnc (web UI, fronts the headed pod's noVNC sidecar)
# =============================================================================
# Selects the headed pod (which owns the noVNC sidecar). The external Ingress
# `playwright-vnc.<domain>` points here for laptop debugging — watch live as
# the headed Chrome scrapes a page.
#
# theasp/novnc serves the noVNC web UI on container port 8080. We keep external
# port 6080 to match v1's URL convention (external Ingress backend uses port
# name `http` from this Service).
# =============================================================================

apiVersion: v1
kind: Service
metadata:
  name: playwright-novnc
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: novnc
    app.kubernetes.io/managed-by: terraform
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headed
  ports:
    - name: http
      port: 6080
      targetPort: 8080
      protocol: TCP
