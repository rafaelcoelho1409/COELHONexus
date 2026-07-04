# =============================================================================
# Neo4j Helm values (rendered by templatefile() in main.tf)
# =============================================================================
# Chart: neo4j/neo4j v2026.3.1 (appVersion 2026.03.1, Community Edition)
# Repo:  https://helm.neo4j.com/neo4j
#
# Tier 1 (2026-05-25) — RAM/CPU optimization, see docs/neo4j_optimization.md.
# All variables interpolated as SCALARS (per memory feedback_yamlencode_helm_values).
# =============================================================================

neo4j:
  name: ${release_name}
  edition: community
  password: ${neo4j_password}
  acceptLicenseAgreement: "yes"

  # Burstable QoS: request matches actual working-set, limit kept at chart
  # minimum 2Gi for 6h apoc.export.cypher.all backup headroom.
  # Chart `evaluateMemory` enforces limit.memory >= 2Gi; can't go lower.
  resources:
    requests:
      cpu:    "${cpu_request}"
      memory: "${memory_request}"
    limits:
      cpu:    "${cpu_limit}"
      memory: "${memory_limit}"

# -----------------------------------------------------------------------------
# JVM args — full override of chart defaults
# -----------------------------------------------------------------------------
# Chart default includes `-XX:+AlwaysPreTouch` which commits the full Xmx at
# boot → RSS = Xmx instantly. For RAM-constrained homelab we want lazy commit:
# RSS grows only to actual working-set.
#
# Dropped vs chart default:
#   +AlwaysPreTouch, +UnlockExperimentalVMOptions, +TrustFinalNonStaticFields,
#   FlightRecorderOptions=stackdepth=256, +UnlockDiagnosticVMOptions,
#   +DebugNonSafepoints  (all JFR / deep-diagnostic, not useful in homelab)
#
# Added:
#   -XX:-AlwaysPreTouch                lazy heap commit
#   -XX:+ExitOnOutOfMemoryError        K8s restart > limping JVM
#   -XX:G1HeapRegionSize=4m            lower region-table overhead for small heap
#   -XX:ReservedCodeCacheSize=128m     was 240m default
#   -XX:InitialCodeCacheSize=32m
#   -XX:+UseStringDeduplication        G1 freebie; trims repeated entity names
#   -Xss256k                           thread stack 1m → 256k (saves on 50-200 Bolt workers)
#   --enable-native-access=ALL-UNNAMED silences Java 21 JNA warning
#
# Removed 2026-06-07:
#   -XX:MaxMetaspaceSize=128m  was bounding APOC class loading, but APOC
#                              + LangChain LLMGraphTransformer (COELHO
#                              Nexus YCS Phase 3) hit the cap and the JVM
#                              crash-looped with OutOfMemoryError:
#                              Metaspace. Letting the JVM pick its stock
#                              default — same approach as the v1 cloud
#                              module that ran stable for months.
# -----------------------------------------------------------------------------
jvm:
  useNeo4jDefaultJvmArguments: false
  additionalJvmArguments:
    - "-XX:+UseG1GC"
    - "-XX:-OmitStackTraceInFastThrow"
    - "-XX:-AlwaysPreTouch"
    - "-XX:+DisableExplicitGC"
    - "-XX:+ExitOnOutOfMemoryError"
    - "-XX:G1HeapRegionSize=4m"
    - "-XX:ReservedCodeCacheSize=128m"
    - "-XX:InitialCodeCacheSize=32m"
    - "-XX:+UseStringDeduplication"
    - "-Xss256k"
    - "-Djdk.nio.maxCachedBufferSize=1024"
    - "-Dio.netty.tryReflectionSetAccessible=true"
    - "-Djdk.tls.ephemeralDHKeySize=2048"
    - "-Djdk.tls.rejectClientInitiatedRenegotiation=true"
    - "-Dlog4j2.disable.jmx=true"
    - "--add-opens=java.base/java.nio=ALL-UNNAMED"
    - "--add-opens=java.base/java.io=ALL-UNNAMED"
    - "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED"
    - "--enable-native-access=ALL-UNNAMED"

# -----------------------------------------------------------------------------
# Memory + features + APOC config
# -----------------------------------------------------------------------------
# Tuned for a homelab knowledge graph (~2 MiB on disk, scales to ~50 MiB):
#   - JVM heap: 256m initial → 512m max (down from 512m → 1G)
#   - Page cache: 64m (down from 256m)
#
# APOC plugin loading (REQUIRED for LangChain — see module main.tf docstring):
#   - server.directories.plugins points at /var/lib/neo4j/labs (where APOC
#     Core ships in the official Neo4j image)
#   - allowlist + unrestricted entries enable the procedures
#
# Bolt: NO native TLS (`tls_level: DISABLED`). External Ingress terminates
# TLS for us with a Let's Encrypt cert; Neo4j just speaks plain Bolt and
# handles WebSocket upgrades on the same port.
# -----------------------------------------------------------------------------
config:
  server.memory.heap.initial_size: "${heap_initial_size}"
  server.memory.heap.max_size: "${heap_max_size}"
  server.memory.pagecache.size: "${pagecache_size}"

  # Advertise the external hostnames so the Browser dynamically constructs
  # the correct Bolt URL when the user opens https://neo4j.<domain>.
  # Bolt advertised port is 443 (the external Ingress's HTTPS port — that's
  # where Browser's wss:// connects).
  server.bolt.advertised_address: "${bolt_advertised_address}"
  server.http.advertised_address: "${browser_advertised_address}:443"

  # APOC Core (bundled in the official image at /var/lib/neo4j/labs/)
  server.directories.plugins: "/var/lib/neo4j/labs"
  dbms.security.procedures.unrestricted: "apoc.*"
  dbms.security.procedures.allowlist: "apoc.*"

  # Bolt TLS DISABLED — external Ingress handles TLS termination.
  server.bolt.tls_level: "DISABLED"

  # Tx log rotation — chart default ("1 days") never rotates on quiet homelab.
  db.tx_log.rotation.retention_policy: "${tx_log_retention_policy}"

  # X-Forward hardening (silences SECURITY WARNING on every boot).
  # Correct 2026.03 keys live under server.http.x_forward.* (verified by
  # extracting setting registrations from neo4j-server-2026.03.1.jar:ServerSettings).
  server.http.x_forward.allow_proxies: "${http_allow_proxies}"
  server.http.x_forward.allow_hosts:   "${http_allow_hosts}"

  # Feature disables — boolean → string (Helm values must be strings).
  # NOTE: `server.metrics.enabled` and `dbms.security.log_successful_authentication`
  # are Enterprise-only — CE has no such settings. Dropped (would only WARN).
  # `server.https.enabled` likewise — CE doesn't bind an HTTPS connector by
  # default (logs show only "HTTP enabled on 0.0.0.0:7474.", no HTTPS line).
  dbms.usage_report.enabled:   "${enable_usage_report}"
  dbms.fleet_manager.enabled:  "${enable_fleet_manager}"

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------
volumes:
  data:
    mode: dynamic
    dynamic:
      storageClassName: ${storage_class}
      requests:
        storage: ${storage_size}

# -----------------------------------------------------------------------------
# Service — chart's default ClusterIP. Both external Ingresses target this.
# -----------------------------------------------------------------------------
services:
  neo4j:
    enabled: true
    spec:
      type: ClusterIP
    ports:
      http:
        enabled: true # 7474 — Browser via external Ingress
      https:
        enabled: false # 7473 — not used (external Ingress terminates TLS)
      bolt:
        enabled: true # 7687 — Bolt via external Ingress (WSS)

# -----------------------------------------------------------------------------
# Chart's built-in Ingress disabled — using external Ingress.
# -----------------------------------------------------------------------------
ingress:
  enabled: false

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------
securityContext:
  runAsNonRoot: true
  runAsUser: 7474
  runAsGroup: 7474
  fsGroup: 7474
