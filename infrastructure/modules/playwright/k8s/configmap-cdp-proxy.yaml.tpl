# =============================================================================
# nginx config for the cdp-proxy sidecar (replaces v1 socat TCP forward)
# =============================================================================
# Why HTTP-aware proxy instead of TCP:
#   socat forwards bytes — it doesn't touch the HTTP Host header. Chrome
#   M113+ has a hardcoded DNS-rebinding-prevention check on /json/* that
#   rejects any Host header that isn't "localhost" or a literal IP. So
#   when a k8s consumer hits this Service via its DNS name
#   (playwright-headed.playwright.svc.cluster.local:9222), Chrome returns
#   500 "Host header is specified and is not an IP address or localhost."
#
#   nginx with `proxy_set_header Host localhost:9222` rewrites it inline,
#   making Chrome accept the request. WebSocket upgrade headers are also
#   forwarded so /devtools/browser/* (the actual CDP transport) works end
#   to end.
#
# This file lives as a ConfigMap; the deployment mounts it at
# /etc/nginx/nginx.conf with subPath: nginx.conf (read-only).
#
# Variables interpolated:
#   ${namespace}
# =============================================================================
apiVersion: v1
kind: ConfigMap
metadata:
  name: playwright-cdp-proxy
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: cdp-proxy
    app.kubernetes.io/managed-by: terraform
data:
  nginx.conf: |
    # nginx.conf — HTTP+WS reverse proxy for Chrome CDP with response rewrite.
    #
    # Solves THREE Chrome M113+ lockdowns (all hardcoded, no kill-switch):
    #   1. localhost-only bind  — chromium binds 127.0.0.1:9222; nginx is the
    #      only thing reaching it from the pod network. (chromedp/headless-
    #      shell binds 0.0.0.0:9222 but the same pod-local proxy pattern
    #      works — they share the network namespace.)
    #   2. Host-header DNS-rebinding check — Chrome returns 500 when Host
    #      isn't "localhost" or a literal IP. `proxy_set_header Host
    #      localhost:9222` spoofs it upstream.
    #   3. webSocketDebuggerUrl reflection — Chrome embeds whatever Host it
    #      saw into its JSON response, so consumers receive
    #      `ws://localhost:9222/devtools/browser/<id>` and try to connect
    #      back to "localhost" (their own loopback, empty). `sub_filter`
    #      rewrites it to `ws://$http_host/...` (the original client URL)
    #      so the WS upgrade reaches us, not the consumer's own pod.
    #
    # Verified: nginx:1.27-alpine-slim ships --with-http_sub_module.
    events { worker_connections 256; }
    http {
        # WebSocket upgrade map — required for /devtools/browser/<id> traffic.
        map $http_upgrade $connection_upgrade {
            default upgrade;
            ''      close;
        }
        access_log off;
        error_log /dev/stderr warn;
        server {
            listen 9220;
            proxy_http_version 1.1;
            # Lockdown #2 — spoof Host so Chrome's IsValidHost check passes.
            proxy_set_header Host localhost:9222;
            # WebSocket headers — propagated to upstream for /devtools/* upgrade.
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            # CDP sessions can run for the lifetime of an automation script.
            proxy_read_timeout 3600;
            proxy_send_timeout 3600;
            # Lockdown #3 — rewrite Chrome's reflected webSocketDebuggerUrl
            # so consumers connect back to us, not to their own localhost.
            # `$http_host` captures the client's original Host header (e.g.
            # `playwright-headed.playwright.svc.cluster.local:9222`).
            #
            # Only application/json bodies need this; WS upgrade responses
            # are 101 with no body so sub_filter is dormant. Length-changing
            # rewrites: nginx auto-strips Content-Length and switches to
            # Transfer-Encoding: chunked.
            sub_filter_types application/json;
            sub_filter_once off;
            sub_filter "ws://localhost:9222" "ws://$http_host";
            location / {
                proxy_pass http://127.0.0.1:9222;
            }
        }
    }
