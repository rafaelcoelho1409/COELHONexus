# =============================================================================
# Tailscale Ingress — Kibana UI (ECK-deployed)
# =============================================================================
# ECK creates Service `kibana-kb-http` with port name `https` on 5601.
# Tailscale's Ingress controller forwards to the backend over HTTPS when the
# backend port is named `https` (or numbered 443). Referencing the port by
# NAME (not number) keeps in-cluster TLS intact instead of disabling ECK's
# self-signed cert — defense in depth without proxy "EOF" errors.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kibana-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: kibana
    app.kubernetes.io/component: search-ui
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Kibana"
    gethomepage.dev/group: "Observability"
    gethomepage.dev/icon: "kibana.png"
    gethomepage.dev/description: "Elasticsearch UI"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "kibana-kb"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "kibana.k8s.elastic.co/name=kibana"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: kibana-kb-http
                port:
                  name: https
  tls:
    - hosts:
        - ${tailscale_hostname}
