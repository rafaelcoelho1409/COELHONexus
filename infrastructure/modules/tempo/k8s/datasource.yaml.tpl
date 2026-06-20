# =============================================================================
# Grafana datasource — Tempo (with cross-pillar links to Loki + Mimir)
# =============================================================================
# ConfigMap labeled `grafana_datasource: "1"` is auto-discovered by Grafana's
# sidecar (running in the grafana namespace) and imported at runtime.
#
# UID `tempo` is the canonical reference. The datasource also wires up:
#   - tracesToLogsV2 → Loki (UID `loki`): click any span → jump to Loki
#     filtered by trace_id/span_id
#   - tracesToMetrics → Mimir (UID `mimir`): click any span → jump to Mimir
#     for service-graph / RED-style metrics
#   - serviceMap → Mimir: render service topology from span metrics
#   - nodeGraph → enabled: Grafana's built-in trace graph view
#
# These cross-links require Loki + Mimir datasources to also be present (UIDs
# `loki` and `mimir` respectively). Both are shipped by their own modules.
# =============================================================================

apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-tempo
  namespace: ${namespace}
  labels:
    grafana_datasource: "1"
    app.kubernetes.io/name: tempo
    app.kubernetes.io/component: grafana-datasource
    app.kubernetes.io/managed-by: terraform
data:
  tempo-datasource.yaml: |-
    apiVersion: 1
    datasources:
      - name: Tempo
        uid: tempo
        type: tempo
        url: http://${service_name}.${namespace}.svc.cluster.local:3200
        access: proxy
        jsonData:
          httpMethod: GET
          tracesToLogsV2:
            datasourceUid: loki
            filterByTraceID: true
            filterBySpanID: true
          tracesToMetrics:
            datasourceUid: mimir
          serviceMap:
            datasourceUid: mimir
          nodeGraph:
            enabled: true
