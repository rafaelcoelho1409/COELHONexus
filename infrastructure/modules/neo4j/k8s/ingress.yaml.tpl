# =============================================================================
# Tailscale Ingress — Neo4j Browser (HTTPS, port 7474)
# =============================================================================
# TLS terminated by Tailscale's LE-issued cert. Backend is plain HTTP on 7474.
# Bolt port 7687 is NOT exposed here — it lives at a separate hostname via
# Tailscale LoadBalancer (see bolt-service.yaml.tpl) so the Browser's wss://
# connection to TLS-wrapped Bolt works without mixed-content blocks.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: neo4j
    app.kubernetes.io/component: graph-db
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Neo4j"
    gethomepage.dev/group: "Databases"
    gethomepage.dev/icon: "neo4j.png"
    gethomepage.dev/description: "Graph Database"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "${release_name}"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app=neo4j,helm.neo4j.com/instance=${release_name}"
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
                  number: 7474
  tls:
    - hosts:
        - ${tailscale_hostname}
