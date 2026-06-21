# =============================================================================
# langfuse/langfuse Helm values — chart ${chart_version} (appVersion 3.172.x)
# =============================================================================
# Chart schema verified against upstream tag at pinning time:
#   https://github.com/langfuse/langfuse-k8s/blob/v${chart_version}/charts/langfuse/values.yaml
#
# Schema deltas vs v1 (1.5.27):
#   - probes are `path: ...` not `httpGet:` (Helm map-replace rule applies →
#     full block written below)
#   - clickhouse defaults: replicaCount=3, resourcesPreset=2xlarge → both
#     overridden for homelab
#   - chart now ships separate s3.{eventUpload,mediaUpload,batchExport} blocks
#     allowing per-upload-type bucket+prefix; we point all three at the shared
#     `backups` bucket with `langfuse/<type>/` prefixes
#
# Secret-handling: NEVER inline plain secrets here. The chart supports
# `secretKeyRef` everywhere it accepts a value, AND `existingSecret` for the
# subchart auth. We pre-create a `langfuse-app` Secret in main.tf and let the
# chart pull from it. Postgres + Redis subcharts are disabled — they read from
# their own `existingSecret` blocks pointing at module-managed Secrets too.
# =============================================================================

# -----------------------------------------------------------------------------
# Langfuse application
# -----------------------------------------------------------------------------
langfuse:
  logging:
    # Tier 1 (2026-05-25): info → warn drops ~16 idle info lines/min.
    # Errors still surface.
    level: ${log_level}
    format: text

  # API key hash salt — value pulled from `langfuse-app` Secret.
  salt:
    secretKeyRef:
      name: langfuse-app
      key: salt

  # 256-bit hex key encrypting LLM API keys at rest.
  encryptionKey:
    secretKeyRef:
      name: langfuse-app
      key: encryptionKey

  features:
    telemetryEnabled: false      # don't phone home from a homelab tracker
    signUpDisabled: true         # headless init seeds the only user
    experimentalFeaturesEnabled: false

  nodeEnv: production

  nextauth:
    url: "${public_url}"
    secret:
      secretKeyRef:
        name: langfuse-app
        key: nextauthSecret

  # Shared env across web + worker + migration containers.
  # All values come from `langfuse-app` Secret — including the IDs that aren't
  # technically secret (kept colocated to simplify rotation + reduce moving parts).
  additionalEnv:
    - name: TZ
      value: UTC

    # Tier 1 RAM optimization (2026-05-25) — applied to web + worker + migration.
    # V8 max-old-space-size pin: caps heap at ~80% of container limit. Without
    # this, Node 24 defaults to ~75% of cgroup ceiling and can spike past it
    # under GC pressure.
    - name: NODE_OPTIONS
      value: "--max-old-space-size=${node_max_old_space_size_mb}"

    # BullMQ blocking-pop idle timeout. Chart default 30s fires every 30s on
    # every idle queue → "Socket timeout. Expecting data, but didn't receive
    # any in 30000ms." ERROR spam (8 queues × 2/min = 16 ERROR lines/min).
    # 5min cuts noise ~10x without hiding genuinely dead Redis sockets.
    - name: REDIS_BLOCKING_SOCKET_TIMEOUT_MS
      value: "${redis_blocking_socket_timeout_ms}"
    - name: LANGFUSE_INIT_ORG_ID
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initOrgId
    - name: LANGFUSE_INIT_PROJECT_ID
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initProjectId
    - name: LANGFUSE_INIT_PROJECT_PUBLIC_KEY
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initProjectPublicKey
    - name: LANGFUSE_INIT_PROJECT_SECRET_KEY
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initProjectSecretKey
    - name: LANGFUSE_INIT_USER_EMAIL
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initUserEmail
    - name: LANGFUSE_INIT_USER_PASSWORD
      valueFrom:
        secretKeyRef:
          name: langfuse-app
          key: initUserPassword

  # Web (UI + API) — full probe blocks below (Helm replaces maps; partial
  # override would drop the chart's `path` default).
  web:
    replicas: 1
    resources:
      requests:
        cpu: "${web_cpu_request}"
        memory: "${web_memory_request}"
      limits:
        memory: "${web_memory_limit}"

    # Web-specific env vars — note nested under `pod` per chart schema (1.5.31
    # values.yaml line 205). `langfuse.web.additionalEnv` does NOT exist; using
    # the wrong path silently does nothing.
    pod:
      additionalEnv:
        # API key + prompt cache TTLs. Single-user → API key rarely rotates;
        # 1h TTL vs default 5min cuts Postgres hits on `api_keys` lookup.
        - name: LANGFUSE_CACHE_API_KEY_ENABLED
          value: "true"
        - name: LANGFUSE_CACHE_API_KEY_TTL_SECONDS
          value: "3600"
        - name: LANGFUSE_CACHE_PROMPT_ENABLED
          value: "true"
        - name: LANGFUSE_CACHE_PROMPT_TTL_SECONDS
          value: "3600"

    service:
      type: ClusterIP
      port: 3000
    livenessProbe:
      path: "/api/public/health"
      initialDelaySeconds: 120
      periodSeconds: 15
      timeoutSeconds: 10
      failureThreshold: 6
      successThreshold: 1
    readinessProbe:
      path: "/api/public/ready"
      initialDelaySeconds: 60
      periodSeconds: 10
      timeoutSeconds: 10
      failureThreshold: 6
      successThreshold: 1
    pdb:
      create: false   # single replica, no point in PDB
    keda:
      enabled: false
    hpa:
      enabled: false

  # Worker (async ingestion → ClickHouse)
  worker:
    replicas: 1
    resources:
      requests:
        cpu: "${worker_cpu_request}"
        memory: "${worker_memory_request}"
      limits:
        memory: "${worker_memory_limit}"

    # Worker-specific env vars — see web.pod note above re: chart path nesting.
    pod:
      additionalEnv:
        # ClickHouse batch-write tuning. Chart default writes every 1s; raising
        # to 5s cuts CH write QPS 5x for low-throughput single-user traffic.
        - name: LANGFUSE_INGESTION_CLICKHOUSE_WRITE_INTERVAL_MS
          value: "${clickhouse_write_interval_ms}"

        # Worker concurrency caps — chart defaults are 5; single-user → 1 each.
        - name: LANGFUSE_EVAL_EXECUTION_WORKER_CONCURRENCY
          value: "1"
        - name: LANGFUSE_LLM_AS_JUDGE_EXECUTION_WORKER_CONCURRENCY
          value: "1"

        # Per-queue worker concurrency. Pinned to LangFuse's documented SOTA
        # target ("20 per worker per queue", no sharding) for ingestion/otel-
        # ingestion; trace-upsert at 10 (single-table writes can be lighter
        # without slowing drain). All three sum to ~50 concurrent CH writes
        # peak — comfortably under max_concurrent_queries=${clickhouse_max_concurrent_queries}
        # with headroom for UI reads. See variables.tf rationale (2026-06-11).
        - name: LANGFUSE_INGESTION_QUEUE_PROCESSING_CONCURRENCY
          value: "${ingestion_queue_concurrency}"
        - name: LANGFUSE_TRACE_UPSERT_WORKER_CONCURRENCY
          value: "${trace_upsert_worker_concurrency}"
        - name: LANGFUSE_OTEL_INGESTION_QUEUE_PROCESSING_CONCURRENCY
          value: "${otel_ingestion_queue_concurrency}"

        # Feature queue toggles. Each disabled queue removes one BullMQ consumer
        # (one ioredis connection + polling timer ≈ 5-10 MiB) and one
        # "Socket timeout 30000ms" ERROR line per 30s.
        - name: QUEUE_CONSUMER_OTEL_INGESTION_QUEUE_IS_ENABLED
          value: "${enable_otel_ingestion}"
        - name: QUEUE_CONSUMER_OTEL_INGESTION_SECONDARY_QUEUE_IS_ENABLED
          value: "${enable_otel_ingestion}"
        - name: QUEUE_CONSUMER_POSTHOG_INTEGRATION_QUEUE_IS_ENABLED
          value: "${enable_posthog_integration}"
        - name: QUEUE_CONSUMER_MIXPANEL_INTEGRATION_QUEUE_IS_ENABLED
          value: "${enable_mixpanel_integration}"
        - name: QUEUE_CONSUMER_NOTIFICATION_QUEUE_IS_ENABLED
          value: "${enable_notification_queue}"

    keda:
      enabled: false
    hpa:
      enabled: false

  # Disable bundled chart Ingress — Tailscale Ingress is shipped separately.
  ingress:
    enabled: false

# -----------------------------------------------------------------------------
# PostgreSQL — bundled Bitnami subchart DISABLED → v2 baseline
# -----------------------------------------------------------------------------
postgresql:
  deploy: false
  host: "${postgres_host}"
  port: ${postgres_port}
  auth:
    username: "${postgres_user}"
    database: "${postgres_database}"
    existingSecret: langfuse-postgres
    secretKeys:
      userPasswordKey: password
      adminPasswordKey: password
  # Direct-URL is what Prisma migrations use (separate from the pooled URL).
  directUrl: "postgresql://${postgres_user}:${postgres_password}@${postgres_host}:${postgres_port}/${postgres_database}"

# -----------------------------------------------------------------------------
# Redis / Valkey — bundled DISABLED → v2 baseline (DB index ${redis_db})
# -----------------------------------------------------------------------------
# Decision recorded: shared Redis is fine for homelab single-user trace volume.
# Switch to bundled (set `redis.deploy: true` and remove the host/auth) if
# Redis CPU >70% sustained or other apps slow down.
redis:
  deploy: false
  host: "${redis_host}"
  port: ${redis_port}
  auth:
    username: "default"
    existingSecret: langfuse-redis-password
    existingSecretPasswordKey: redis-password
    database: ${redis_db}

# -----------------------------------------------------------------------------
# ClickHouse — bundled (no v2 baseline ClickHouse exists)
# -----------------------------------------------------------------------------
# Single-replica homelab mode: clusterEnabled=false, replicaCount=1, no keeper,
# no zookeeper. Default `replicaCount: 3` + `resourcesPreset: 2xlarge` would
# blow the homelab memory budget — both overridden.
# UTC timezone is required by Langfuse docs (non-UTC → empty/incorrect queries).
clickhouse:
  deploy: true
  shards: 1
  replicaCount: 1
  clusterEnabled: false
  keeper:
    enabled: false
  zookeeper:
    enabled: false
  auth:
    username: default
    existingSecret: langfuse-clickhouse
    existingSecretKey: password
  persistence:
    enabled: true
    size: ${clickhouse_storage_size}
  resources:
    requests:
      cpu: 200m
      memory: "${clickhouse_memory_request}"
    limits:
      memory: "${clickhouse_memory_limit}"
  # Disable ClickHouse's internal telemetry tables (NOT Langfuse's trace data
  # — those are in `default.traces`, separate). Default ClickHouse writes to
  # ~12 internal *_log tables every second, generating constant merge load
  # that pinned this homelab pod at 1.7+ cores at idle (verified via
  # system.parts + system.merges 2026-05-08).
  #
  # `extraOverrides` is the Bitnami clickhouse-8.0.5 chart-native single-file
  # override — renders to /etc/clickhouse-server/conf.d/01_extra_overrides.xml
  # (via ConfigMap created by templates/configmap-extra.yaml + StatefulSet
  # mount). ClickHouse auto-loads every *.xml in conf.d/ at startup.
  #
  # `remove="remove"` is ClickHouse's config-merge directive to delete a
  # section from the merged final config, fully disabling each system_*_log.
  # Reference: https://clickhouse.com/docs/operations/configuration-files
  #
  # Why not `sampling.enabled: false` or `configdFiles` — both are 9.x
  # parameters; this chart is 8.0.5 (langfuse pins it).
  extraOverrides: |
    <clickhouse>
      <!-- ===================================================================
           USER-VALIDATED LOG-DISABLE BLOCK (2026-05-08) — DO NOT MODIFY.
           Disabling these 18 system_*_log tables eliminated a 1.7-core idle
           CPU spike from constant background merges.
           ================================================================ -->
      <query_log remove="remove"/>
      <query_thread_log remove="remove"/>
      <query_views_log remove="remove"/>
      <part_log remove="remove"/>
      <metric_log remove="remove"/>
      <asynchronous_metric_log remove="remove"/>
      <trace_log remove="remove"/>
      <text_log remove="remove"/>
      <crash_log remove="remove"/>
      <opentelemetry_span_log remove="remove"/>
      <session_log remove="remove"/>
      <zookeeper_log remove="remove"/>
      <transactions_info_log remove="remove"/>
      <processors_profile_log remove="remove"/>
      <asynchronous_insert_log remove="remove"/>
      <backup_log remove="remove"/>
      <error_log remove="remove"/>
      <blob_storage_log remove="remove"/>
      <latency_log remove="remove"/>

      <!-- ===================================================================
           TIER 1 RAM TUNING (2026-05-25) — ADDITIVE inside same <clickhouse>
           root. Sized for our 1.09 GiB DB (`observations` 1 GiB, `traces` 27 MiB)
           — current MarkCacheBytes=1.63 KiB used vs 5 GiB declared. Defaults
           are sized for a 2xlarge cloud node, not a homelab single-shard CE.
           ================================================================ -->

      <!-- Caches: declared sizes were 5 GiB + 8 GiB + 5 GiB virtual. Tiny used. -->
      <mark_cache_size>134217728</mark_cache_size>             <!-- 128 MiB -->
      <index_mark_cache_size>33554432</index_mark_cache_size>  <!-- 32 MiB -->
      <uncompressed_cache_size>0</uncompressed_cache_size>     <!-- 0 = disabled; Langfuse use_uncompressed_cache=0 by default -->

      <!-- Hard cap. 1.2 GiB inside 1.5 GiB container limit leaves 300 MiB for
           OS page cache (helpful for query latency on 1 GiB observations). -->
      <max_server_memory_usage>1288490188</max_server_memory_usage>

      <!-- Background pools: scaled down for single-shard, no-Keeper, no-Kafka.
           Defaults: pool_size=16, schedule_pool_size=512, buffer_flush=16,
           message_broker=16. -->
      <background_pool_size>2</background_pool_size>
      <background_schedule_pool_size>8</background_schedule_pool_size>
      <background_buffer_flush_schedule_pool_size>1</background_buffer_flush_schedule_pool_size>
      <background_message_broker_schedule_pool_size>1</background_message_broker_schedule_pool_size>
      <background_merges_mutations_concurrency_ratio>1</background_merges_mutations_concurrency_ratio>
      <background_move_pool_size>1</background_move_pool_size>
      <background_common_pool_size>2</background_common_pool_size>
      <background_distributed_schedule_pool_size>1</background_distributed_schedule_pool_size>
      <background_fetches_pool_size>1</background_fetches_pool_size>

      <!-- Thread + concurrency ceilings. Defaults 10000 / 100 / 16. The 20-cap
           that shipped with this chart was too low for the langfuse-worker's
           OTel/ingestion backlog drain — bursts of 30+ concurrent writes
           breached it and surfaced as "Too many simultaneous queries" errors
           plus tRPC `traces.byId` 500s in the UI (rows partial). Raised to
           the CH upstream default — see variables.tf clickhouse_max_concurrent_queries. -->
      <max_thread_pool_size>500</max_thread_pool_size>
      <max_concurrent_queries>${clickhouse_max_concurrent_queries}</max_concurrent_queries>
      <async_insert_threads>2</async_insert_threads>

      <!-- MergeTree: cap merge RAM (default 150 GiB) and bump parts-thresholds
           to give the smaller background_pool_size headroom before throttling.

           number_of_free_entries_in_pool_to_* MUST be <= background_pool_size *
           background_merges_mutations_concurrency_ratio (=2 here), or ClickHouse
           sanityCheck refuses to start (BAD_ARGUMENTS code 36). Defaults are 20. -->
      <merge_tree>
        <max_bytes_to_merge_at_max_space_in_pool>1073741824</max_bytes_to_merge_at_max_space_in_pool>
        <merge_max_block_size>1024</merge_max_block_size>
        <parts_to_delay_insert>300</parts_to_delay_insert>
        <parts_to_throw_insert>600</parts_to_throw_insert>
        <number_of_free_entries_in_pool_to_execute_mutation>1</number_of_free_entries_in_pool_to_execute_mutation>
        <number_of_free_entries_in_pool_to_lower_max_size_of_merge>0</number_of_free_entries_in_pool_to_lower_max_size_of_merge>
        <number_of_free_entries_in_pool_to_execute_optimize_entire_partition>1</number_of_free_entries_in_pool_to_execute_optimize_entire_partition>
      </merge_tree>
    </clickhouse>
  extraEnvVars:
    - name: TZ
      value: UTC

# -----------------------------------------------------------------------------
# S3 / MinIO — bundled DISABLED → v2 baseline (shared `${artifacts_bucket}`)
# -----------------------------------------------------------------------------
# All three upload types share one bucket, separated by prefix. Pre-flight
# bootstrap Job ensures the bucket exists before Helm starts.
s3:
  deploy: false
  storageProvider: s3
  bucket: "${artifacts_bucket}"
  region: us-east-1
  endpoint: "${minio_endpoint}"
  forcePathStyle: true
  accessKeyId:
    secretKeyRef:
      name: langfuse-minio
      key: MINIO_ACCESS_KEY
  secretAccessKey:
    secretKeyRef:
      name: langfuse-minio
      key: MINIO_SECRET_KEY

  # S3 GET/PUT concurrency — chart-native override (1.5.31 _helpers.tpl:708-716
  # gates LANGFUSE_S3_CONCURRENT_{READS,WRITES} on hasKey s3.concurrency.{reads,writes}).
  # Setting them via worker.pod.additionalEnv fails with strategic-merge-patch
  # duplicate-name error since the chart already injects them.
  concurrency:
    reads: ${s3_concurrent_reads}
    writes: ${s3_concurrent_writes}
  eventUpload:
    bucket: "${artifacts_bucket}"
    prefix: "${artifacts_prefix}/events/"
  mediaUpload:
    bucket: "${artifacts_bucket}"
    prefix: "${artifacts_prefix}/media/"
  batchExport:
    bucket: "${artifacts_bucket}"
    prefix: "${artifacts_prefix}/exports/"

# -----------------------------------------------------------------------------
# Pod security — non-root, drop ALL caps (matches v2 cluster convention)
# -----------------------------------------------------------------------------
global:
  defaultPodSecurityContext:
    fsGroup: 1000
  defaultContainerSecurityContext:
    runAsUser: 1000
    runAsNonRoot: true
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
