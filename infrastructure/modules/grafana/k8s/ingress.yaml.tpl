# =============================================================================
# Tailscale Ingress — Grafana
# =============================================================================
# Variables interpolated:
#   ${namespace}, ${release_name}, ${tailscale_hostname}, ${tailscale_domain},
#   ${ingress_class_name}
#
# Backend Service: ${release_name} (chart's default Service name) on port 80.
# The chart's Service maps port 80 → containerPort 3000 (Grafana's listener).
#
# Homepage annotations include the mandatory `gethomepage.dev/href` (Tailscale
# Ingresses use rules[0].host: "*" wildcard, so Homepage can't auto-derive).
# `pod-selector` matches the chart's modern label format.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: grafana
    app.kubernetes.io/component: observability-ui
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Grafana"
    gethomepage.dev/group: "Observability"
    gethomepage.dev/icon: "grafana.png"
    gethomepage.dev/description: "Metrics, logs, traces (LGTM)"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "${release_name}"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=${release_name}"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                # The grafana-community chart names the Service `<release>`
                # (no chart-name suffix), unlike open-webui or pgadmin4.
                name: ${release_name}
                port:
                  number: 80
  tls:
    - hosts:
        - ${tailscale_hostname}
