# =============================================================================
# Alloy ClusterRole — kubelet/cAdvisor scrape access
# =============================================================================
# The Alloy chart's default rbac.rules cover discovery (pods/services/endpoints/
# ingresses) but NOT nodes/proxy — needed for our prometheus.scrape.kubelet
# and prometheus.scrape.kubelet_cadvisor jobs that hit
# /api/v1/nodes/<name>/proxy/metrics(/cadvisor).
#
# Without this, kubelet scrapes return 403 Forbidden and Mimir gets no
# container_* / node-level metrics. Added here as a separate ClusterRole
# rather than overriding the chart's full rbac.rules list (avoids the
# Helm map-replace footgun if upstream chart adds new default rules).
# =============================================================================
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ${name}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/managed-by: terraform
    app.kubernetes.io/component: kubelet-scrape-rbac
rules:
  - apiGroups: [""]
    resources:
      - nodes
      - nodes/metrics
      - nodes/stats
      - nodes/proxy
    verbs: ["get", "list", "watch"]
  # /metrics access on the kubelet via non-resource URLs (some k8s distros require this).
  - nonResourceURLs:
      - /metrics
      - /metrics/cadvisor
      - /metrics/resource
    verbs: ["get"]
