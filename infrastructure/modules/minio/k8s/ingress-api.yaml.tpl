# =============================================================================
# Tailscale Ingress — MinIO S3 API (port 9000)
# =============================================================================
# Variables interpolated:
#   ${namespace}, ${release_name}, ${tailscale_hostname}, ${tailscale_domain},
#   ${ingress_class_name}
#   (${tailscale_domain} is currently unused — kept in the templatefile call
#   for symmetry with the console ingress and future re-introduction of
#   Homepage annotations if desired.)
#
# Backend Service: ${release_name} (the API Service, charts.min.io default).
# This is the S3 endpoint that other services point at: GitLab, Mimir/Loki/
# Tempo, MLflow, backup CronJobs, external mc/mcli clients.
#
# Homepage: NOT registered here (no gethomepage.dev/* annotations). The
# Console tile is enough to represent MinIO on the dashboard; the S3 API
# is a programmatic endpoint, not a UI to click into. This keeps the
# Homepage tile count tight.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-api-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: minio-api
    app.kubernetes.io/component: storage
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
spec:
  ingressClassName: ${ingress_class_name}
  defaultBackend:
    service:
      name: ${release_name}
      port:
        number: 9000
  tls:
    - hosts:
        - ${tailscale_hostname}
