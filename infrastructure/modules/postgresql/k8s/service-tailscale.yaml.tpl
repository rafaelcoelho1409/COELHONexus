# =============================================================================
# Tailscale-exposed Service for PostgreSQL (TCP 5432)
# =============================================================================
# Pattern: type=LoadBalancer + loadBalancerClass=tailscale tells the Tailscale
# operator to provision a proxy pod that joins the tailnet at the configured
# hostname and routes inbound TCP traffic to this Service's ClusterIP.
#
# This is a SECOND Service (the chart-managed one stays as ClusterIP for
# in-cluster traffic). Both target the same primary pod via shared labels.
#
# Variables interpolated:
#   ${namespace}, ${release_name}, ${tailscale_hostname}
#
# Selector:
#   Bitnami chart's primary pod has these labels:
#     app.kubernetes.io/instance: ${release_name}
#     app.kubernetes.io/name:     postgresql
#     app.kubernetes.io/component: primary
#   We match instance + component so this works whether you stay standalone
#   or scale to HA with read replicas later (only the primary gets traffic).
# =============================================================================

apiVersion: v1
kind: Service
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: postgresql
    app.kubernetes.io/component: primary
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
spec:
  type: LoadBalancer
  loadBalancerClass: tailscale
  selector:
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/name: postgresql
    app.kubernetes.io/component: primary
  ports:
    - name: postgresql
      port: 5432
      targetPort: 5432
      protocol: TCP
