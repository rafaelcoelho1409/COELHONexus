# =============================================================================
# Tailscale Ingress for Rancher
# =============================================================================
# When applied, the Tailscale operator spawns a proxy pod that registers as
# Tailscale node "${tailscale_hostname}" → reachable at
# https://${tailscale_hostname}.${tailscale_domain} from any tailnet member.
#
# Variables interpolated by templatefile():
#   ${namespace}, ${release_name}, ${tailscale_hostname}, ${tailscale_domain},
#   ${ingress_class_name}
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: rancher
    app.kubernetes.io/managed-by: terraform
  annotations:
    # Explicit Tailnet hostname (also derivable from tls.hosts[0], but this is
    # more readable and matches the convention used across all v2 Ingresses).
    tailscale.com/hostname: ${tailscale_hostname}
    # Homepage auto-discovery. `href` is REQUIRED for Tailscale Ingresses
    # because rules[0].host is `*` (wildcard) — Homepage can't auto-derive the URL.
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Rancher"
    gethomepage.dev/group: "Infrastructure"
    gethomepage.dev/icon: "rancher.png"
    gethomepage.dev/description: "Kubernetes management"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    # Pod-status badge: Homepage has no disable mechanism. `app` + `namespace` set
    # the lookup target. Rancher's chart uses the OLD label `app=rancher` (not the
    # modern `app.kubernetes.io/name=rancher`), so we override with `pod-selector`
    # (KEBAB-CASE — `podSelector` camelCase is silently ignored, that was a bug).
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
                name: ${release_name}
                port:
                  number: 443
  tls:
    - hosts:
        - ${tailscale_hostname}
