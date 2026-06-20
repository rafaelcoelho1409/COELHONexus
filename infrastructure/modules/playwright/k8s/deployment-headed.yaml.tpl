# =============================================================================
# Deployment — playwright-headed (3-container pod, all pre-built)
# =============================================================================
# Containers in this pod share network namespace + selected volumes:
#
#   - novnc      : owns Xvfb on display :0, runs x11vnc + noVNC + websockify.
#                  Shares /tmp/.X11-unix so chromium can use the X server.
#                  Exposes web UI on port 8080.
#
#   - chromium   : official Playwright image. Doesn't run its own Xvfb;
#                  connects to novnc's display :0 via shared X11 socket.
#                  Chrome binds 127.0.0.1:9222 (M113+ forces this; --remote-
#                  debugging-address=0.0.0.0 is silently rewritten to localhost).
#                  Launched with --remote-allow-origins=* so WebSocket upgrade
#                  is accepted from any caller (the Origin check is a separate
#                  M113+ lockdown from the Host check handled by cdp-proxy).
#
#   - cdp-proxy  : nginx-alpine. HTTP-aware reverse proxy listening on
#                  0.0.0.0:9220, forwarding to 127.0.0.1:9222 with
#                  `Host: localhost:9222` rewrite. Replaces v1's alpine/socat
#                  (TCP-only) — socat correctly bypassed the localhost-bind
#                  lockdown but couldn't fix the Host-header DNS-rebinding
#                  check that causes Chrome to return 500 when reached via
#                  any k8s Service / Tailscale hostname.
#
# Service `playwright-headed` maps port 9222 → targetPort 9220 → cdp-proxy
# (nginx) → 127.0.0.1:9222 (chromium CDP). Consumers in any namespace
# connect via `playwright-headed.playwright.svc.cluster.local:9222` and get
# a working CDP session — no client-side Host-header tricks required.
# =============================================================================

apiVersion: apps/v1
kind: Deployment
metadata:
  name: playwright-headed
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headed
    app.kubernetes.io/managed-by: terraform
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: playwright
      app.kubernetes.io/component: headed
  template:
    metadata:
      labels:
        app.kubernetes.io/name: playwright
        app.kubernetes.io/component: headed
        app.kubernetes.io/managed-by: terraform
    spec:
      volumes:
        - name: x11-socket
          emptyDir:
            medium: Memory
            sizeLimit: 32Mi
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: ${shm_size}
        - name: cdp-proxy-config
          configMap:
            name: playwright-cdp-proxy
            items:
              - key: nginx.conf
                path: nginx.conf

      containers:
        # ---------------------------------------------------------------------
        # noVNC sidecar — owns the X server (1920x1080, 16:9), exposes web UI
        # ---------------------------------------------------------------------
        # theasp/novnc ships supervisord with `x11vnc -nopw` hardcoded. To
        # inject our SOPS-stored VNC password we wrap the entrypoint: store
        # the password file, sed-patch the supervisord conf to use -rfbauth,
        # then chain to the image's original /entrypoint.sh.
        # ---------------------------------------------------------------------
        - name: novnc
          image: ${novnc_image}
          imagePullPolicy: IfNotPresent
          command: ["bash", "-c"]
          args:
            - |
              set -e
              mkdir -p /tmp/.vnc
              x11vnc -storepasswd "$VNC_PASSWORD" /tmp/.vnc/passwd
              # theasp/novnc supervisord layout (verified inside the image):
              #   /app/supervisord.conf         (entry point)
              #   /app/conf.d/x11vnc.conf       (per-program: x11vnc -forever -shared)
              # Append -rfbauth flag to the x11vnc command line.
              sed -i 's|^command=x11vnc |command=x11vnc -rfbauth /tmp/.vnc/passwd |' /app/conf.d/x11vnc.conf
              exec /app/entrypoint.sh
          env:
            # 1600x900 = 16:9, comfortable Chrome viewport, fits any laptop
            # browser without spilling. noVNC URL uses ?resize=scale to do
            # client-side scaling for smaller browser windows (theasp's Xvfb
            # is built without RandR, so resize=remote doesn't work).
            - name: DISPLAY_WIDTH
              value: "1600"
            - name: DISPLAY_HEIGHT
              value: "900"
            - name: RUN_XTERM
              value: "no"
            - name: RUN_FLUXBOX
              value: "yes"
            - name: VNC_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ${vnc_secret_name}
                  key: VNC_PASSWORD
          ports:
            - name: novnc
              containerPort: 8080
              protocol: TCP
          volumeMounts:
            - name: x11-socket
              mountPath: /tmp/.X11-unix
          resources:
            requests:
              cpu: 50m
              memory: ${novnc_memory_request}
            limits:
              memory: ${novnc_memory_limit}
          livenessProbe:
            tcpSocket:
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 20
          readinessProbe:
            tcpSocket:
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10

        # ---------------------------------------------------------------------
        # Chromium — connects to novnc's :0 display, exposes raw CDP on localhost
        # ---------------------------------------------------------------------
        - name: chromium
          image: ${chromium_image}
          imagePullPolicy: IfNotPresent
          command: ["bash", "-c"]
          args:
            - |
              set -e
              # Wait for noVNC sidecar's Xvfb socket
              for i in $(seq 1 30); do
                if [ -S /tmp/.X11-unix/X0 ]; then break; fi
                echo "waiting for Xvfb (:0) ..."
                sleep 1
              done
              # Resolve chromium binary (path varies by Playwright version)
              CHROME=$(ls /ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)
              if [ -z "$CHROME" ]; then
                echo "FATAL: chrome binary not found under /ms-playwright/" >&2
                exit 1
              fi
              echo "Launching $CHROME on display :0"
              exec "$CHROME" \
                --remote-debugging-port=9222 \
                --remote-debugging-address=127.0.0.1 \
                --remote-allow-origins=* \
                --display=:0 \
                --no-sandbox \
                --no-first-run \
                --no-default-browser-check \
                --disable-blink-features=AutomationControlled \
                --window-size=1600,900 \
                --user-data-dir=/tmp/chrome-headed \
                --lang=en-US \
                about:blank
          env:
            - name: DISPLAY
              value: ":0"
          volumeMounts:
            - name: x11-socket
              mountPath: /tmp/.X11-unix
            - name: dshm
              mountPath: /dev/shm
          resources:
            requests:
              cpu: ${chromium_cpu_request}
              memory: ${chromium_memory_request}
            limits:
              cpu: ${chromium_cpu_limit}
              memory: ${chromium_memory_limit}

        # ---------------------------------------------------------------------
        # cdp-proxy sidecar — nginx-alpine reverse proxy
        # ---------------------------------------------------------------------
        # Replaces the v1 alpine/socat TCP forwarder. Same target (Chrome's
        # CDP at 127.0.0.1:9222) but HTTP-aware — also rewrites the Host
        # header to "localhost:9222" so Chrome's M113+ DNS-rebinding check
        # accepts requests that arrive via the k8s Service hostname or
        # Tailscale Ingress hostname. Without this, /json/version returns
        # HTTP 500 and Playwright's connect_over_cdp() fails immediately.
        # ---------------------------------------------------------------------
        - name: cdp-proxy
          image: ${cdp_proxy_image}
          imagePullPolicy: IfNotPresent
          # nginx's CMD is ["nginx", "-g", "daemon off;"] and it loads
          # /etc/nginx/nginx.conf by default. We mount our ConfigMap at that
          # exact path, so no command/args override is needed. (Earlier I
          # passed args: ["-c", "/etc/nginx/nginx.conf"] but the image's
          # docker-entrypoint.sh treats leading `-c` as its own option and
          # crashes with "illegal option -c". Dropping args delegates to the
          # default CMD which loads our config from the canonical path.)
          ports:
            - name: cdp
              containerPort: 9220
              protocol: TCP
          volumeMounts:
            - name: cdp-proxy-config
              mountPath: /etc/nginx/nginx.conf
              subPath: nginx.conf
              readOnly: true
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
            limits:
              memory: 64Mi
          # Probe via /json/version — this verifies BOTH the proxy AND that
          # Chrome's CDP is responding through it (a TCP probe on 9220 only
          # confirmed nginx was listening, not that Chrome was reachable).
          #
          # `scheme: HTTP` is REQUIRED. Without it, the K8s API server fills
          # in the default server-side, then the kubernetes_manifest provider
          # sees a value that wasn't in our config and aborts apply with
          # "Provider produced inconsistent result after apply." Same family
          # of bug as the cpu '1' vs '1000m' normalization issue.
          livenessProbe:
            httpGet:
              path: /json/version
              port: 9220
              scheme: HTTP
            initialDelaySeconds: 30
            periodSeconds: 20
          readinessProbe:
            httpGet:
              path: /json/version
              port: 9220
              scheme: HTTP
            initialDelaySeconds: 15
            periodSeconds: 10
