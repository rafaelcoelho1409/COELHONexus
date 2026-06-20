# =============================================================================
# Tailscale Ingress — noVNC web UI (Homepage tile YES, it's a UI)
# =============================================================================
# Backend: Service `playwright-novnc:6080` (the headed pod). Tailscale
# terminates LE TLS at the front; backend is plain HTTP — port name `http`
# (NOT `https`), so the proxy speaks HTTP to the backend.
# =============================================================================

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: playwright-novnc-tailscale
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: novnc
    app.kubernetes.io/managed-by: terraform
  annotations:
    tailscale.com/hostname: ${tailscale_hostname}
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: "Playwright noVNC"
    gethomepage.dev/group: "Automation"
    gethomepage.dev/icon: "https://playwright.dev/img/playwright-logo.svg"
    gethomepage.dev/description: "Headed browser live view"
    gethomepage.dev/href: "https://${tailscale_hostname}.${tailscale_domain}/vnc.html?autoconnect=1&resize=scale"
    gethomepage.dev/app: "playwright-headed"
    gethomepage.dev/namespace: "${namespace}"
    gethomepage.dev/pod-selector: "app.kubernetes.io/component=headed"
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: playwright-novnc
                port:
                  name: http
  tls:
    - hosts:
        - ${tailscale_hostname}
