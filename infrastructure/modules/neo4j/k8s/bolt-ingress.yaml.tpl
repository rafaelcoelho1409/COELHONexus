# =============================================================================
# Tailscale Ingress — Neo4j Bolt over WSS (port 7687 backend)
# =============================================================================
# Why a SECOND Ingress instead of a LoadBalancer Service:
#   - Tailscale operator auto-provisions Let's Encrypt certs ONLY for L7
#     Ingress resources. LoadBalancer Services (L4 TCP) get tailnet IPs but
#     no certs — that meant our previous setup served a self-signed cert,
#     and Browsers flag any wss:// to self-signed as "not secure".
#   - With a Tailscale Ingress, Tailscale terminates TLS at the proxy with
#     an LE-issued cert (trusted by all browsers natively).
#   - Tailscale's HTTP proxy passes WebSocket Upgrade headers transparently,
#     so Neo4j's Bolt port (which natively supports WebSocket upgrades on
#     the same 7687 socket as plain Bolt) just works.
#
# Browser URL becomes: bolt+s://neo4j-bolt.<domain>
#   - port 443 implicit (Tailscale Ingress listens there)
#   - bolt+s:// scheme = encrypted Bolt (JS driver opens wss://...)
#   - LE cert means clean lock 🔒 in address bar
#
# In-cluster Nexus apps still use plain bolt://<release>.<ns>:7687 — they
# bypass Tailscale entirely (no proxy hop, no TLS overhead).
#
# NO Homepage tile — Bolt is a data-plane endpoint, not a tile-worthy URL.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-bolt-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: neo4j-bolt
    app.kubernetes.io/component: graph-db-bolt
    app.kubernetes.io/instance: ${release_name}
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
                  number: 7687
  tls:
    - hosts:
        - ${tailscale_hostname}
