# =============================================================================
# Grafana datasource — Mimir
# =============================================================================
# ConfigMap labeled `grafana_datasource: "1"` is auto-discovered by Grafana's
# sidecar (running in the grafana namespace) which imports it at runtime — no
# Grafana restart needed.
#
# UID `mimir` (lowercase) is the canonical reference used by all dashboards
# in this stack. Every dashboard ConfigMap references this UID for its
# Prometheus-style queries.
# =============================================================================

apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-mimir
  namespace: ${namespace}
  labels:
    grafana_datasource: "1"
    app.kubernetes.io/name: mimir
    app.kubernetes.io/component: grafana-datasource
    app.kubernetes.io/managed-by: terraform
data:
  mimir-datasource.yaml: |-
    apiVersion: 1
    datasources:
      - name: Mimir
        uid: mimir
        type: prometheus
        url: http://${gateway_service}.${namespace}.svc.cluster.local/prometheus
        access: proxy
        isDefault: true
        jsonData:
          httpMethod: POST
          timeInterval: 30s
          manageAlerts: true
          prometheusType: Mimir
          prometheusVersion: 3.0.0
