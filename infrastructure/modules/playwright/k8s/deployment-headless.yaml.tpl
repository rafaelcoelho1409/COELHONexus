# =============================================================================
# Deployment — playwright-headless (2-container pod: chromedp + nginx proxy)
# =============================================================================
# `chromedp/headless-shell` is purpose-built for raw CDP serving:
#   - Ships a custom-compiled headless-shell binary (Chromium-based)
#   - Binds CDP on 0.0.0.0:9222 by default
#   - MIT licensed, maintained by chromedp team
#
# However, headless-shell IS Chromium under the hood, so it inherits Chrome
# M113+'s Host-header DNS-rebinding check — requests via the k8s Service
# hostname get HTTP 500 just like the headed variant. The cdp-proxy nginx
# sidecar handles this identically: spoofs `Host: localhost:9222` upstream
# and rewrites the reflected `webSocketDebuggerUrl` in the JSON response.
#
# Same nginx ConfigMap as headed (one config, both pods mount it).
# =============================================================================

apiVersion: apps/v1
kind: Deployment
metadata:
  name: playwright-headless
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: headless
    app.kubernetes.io/managed-by: terraform
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: playwright
      app.kubernetes.io/component: headless
  template:
    metadata:
      labels:
        app.kubernetes.io/name: playwright
        app.kubernetes.io/component: headless
        app.kubernetes.io/managed-by: terraform
    spec:
      volumes:
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
        # chromedp/headless-shell — Chromium-based CDP server
        # ---------------------------------------------------------------------
        - name: headless-shell
          image: ${image}
          imagePullPolicy: IfNotPresent
          # Listens on 0.0.0.0:9222 by default. The cdp-proxy sidecar reaches
          # it via 127.0.0.1:9222 (shared pod network namespace). External
          # clients reach the cdp-proxy on 9220, never chromedp directly.
          ports:
            - name: cdp-internal
              containerPort: 9222
              protocol: TCP
          volumeMounts:
            - name: dshm
              mountPath: /dev/shm
          resources:
            requests:
              cpu: ${cpu_request}
              memory: ${memory_request}
            limits:
              cpu: ${cpu_limit}
              memory: ${memory_limit}
          livenessProbe:
            tcpSocket:
              port: 9222
            initialDelaySeconds: 15
            periodSeconds: 20
          readinessProbe:
            tcpSocket:
              port: 9222
            initialDelaySeconds: 5
            periodSeconds: 10

        # ---------------------------------------------------------------------
        # cdp-proxy sidecar — same nginx pattern as headed
        # ---------------------------------------------------------------------
        # Mounts the SAME ConfigMap as the headed pod's cdp-proxy. nginx
        # default CMD loads /etc/nginx/nginx.conf — no command/args override.
        # ---------------------------------------------------------------------
        - name: cdp-proxy
          image: ${cdp_proxy_image}
          imagePullPolicy: IfNotPresent
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
          # `scheme: HTTP` is REQUIRED — the kubernetes_manifest provider
          # aborts apply if K8s server-side defaults fill in a value not
          # present in the config. Same trap as the cpu '1' vs '1000m'
          # normalization issue (see variables.tf).
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
