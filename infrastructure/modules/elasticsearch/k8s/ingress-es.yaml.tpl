# =============================================================================
# Tailscale Ingress — Elasticsearch HTTPS REST API
# =============================================================================
# Backend: ECK Service `elasticsearch-es-http` with port name `https` on 9200.
# Tailscale Ingress speaks HTTPS to the backend when the port is named `https`
# (or numbered 443) — referencing by NAME keeps ECK's in-cluster TLS intact.
# At the front, Tailscale terminates TLS with its own LE cert; clients see a
# clean LE-trusted endpoint.
#
# Use case: laptop scripts during Nexus development (e.g. AsyncElasticsearch
# pointed at https://elasticsearch.<domain>:443). Apps deployed in-cluster
# should use the in-cluster URL instead (no proxy hop, faster).
#
# NO Homepage tile — ES API is a data-plane endpoint, not a tile-worthy URL.
# Kibana provides the UI for browsing.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: elasticsearch-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: elasticsearch
    app.kubernetes.io/component: search-api
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
                name: elasticsearch-es-http
                port:
                  name: https
  tls:
    - hosts:
        - ${tailscale_hostname}
