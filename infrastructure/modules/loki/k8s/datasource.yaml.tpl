# =============================================================================
# Grafana datasource — Loki
# =============================================================================
# ConfigMap labeled `grafana_datasource: "1"` is auto-discovered by Grafana's
# sidecar (running in the grafana namespace) and imported at runtime.
#
# UID `loki` is the canonical reference used by every dashboard ConfigMap.
# In Monolithic mode the SingleBinary Service is named `<release>` and
# serves on port 3100 — no separate gateway hop needed.
# =============================================================================

apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-loki
  namespace: ${namespace}
  labels:
    grafana_datasource: "1"
    app.kubernetes.io/name: loki
    app.kubernetes.io/component: grafana-datasource
    app.kubernetes.io/managed-by: terraform
data:
  loki-datasource.yaml: |-
    apiVersion: 1
    datasources:
      - name: Loki
        uid: loki
        type: loki
        url: http://${service_name}.${namespace}.svc.cluster.local:3100
        access: proxy
        jsonData:
          maxLines: 1000
          timeout: 60
          derivedFields:
            - datasourceUid: tempo
              matcherRegex: "trace_id=([0-9a-f]+)"
              name: TraceID
              url: "$${__value.raw}"
              urlDisplayLabel: "View Trace"
