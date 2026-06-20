# =============================================================================
# Tailscale Ingress — Langfuse web UI + API
# =============================================================================
# Backend: Service `${release_name}-web` on port 3000.
# Tailscale terminates Let's Encrypt TLS at the front; backend HTTP only.
# All Homepage annotations included per memory feedback_homepage_href_annotation.
# Icon: `si-langfuse` (Simple Icons slug — verified reachable on cdn.simpleicons.org).
# =============================================================================
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${release_name}-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: langfuse
    app.kubernetes.io/component: ui
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Langfuse"
    gethomepage.dev/group: "AI"
    gethomepage.dev/icon: "https://raw.githubusercontent.com/langfuse/langfuse/main/web/public/icon256.png"
    gethomepage.dev/description: "LLM observability + tracing (LangChain/OpenAI/Anthropic)"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}"
    gethomepage.dev/app: "${release_name}-web"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app=web"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${release_name}-web
                port:
                  number: 3000
  tls:
    - hosts:
        - ${tailscale_hostname}
