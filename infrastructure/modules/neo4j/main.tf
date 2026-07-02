# =============================================================================
# neo4j module — Graph database (Community Edition) with HTTPS-first exposure
# =============================================================================
#
# v1 → v2 KEY DIFFERENCE: HTTPS instead of HTTP, end-to-end LE-trusted certs
# ----------------------------------------------------------------------------
# v1 exposed Browser + Bolt over a single Tailscale LoadBalancer with PLAIN
# HTTP because the alternative — Tailscale Ingress with HTTPS — broke the
# Browser's WebSocket connection to Bolt (mixed-content security policy).
#
# v2 fix: TWO Tailscale Ingresses (both L7, both with Tailscale's auto-issued
# Let's Encrypt certs):
#   - Tailscale Ingress: Browser at https://neo4j.<domain> (port 7474)
#   - Tailscale Ingress: Bolt at https://neo4j-bolt.<domain> (port 7687,
#     Tailscale terminates TLS with LE cert; Neo4j receives plain HTTP and
#     handles the WebSocket upgrade for Bolt-over-WSS)
#   - Browser URL: bolt+s://neo4j-bolt.<domain> (port 443 implicit, LE cert
#     trusted by browsers natively — no `+ssc` needed, no mixed-content
#     warning, clean lock 🔒 in the address bar)
#
# Why this works (vs the earlier self-signed Bolt approach):
#   - Tailscale operator auto-provisions LE certs ONLY for L7 Ingress
#     resources. LoadBalancer services (L4 TCP) get tailnet IPs but no certs.
#   - Both Browser and Bolt traffic now traverse Tailscale Ingress proxies
#     with valid LE certs → browsers see clean HTTPS + WSS connections.
#   - Neo4j's Bolt port 7687 natively handles WebSocket upgrade requests,
#     so the Tailscale proxy can forward HTTP traffic to the same port that
#     accepts plain Bolt — no separate WebSocket endpoint needed.
#
# APOC FIXES (LangChain compatibility):
# ----------------------------------------------------------------------------
# Nexus uses `langchain_neo4j.Neo4jGraph` which depends on:
#   - apoc.meta.data / apoc.meta.stats (schema introspection — Nexus disables
#     refresh_schema due to slowness on 41k+ node graphs)
#   - apoc.refactor.mergeNodes (entity resolution in graph_builder.py)
#   - apoc.export.cypher.all (used by the backup CronJob)
#
# Required Neo4j config (carried from v1):
#   server.directories.plugins: "/var/lib/neo4j/labs"     ← APOC Core bundled here
#   dbms.security.procedures.unrestricted: "apoc.*"
#   dbms.security.procedures.allowlist: "apoc.*"
#
# Resources:
# ----------------------------------------------------------------------------
# Chart enforces minimum 500m CPU / 2Gi memory. JVM heap 512m-1G + pagecache
# 256m + OS overhead. PVC trimmed from v1's 10Gi → 5Gi.
#
# Backup:
# ----------------------------------------------------------------------------
# Every 6h, alpine init container runs cypher-shell + apoc.export.cypher.all
# to /backup, mc upload to MinIO `backups/neo4j/`, retention 20.
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "neo4j" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "neo4j"
      "app.kubernetes.io/component"  = "graph-db"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# MinIO creds Secret for the backup CronJob (env-from)
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "backup_creds" {
  metadata {
    name      = "${var.release_name}-backup-creds"
    namespace = kubernetes_namespace_v1.neo4j.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "neo4j-backup"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    MINIO_ENDPOINT   = var.minio_endpoint
    MINIO_ACCESS_KEY = var.minio_access_key
    MINIO_SECRET_KEY = var.minio_secret_key
    MINIO_BUCKET     = var.backup_bucket
    NEO4J_PASSWORD   = var.neo4j_password
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — ensure the backup bucket exists (idempotent)
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "ensure_bucket" {
  metadata {
    name      = "${var.release_name}-ensure-bucket"
    namespace = kubernetes_namespace_v1.neo4j.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "neo4j-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "neo4j-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "mc"
          image = "minio/mc:latest"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.backup_creds.metadata[0].name
            }
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            mc alias set m "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
            mc mb --ignore-existing "m/$MINIO_BUCKET"
            echo "Bucket $MINIO_BUCKET ready."
          EOT
          ]

          resources {
            requests = {
              cpu    = "10m"
              memory = "32Mi"
            }
            limits = {
              memory = "64Mi"
            }
          }
        }
      }
    }
  }

  wait_for_completion = true
  timeouts {
    create = "5m"
  }

  depends_on = [kubernetes_secret_v1.backup_creds]
}

# -----------------------------------------------------------------------------
# Helm release — neo4j/neo4j
# -----------------------------------------------------------------------------
resource "helm_release" "neo4j" {
  name       = var.release_name
  repository = "https://helm.neo4j.com/neo4j"
  chart      = "neo4j"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.neo4j.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      release_name   = var.release_name
      neo4j_password = var.neo4j_password

      # Resources (Burstable QoS)
      cpu_request    = var.cpu_request
      cpu_limit      = var.cpu_limit
      memory_request = var.memory_request
      memory_limit   = var.memory_limit

      # JVM heap + page cache
      heap_initial_size       = var.heap_initial_size
      heap_max_size           = var.heap_max_size
      pagecache_size          = var.pagecache_size
      tx_log_retention_policy = var.tx_log_retention_policy

      # Feature toggles
      enable_usage_report  = var.enable_usage_report
      enable_fleet_manager = var.enable_fleet_manager

      # X-Forward hardening
      http_allow_proxies = var.http_allow_proxies
      http_allow_hosts   = var.http_allow_hosts

      # Storage
      storage_class = var.storage_class
      storage_size  = var.storage_size

      # Tailscale advertised addresses
      browser_advertised_address = "${var.tailscale_hostname_browser}.${var.tailscale_domain}"
      bolt_advertised_address    = "${var.tailscale_hostname_bolt}.${var.tailscale_domain}:443"
    })
  ]

  wait    = true
  timeout = 900

  depends_on = [
    kubernetes_job_v1.ensure_bucket,
  ]
}

# -----------------------------------------------------------------------------
# Tailscale Ingress — Browser HTTPS (LE cert, port 7474 backend)
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.neo4j.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname_browser
    tailscale_domain   = var.tailscale_domain
    ingress_class_name = var.tailscale_ingress_class
    bolt_hostname      = var.tailscale_hostname_bolt
  }))

  depends_on = [helm_release.neo4j]
}

# -----------------------------------------------------------------------------
# Tailscale Ingress — Bolt over WSS (LE cert, port 7687 backend)
# -----------------------------------------------------------------------------
# Tailscale's L7 Ingress proxy:
#   - Terminates TLS with auto-issued LE cert for ${tailscale_hostname_bolt}
#   - Forwards HTTP to backend Service port 7687
#   - Passes WebSocket upgrade headers transparently
#
# Neo4j on port 7687 detects HTTP "Upgrade: websocket" header and switches
# to Bolt-over-WSS. The Browser opens wss://neo4j-bolt.<domain>/ over a
# clean LE-trusted TLS connection — no self-signed cert acceptance, no
# mixed-content warning.
#
# NO Homepage tile — Bolt is a data-plane endpoint, not a tile-worthy URL.
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "bolt_ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/bolt-ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.neo4j.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname_bolt
    ingress_class_name = var.tailscale_ingress_class
  }))

  depends_on = [helm_release.neo4j]
}

# -----------------------------------------------------------------------------
# Backup CronJob — neo4j-admin / cypher-shell dump → MinIO every 6h
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace        = kubernetes_namespace_v1.neo4j.metadata[0].name
    release_name     = var.release_name
    backup_schedule  = var.backup_schedule
    backup_retention = var.backup_retention
    creds_secret     = kubernetes_secret_v1.backup_creds.metadata[0].name
  }))

  depends_on = [helm_release.neo4j]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Service, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the Tailscale Ingresses above — those stay unconditional and
# work as-is on any environment with a real Tailscale operator. This is for
# k3d standalone dev clusters, where Tailscale Ingress is a documented no-op
# (see this module's live leaf comment: "DUMMY tailscale strings... inert
# without an Ingress controller"). Selector must match the pods behind the
# `neo4j` Service above (`app: neo4j, helm.neo4j.com/instance: neo4j`,
# verified via `kubectl get svc neo4j -n neo4j -o yaml`).
# -----------------------------------------------------------------------------
module "k3d_expose" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.neo4j.metadata[0].name
  service_name = var.release_name
  pod_selector = {
    "app"                     = "neo4j"
    "helm.neo4j.com/instance" = var.release_name
  }
  ports = [
    { name = "http", target_port = 7474, node_port = var.k3d_http_node_port },
    { name = "bolt", target_port = 7687, node_port = var.k3d_bolt_node_port },
  ]

  depends_on = [helm_release.neo4j]
}
