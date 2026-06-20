# =============================================================================
# Tailscale Ingress — Qdrant (Dashboard UI + REST API)
# =============================================================================
# Backend: <release>:6333 (REST API, hosts the Dashboard at /dashboard).
# gRPC port (6334) is NOT exposed externally — Tailscale Ingress doesn't
# pass HTTP/2 cleanly; use the in-cluster Service for gRPC.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: qdrant
    app.kubernetes.io/component: vector-db
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Qdrant"
    gethomepage.dev/group: "Databases"
    gethomepage.dev/icon: "qdrant.png"
    gethomepage.dev/description: "Vector database"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}/dashboard"
    gethomepage.dev/app: "${release_name}"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app.kubernetes.io/name=qdrant,app.kubernetes.io/instance=${release_name}"
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
                  number: 6333
  tls:
    - hosts:
        - ${tailscale_hostname}
