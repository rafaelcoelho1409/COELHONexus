# =============================================================================
# Tailscale Ingress — Alloy OTLP HTTP (port 4318)
# =============================================================================
# Backend: <release>:4318 (alloy.extraPorts in the Helm values opens this).
# Use cases:
#   - Laptop Python apps: OTEL_EXPORTER_OTLP_ENDPOINT=https://alloy.<domain>
#     OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
#   - External services on the tailnet shipping telemetry without entering
#     the k3d cluster.
#
# No Homepage annotations — Alloy is a data ingest endpoint, no UI.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/component: telemetry-collector
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
                name: ${release_name}
                port:
                  number: 4318
  tls:
    - hosts:
        - ${tailscale_hostname}
