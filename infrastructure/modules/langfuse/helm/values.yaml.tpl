# langfuse/langfuse Helm values — chart ${chart_version}. No plain secrets inline.
langfuse:
  logging:
    level: ${log_level}
    format: text

  salt:
    secretKeyRef:
      name: langfuse-app
      key: salt

  encryptionKey:
    secretKeyRef:
      name: langfuse-app
      key: encryptionKey

  features:
    telemetryEnabled: false
    signUpDisabled: true
    experimentalFeaturesEnabled: false

  nodeEnv: production

  nextauth:
    url: "${public_url}"
    secret:
      secretKeyRef:
        name: langfuse-app
        key: nextauthSecret

  additionalEnv:
    - name: TZ
      value: UTC

    # Caps V8 heap at ~80% of container limit; Node 24 defaults can spike past the cgroup ceiling.
    - name: NODE_OPTIONS
      value: "--max-old-space-size=${node_max_old_space_size_mb}"

    # 5min vs default 30s; cuts "Socket timeout 30000ms" ERROR spam 10x without hiding dead Redis.
    - name: REDIS_BLOCKING_SOCKET_TIMEOUT_MS
      value: "${redis_blocking_socket_timeout_ms}"

    - name: REDIS_CONNECTION_TIMEOUT_MS
      value: "20000"
    - name: REDIS_COMMAND_TIMEOUT_MS
      value: "15000"
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

  web:
    replicas: 1
    resources:
      requests:
        cpu: "${web_cpu_request}"
        memory: "${web_memory_request}"
      limits:
        memory: "${web_memory_limit}"

    # `langfuse.web.additionalEnv` doesn't exist in chart schema; env must nest under `pod`.
    pod:
      additionalEnv:
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

  worker:
    replicas: 1
    resources:
      requests:
        cpu: "${worker_cpu_request}"
        memory: "${worker_memory_request}"
      limits:
        memory: "${worker_memory_limit}"

    pod:
      additionalEnv:
        # Raises default 1s CH write interval; cuts write QPS at single-user scale.
        - name: LANGFUSE_INGESTION_CLICKHOUSE_WRITE_INTERVAL_MS
          value: "${clickhouse_write_interval_ms}"

        - name: LANGFUSE_EVAL_EXECUTION_WORKER_CONCURRENCY
          value: "1"
        - name: LANGFUSE_LLM_AS_JUDGE_EXECUTION_WORKER_CONCURRENCY
          value: "1"

        # Sum ≤ max_concurrent_queries=${clickhouse_max_concurrent_queries} with headroom for UI reads.
        - name: LANGFUSE_INGESTION_QUEUE_PROCESSING_CONCURRENCY
          value: "${ingestion_queue_concurrency}"
        - name: LANGFUSE_TRACE_UPSERT_WORKER_CONCURRENCY
          value: "${trace_upsert_worker_concurrency}"
        - name: LANGFUSE_OTEL_INGESTION_QUEUE_PROCESSING_CONCURRENCY
          value: "${otel_ingestion_queue_concurrency}"

        # Disabling each queue removes one ioredis consumer + eliminates 30s polling noise.
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

  ingress:
    enabled: false

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
  directUrl: "postgresql://${postgres_user}:${postgres_password}@${postgres_host}:${postgres_port}/${postgres_database}"

redis:
  deploy: false
  host: "${redis_host}"
  port: ${redis_port}
  auth:
    username: "default"
    existingSecret: langfuse-redis-password
    existingSecretPasswordKey: redis-password
    database: ${redis_db}

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
  # Disabling 18 internal *_log tables eliminated a 1.7-core idle CPU spike from constant CH merges.
  # remove="remove" is CH's config-merge directive (8.0.5 chart — 9.x configdFiles unavailable).
  extraOverrides: |
    <clickhouse>
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

      <!-- RAM tuning: defaults sized for 2xlarge cloud; homelab DB is 1.09 GiB. -->
      <mark_cache_size>134217728</mark_cache_size>             <!-- 128 MiB -->
      <index_mark_cache_size>33554432</index_mark_cache_size>  <!-- 32 MiB -->
      <uncompressed_cache_size>0</uncompressed_cache_size>

      <!-- 1.2 GiB of 1.5 GiB limit; leaves 300 MiB OS page cache. -->
      <max_server_memory_usage>1288490188</max_server_memory_usage>

      <!-- Background pools scaled for single-shard, no-Keeper. -->
      <background_pool_size>2</background_pool_size>
      <background_schedule_pool_size>8</background_schedule_pool_size>
      <background_buffer_flush_schedule_pool_size>1</background_buffer_flush_schedule_pool_size>
      <background_message_broker_schedule_pool_size>1</background_message_broker_schedule_pool_size>
      <background_merges_mutations_concurrency_ratio>1</background_merges_mutations_concurrency_ratio>
      <background_move_pool_size>1</background_move_pool_size>
      <background_common_pool_size>2</background_common_pool_size>
      <background_distributed_schedule_pool_size>1</background_distributed_schedule_pool_size>
      <background_fetches_pool_size>1</background_fetches_pool_size>

      <!-- chart default of 20 caused "Too many simultaneous queries" during OTel drain bursts. -->
      <max_thread_pool_size>500</max_thread_pool_size>
      <max_concurrent_queries>${clickhouse_max_concurrent_queries}</max_concurrent_queries>
      <async_insert_threads>2</async_insert_threads>

      <!-- number_of_free_entries_in_pool_to_* MUST be ≤ pool_size*ratio=2 or CH refuses to start. -->
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

  # Must use s3.concurrency.{reads,writes} — chart injects these env vars; worker.pod.additionalEnv causes a merge-patch duplicate.
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
