# =============================================================================
# Tailscale Ingress — ArgoCD Web UI
# =============================================================================
# Backend: <release>-server:80 (chart's HTTP port, since server runs --insecure
# and TLS is terminated at the Tailscale proxy).
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: argocd
    app.kubernetes.io/component: gitops
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "ArgoCD"
    gethomepage.dev/group: "Development"
    gethomepage.dev/icon: "argo-cd.png"
    gethomepage.dev/description: "GitOps continuous delivery"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "${release_name}-server"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app.kubernetes.io/name=argocd-server,app.kubernetes.io/instance=${release_name}"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${release_name}-server
                port:
                  number: 80
  tls:
    - hosts:
        - ${tailscale_hostname}
