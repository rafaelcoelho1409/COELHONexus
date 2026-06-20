# =============================================================================
# Deployment — playwright-server (Playwright WS protocol, NOT CDP)
# =============================================================================
# A THIRD execution mode alongside headed (CDP) and headless (CDP):
#
#   - Runs `npx playwright run-server --port 3000 --host 0.0.0.0`
#   - Exposes Playwright's NATIVE WebSocket protocol (NOT Chrome DevTools
#     Protocol). Clients connect via `chromium.connect(ws://host:3000/`).
#   - Consumed by Open WebUI's web-loader engine (WEB_LOADER_ENGINE=playwright,
#     PLAYWRIGHT_WS_URL=ws://playwright-server.playwright.svc.cluster.local:3000)
#
# Why a separate pod and not a sidecar to headed/headless:
#   - Different image tag (1.58.0 vs 1.59.1) — Open WebUI pins playwright==1.58.0
#     and client/server versions MUST match exactly (Playwright errors out on
#     version handshake mismatch).
#   - Different exec mode — `run-server` not Chromium-with-CDP. The Playwright
#     server spawns its OWN browser instances per ws.connect() call.
#   - Different lifecycle and scaling profile.
#
# Why no nginx cdp-proxy sidecar (unlike headed/headless):
#   - run-server doesn't expose CDP — it exposes Playwright protocol over WS.
#   - The Chrome M113+ Host-header DNS-rebinding check applies only to CDP's
#     /json/* endpoints, not to the Playwright protocol envelope.
#
# Why no Tailscale Ingress:
#   - Internal-only API (no UI). Per memory feedback_no_external_ingress_for_uiless_backends.
# =============================================================================

apiVersion: apps/v1
kind: Deployment
metadata:
  name: playwright-server
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: playwright
    app.kubernetes.io/component: server
    app.kubernetes.io/managed-by: terraform
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: playwright
      app.kubernetes.io/component: server
  template:
    metadata:
      labels:
        app.kubernetes.io/name: playwright
        app.kubernetes.io/component: server
        app.kubernetes.io/managed-by: terraform
    spec:
      # ---------------------------------------------------------------------
      # Run as root (image default). The noble-based image has uid 1000 =
      # `ubuntu`, NOT `pwuser` — and /home/pwuser is mode 0750 owned by
      # pwuser:pwuser, so any other uid hits EACCES on chdir. Open WebUI's
      # reference docker-compose runs this image with NO user override and
      # NO workdir override, so we match that pattern exactly.
      #
      # Hardening that's safe with uid 0: seccomp + drop-all-caps +
      # no-privilege-escalation. Combined, root in this pod can't do
      # anything privileged at the kernel level.
      # ---------------------------------------------------------------------
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      volumes:
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: ${shm_size}
      containers:
        # -----------------------------------------------------------------
        # mcr.microsoft.com/playwright — official Microsoft image
        # -----------------------------------------------------------------
        # Image bundles Node.js + npx + all Chromium system deps. The
        # `npx -y playwright@$${version} run-server` command downloads the
        # Playwright Node package on first start (~5s, cached in
        # ~/.npm thereafter) and exec's the WS server.
        #
        # The version after `playwright@` MUST match the image's bundled
        # Playwright version (the image tag's vX.Y.Z). Open WebUI's Python
        # client (playwright==X.Y.Z) must also match for the protocol
        # handshake to succeed.
        # -----------------------------------------------------------------
        - name: playwright-server
          image: ${image}
          imagePullPolicy: IfNotPresent
          # No workingDir override — image default is "/", which is where
          # npm writes its cache (/root/.npm) when running as root.
          # Match Open WebUI's reference compose exactly.
          command: ["/bin/sh", "-c"]
          # `run-server` only accepts --port/--host/--path/--max-clients/--mode
          # — there is NO --browser flag (that's a `playwright test` thing).
          # Browser is selected client-side per ws.connect() call (Open WebUI
          # always passes chromium). Re-confirmed against playwright 1.58 source
          # 2026-06-02; adding --browser fails with "unknown option" CrashLoop.
          args:
            - "npx -y playwright@${playwright_version} run-server --port 3000 --host 0.0.0.0"
          # Drop-all-caps + no-privilege-escalation hardens the root user
          # without breaking the npm/Chromium runtime (neither needs any
          # caps — Chrome's sandbox uses user namespaces, not CAP_SYS_ADMIN).
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          ports:
            - name: ws
              containerPort: 3000
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
          # WS endpoint returns HTTP 101 (switching protocols) for clients
          # speaking WebSocket; bare HTTP GET returns 400. TCP probe is the
          # cleanest health signal here.
          livenessProbe:
            tcpSocket:
              port: 3000
            initialDelaySeconds: 20
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            tcpSocket:
              port: 3000
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          # npx + Playwright cold start takes ~10s on first boot (downloads
          # the Node package). startupProbe gives 60s before failing over
          # to liveness.
          startupProbe:
            tcpSocket:
              port: 3000
            periodSeconds: 5
            failureThreshold: 12
