# =============================================================================
# Alloy Helm values — alloy-logs (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: grafana/alloy v1.8.0 (same chart as helm_release.alloy, separate
# release). Repo: https://grafana.github.io/helm-charts
#
# Ported from the COELHO Cloud fix, 2026-09-23. Why this release exists:
# helm_release.alloy's PREVIOUS log pipeline used `loki.source.kubernetes` —
# tails pod logs via the Kubernetes API's pods/log subresource. That
# component never garbage-collects tailers for deleted pods (no upstream fix
# through Alloy v1.20.0-rc.0; see grafana/alloy#7192, #3829). Under frequent
# redeploys, zombie tailers accumulate and retry the K8s API forever,
# CPU-starving the whole Deployment — including its OTLP receiver — until it
# fails its own readiness probe and the Service has zero ready endpoints.
#
# Fix: file-based tailing instead. `alloy.mounts.varlog: true` mounts the
# HOST's /var/log read-only (chart-native preset, no privileged container).
# `loki.source.file` uses fsnotify — when kubelet removes a pod's log file,
# the watch is dropped cleanly. No API polling, no retry loop, no leak.
#
# River config follows Grafana's own "migrate from Agent Operator" recipe
# verbatim (docs/sources/set-up/migrate/from-operator.md upstream) —
# confirmed via Alloy's source (internal/component/loki/source/file/file.go)
# that `loki.source.file` calls os.Stat(target.Path) LITERALLY, with no glob
# expansion of its own. `discovery.relabel`'s __path__ is still a glob
# (`/var/log/pods/*<uid>/<container>/*.log`); `local.file_match` is the
# required stage that actually expands that glob into concrete, literal
# file targets before `loki.source.file` ever sees them. Omitting it makes
# every single target fail with "failed to create source, skipping" /
# "stat failed: ... no such file or directory" — 100% of pods, since a
# literal stat() of a string containing `*` can never succeed. (This was
# caught live in COELHO Cloud before being ported here — see that repo's
# docs/alloy_optimization.md for the full incident writeup.)
#
# DaemonSet, one pod per node — each pod only watches ITS OWN node's pods via
# a server-side field selector on spec.nodeName (chart sets the HOSTNAME env
# var from spec.nodeName by default), so there's no cross-node duplication.
#
# Namespace scope is a DENYLIST, not an allowlist: discovery.kubernetes runs
# cluster-wide; a small, effectively-fixed regex of K8s/Rancher internals
# (alloy_log_namespace_denylist) is dropped at the discovery.relabel stage
# below, before any file handle opens. An allowlist needs a terragrunt apply
# every time a new project namespace appears. Safe cluster-wide because cost
# here scales with log line volume (file reads), not namespace count.
# =============================================================================

crds:
  create: false

image:
  registry: docker.io
  repository: grafana/alloy
  tag: "${alloy_image_tag}"
  pullPolicy: IfNotPresent

alloy:
  stabilityLevel: generally-available

  clustering:
    enabled: false

  resources:
    requests:
      cpu: "${cpu_request}"
      memory: "${memory_request}"
    limits:
      memory: "${memory_limit}"

  extraEnv:
    - name: GOMEMLIMIT
      value: "${alloy_gomemlimit}"
    - name: GOGC
      value: "${alloy_gogc}"

  # Chart-native preset — mounts the host's /var/log read-only at /var/log in
  # the container. Covers /var/log/pods (containerd symlinks each container's
  # log there) without a privileged container or extra hostPath wiring.
  mounts:
    varlog: true

  configMap:
    create: true
    content: |
      // ====================================================================
      // Alloy River config — pod log tailing (alloy-logs instance)
      // ====================================================================
      // Pipeline: discovery.kubernetes (this node only) → discovery.relabel
      //           (builds __path__ glob from pod UID + container name) →
      //           local.file_match (expands the glob into literal files) →
      //           loki.source.file → loki.process (CRI/docker parsing) →
      //           loki.write → Loki
      // ====================================================================

      logging {
        level  = "info"
        format = "logfmt"
      }

      loki.write "default" {
        endpoint {
          url = "${loki_push_url}"
        }
        external_labels = {
          cluster = "${cluster_label}",
        }
      }

      // Server-side field selector — the API server only returns pods
      // scheduled on THIS node, so each DaemonSet pod's watch stays cheap
      // regardless of cluster size. No namespace filter here — see the
      // drop rule below instead (denylist, not allowlist).
      discovery.kubernetes "pod" {
        role = "pod"
        selectors {
          role  = "pod"
          field = "spec.nodeName=" + coalesce(sys.env("HOSTNAME"), constants.hostname)
        }
      }

      discovery.relabel "pod_logs" {
        targets = discovery.kubernetes.pod.targets

        // Denylist: drop K8s/Rancher internals before any file handle
        // opens. Everything else — including future project namespaces —
        // is collected automatically.
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          regex         = "${alloy_log_namespace_denylist}"
          action        = "drop"
        }

        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          target_label  = "namespace"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_name"]
          target_label  = "pod"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_container_name"]
          target_label  = "container"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name"]
          target_label  = "app"
        }

        // Kubelet lays out container logs as
        // /var/log/pods/<namespace>_<pod>_<uid>/<container>/*.log — the `*`
        // glob absorbs the <namespace>_<pod>_ prefix, leaving the pod UID
        // (globally unique) to pin the exact directory. This __path__ is
        // still a GLOB at this point — local.file_match below expands it.
        rule {
          source_labels = ["__meta_kubernetes_pod_uid", "__meta_kubernetes_pod_container_name"]
          separator     = "/"
          target_label  = "__path__"
          replacement   = "/var/log/pods/*$1/*.log"
        }

        // Container runtime (containerd vs docker) determines which log-line
        // format loki.process below needs to parse (stage.cri vs stage.docker).
        rule {
          source_labels = ["__meta_kubernetes_pod_container_id"]
          regex         = "^(\\w+):\\/\\/.+$"
          target_label  = "tmp_container_runtime"
          replacement   = "$1"
        }
      }

      // Expands each target's glob __path__ into concrete, literal file
      // paths on disk — loki.source.file itself does NOT glob-expand (it
      // os.Stat()s target.Path literally), so this stage is required, not
      // optional. Re-scans on every discovery.relabel output change.
      local.file_match "pod_logs" {
        path_targets = discovery.relabel.pod_logs.output
      }

      loki.source.file "pod_logs" {
        targets        = local.file_match.pod_logs.targets
        forward_to     = [loki.process.pod_logs.receiver]
        // A brand-new tailer has no saved read position, so without this it
        // replays each file's ENTIRE backlog from byte 0 on first tail (and
        // again on every pod restart — the DaemonSet's positions file lives
        // on ephemeral local storage, not a PVC). Loki rejects anything
        // older than its accept window (currently ~7d) with HTTP 400
        // "timestamp too old", so old backlog lines are dropped anyway —
        // skip reading them at all. Same "gaps over retry storms" homelab
        // philosophy as prometheus.remote_write's sample_age_limit elsewhere.
        tail_from_end  = true
      }

      loki.process "pod_logs" {
        forward_to = [loki.write.default.receiver]

        // (No namespace drop here — the denylist above already excludes
        // K8s/Rancher internals before a file handle ever opens for them,
        // so a matching target never reaches this stage. A second drop
        // here would be dead code.)

        // containerd (k3d's runtime) log lines are CRI-formatted:
        // "<timestamp> <stream> <flags> <message>". Parse into k/v pairs
        // and promote stream/flags to labels, matching Grafana's own
        // "migrate from Agent Operator" recipe.
        stage.match {
          selector = "{tmp_container_runtime=\"containerd\"}"
          stage.cri {}
          stage.labels {
            values = {
              flags  = "",
              stream = "",
            }
          }
        }

        // Docker runtime isn't used on this cluster today, but kept for
        // parity with the upstream recipe — harmless no-op if it never
        // matches.
        stage.match {
          selector = "{tmp_container_runtime=\"docker\"}"
          stage.docker {}
          stage.labels {
            values = {
              stream = "",
            }
          }
        }

        stage.label_drop {
          values = ["tmp_container_runtime"]
        }

        // Drop DEBUG-level lines from the LGTM stack itself (extremely chatty).
        stage.match {
          selector = "{namespace=~\"mimir|loki|tempo\"} |~ \"level=debug\""
          action   = "drop"
        }

        stage.static_labels {
          values = {
            cluster = "${cluster_label}",
          }
        }
      }

# -----------------------------------------------------------------------------
# DaemonSet — one pod per node, each tailing only its own node's pod logs.
# -----------------------------------------------------------------------------
controller:
  type: daemonset
  tolerations:
    - operator: Exists

# -----------------------------------------------------------------------------
# No Service needed — this instance only ships logs outward to Loki, nothing
# queries it. Its own CPU/memory are still visible via cAdvisor (scraped by
# helm_release.alloy's kubelet_cadvisor pipeline, cluster-wide).
# -----------------------------------------------------------------------------
service:
  enabled: false

serviceMonitor:
  enabled: false

rbac:
  create: true
