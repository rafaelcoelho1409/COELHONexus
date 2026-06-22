# =============================================================================
# eck-stack Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: elastic/eck-stack v0.18.2 — wraps eck-elasticsearch + eck-kibana.
#
# All variables interpolated as SCALARS.
# =============================================================================

# -----------------------------------------------------------------------------
# Elasticsearch
# -----------------------------------------------------------------------------
eck-elasticsearch:
  enabled: true
  fullnameOverride: elasticsearch
  version: ${es_version}

  # http: {}  # using ECK's auto-generated TLS — no overrides needed

%{ if elastic_file_realm_secret_name != "" || app_file_realm_secret_name != "" || app_roles_secret_name != "" ~}
  auth:
%{ if elastic_file_realm_secret_name != "" ~}
    # disableElasticUser: ECK stops managing the built-in elastic user so our
    # file-realm entry below is the sole authority for elastic credentials.
    # Without this, ECK's internal reconcile races against our file realm entry.
    disableElasticUser: true
%{ endif ~}
%{ if elastic_file_realm_secret_name != "" || app_file_realm_secret_name != "" ~}
    fileRealm:
%{ if elastic_file_realm_secret_name != "" ~}
      - secretName: ${elastic_file_realm_secret_name}
%{ endif ~}
%{ if app_file_realm_secret_name != "" ~}
      - secretName: ${app_file_realm_secret_name}
%{ endif ~}
%{ endif ~}
%{ if app_roles_secret_name != "" ~}
    roles:
      - secretName: ${app_roles_secret_name}
%{ endif ~}

%{ endif ~}

  # secureSettings: ECK loads each KEY from this Secret into ES's keystore
  # on pod start. Required for the s3 snapshot repo plugin (ES 8.18 rejects
  # inline credentials in repo settings).
  secureSettings:
    - secretName: elasticsearch-s3-keystore

  nodeSets:
    - name: default
      count: 1
      config:
        node.store.allow_mmap: false
        # NOTE: do NOT set `discovery.type: single-node` here. ECK auto-injects
        # `cluster.initial_master_nodes` based on nodeSet count, and the two
        # settings conflict ("setting [cluster.initial_master_nodes] is not
        # allowed when [discovery.type] is set to [single-node]"). ECK
        # auto-detects single-node mode from `count: 1`.

        # Drop ml + transform + remote_cluster_client roles. Elastic-recommended
        # over disabling-only-via-xpack — frees the native ML controller
        # process (~80-150 MiB RSS off-heap) and the transform allocator.
        node.roles: ["master", "data", "ingest"]

        # Disable unused xpack subsystems (frees heap + native processes):
        xpack.ml.enabled: false                    # no ML jobs in use
        xpack.watcher.enabled: false               # Grafana handles alerting; saves .watcher-history-* churn
        xpack.monitoring.collection.enabled: false # no Stack Monitoring consumer

        # Single-node tunings:
        cluster.routing.allocation.disk.watermark.enable_for_single_data_node: true
        indices.lifecycle.poll_interval: 30m       # was 10m default → 3× fewer ILM executor wake-ups
      podTemplate:
        spec:
          containers:
            - name: elasticsearch
              env:
                # Override ECK's auto-derived JVM heap (50%-of-limit default).
                # 640m on 1.5Gi pod ≈ 42%; the remaining ~900 MiB goes to Lucene
                # FS cache + JVM overhead + off-heap allocations.
                # G1GC stays default — do NOT switch to ZGC at this heap size
                # (concurrent collector overhead outweighs gain below ~2GB heap).
                - name: ES_JAVA_OPTS
                  value: "-Xms${es_java_heap} -Xmx${es_java_heap}"
              resources:
                requests:
                  cpu: ${es_cpu_request}
                  memory: ${es_memory_request}
                limits:
                  # Burstable QoS — request (above) < limit. Lets the JVM burst
                  # for off-heap spikes without inflating steady-state.
                  memory: ${es_memory_limit}
      volumeClaimTemplates:
        - metadata:
            name: elasticsearch-data
          spec:
            accessModes:
              - ReadWriteOnce
            resources:
              requests:
                storage: ${storage_size}
            storageClassName: ${storage_class}

# -----------------------------------------------------------------------------
# Kibana
# -----------------------------------------------------------------------------
eck-kibana:
  enabled: true
  fullnameOverride: kibana
  version: ${kibana_version}
  count: 1

  # ElasticsearchRef wires Kibana → ES automatically (operator manages auth)
  elasticsearchRef:
    name: elasticsearch

  # kibana.yml settings — disable plugins not in use at homelab scale.
  # NOTE: do NOT set `xpack.ml.enabled` in kibana.yml — Kibana 8.x rejects it
  # (FATAL "definition for this key is missing"). Kibana auto-detects ML
  # availability from ES; since `xpack.ml.enabled: false` is set on ES side,
  # the ML UI is auto-hidden.
  config:
    xpack.reporting.enabled: false    # PDF/PNG export uses Chromium subprocess (~150 MiB)
    xpack.apm.ui.enabled: false       # no APM stack in use
    xpack.fleet.enabled: false        # Fleet manager loads a lot at boot
    telemetry.enabled: false
    newsfeed.enabled: false           # trim plugin auto-init

    # ONE-TIME MIGRATION FIX (2026-05-24): disabling Fleet stops Kibana from
    # loading the Cloud Security Posture plugin → leftover `cloud-security-posture-settings`
    # saved objects in `.kibana_security_solution` (from a prior install with
    # Fleet enabled) become "unknown types" → migration FATAL → boot loop.
    # Per https://www.elastic.co/guide/en/kibana/8.18/resolve-migrations-failures.html
    # this discards those orphaned objects ONCE for the 8.18.8 migration target.
    # Pin to current ${kibana_version} so the next version bump auto-no-ops this
    # setting (Kibana ignores it when target version doesn't match).
    migrations.discardUnknownObjects: "${kibana_version}"

  podTemplate:
    spec:
      containers:
        - name: kibana
          env:
            # Cap Node.js heap explicitly. Elastic's official guidance (May 2026)
            # is to NOT override under orchestration — but their auto-derivation
            # rounds up generously. For homelab scale this is the only way to
            # land at <600 MiB. If Kibana OOMs on plugin init: bump to 600.
            - name: NODE_OPTIONS
              value: "--max-old-space-size=${kibana_node_max_old_space_mb}"
          resources:
            requests:
              cpu: ${kibana_cpu_request}
              memory: ${kibana_memory_request}
            limits:
              memory: ${kibana_memory_limit}

# -----------------------------------------------------------------------------
# Other Stack components — disabled
# -----------------------------------------------------------------------------
eck-agent:
  enabled: false
eck-fleet-server:
  enabled: false
eck-beats:
  enabled: false
eck-logstash:
  enabled: false
eck-apm-server:
  enabled: false
eck-enterprise-search:
  enabled: false
