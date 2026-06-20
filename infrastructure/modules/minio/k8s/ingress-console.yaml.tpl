# =============================================================================
# Tailscale Ingress — MinIO Console (Web UI on port 9001)
# =============================================================================
# Variables interpolated:
#   ${namespace}, ${release_name}, ${tailscale_hostname}, ${tailscale_domain},
#   ${ingress_class_name}
#
# Backend Service: ${release_name}-console (the chart auto-creates this
# Service for the Console). Distinct from the API Service ${release_name}.
#
# Homepage annotations:
#   - `href` is mandatory because Tailscale Ingresses use rules[0].host: "*"
#     (wildcard) — Homepage cannot derive the URL from spec alone.
#   - `pod-selector: "app=${release_name}"` is required because charts.min.io
#     5.4.0 uses the OLDER label `app=minio` (not modern app.kubernetes.io/name).
#     Without this, the Homepage tile shows "not found" badge.
#   - KEBAB-CASE `pod-selector` (camelCase `podSelector` is silently ignored).
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-console-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: minio-console
    app.kubernetes.io/component: storage
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "MinIO Console"
    gethomepage.dev/group: "Infrastructure"
    gethomepage.dev/icon: "minio.png"
    gethomepage.dev/description: "S3-compatible object storage UI"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "${release_name}"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app=${release_name}"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${release_name}-console
                port:
                  number: 9001
  tls:
    - hosts:
        - ${tailscale_hostname}
