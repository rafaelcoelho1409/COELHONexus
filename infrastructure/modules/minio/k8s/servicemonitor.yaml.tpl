# =============================================================================
# ServiceMonitor — MinIO Prometheus scrape config
# =============================================================================
# charts.min.io 5.4.0 doesn't ship a ServiceMonitor template even when
# `metrics.serviceMonitor.enabled: true` is set in values. We create it
# manually so Alloy/Mimir scrape MinIO's metrics endpoints.
#
# MinIO exposes metrics on the API port (9000) at:
#   /minio/v2/metrics/cluster — capacity, total buckets, nodes
#   /minio/v2/metrics/bucket  — per-bucket size, object count
#
# Prometheus auth: chart's `metrics.prometheusAuthType: public` (or v1
# default) lets the endpoints serve metrics without bearer tokens, which
# matches how Alloy is configured in this homelab.
#
# Variables interpolated: ${namespace}, ${release_name}
# =============================================================================
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: minio
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: minio
    release: kube-prometheus-stack
spec:
  selector:
    matchLabels:
      app: minio
      release: ${release_name}
      monitoring: "true"
  namespaceSelector:
    matchNames:
      - ${namespace}
  endpoints:
    # Cluster-wide metrics (capacity, total buckets, nodes)
    - port: http
      path: /minio/v2/metrics/cluster
      interval: 30s
      scrapeTimeout: 10s
    # Per-bucket metrics (size, object count per bucket)
    - port: http
      path: /minio/v2/metrics/bucket
      interval: 60s
      scrapeTimeout: 10s
