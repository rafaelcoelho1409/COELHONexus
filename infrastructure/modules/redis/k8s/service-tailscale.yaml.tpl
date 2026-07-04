# =============================================================================
# Externally-exposed Service for Redis (TCP 6379) — OPTIONAL
# =============================================================================
# Same external LoadBalancer pattern as Postgres. Created only when
# external exposure is enabled.
#
# Variables interpolated: ${namespace}, ${release_name}, plus the target hostname value below
# =============================================================================
apiVersion: v1
kind: Service
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: redis
    app.kubernetes.io/component: master
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
spec:
  type: LoadBalancer
  loadBalancerClass: tailscale
  selector:
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/name: redis
    app.kubernetes.io/component: master
  ports:
    - name: redis
      port: 6379
      targetPort: 6379
      protocol: TCP
