# =============================================================================
# grafana module — Grafana on v2 baseline DBs (Postgres backend)
# =============================================================================
#
# Deploys:
#   1. grafana namespace
#   2. random_password for the Grafana admin login
#   3. random_password for the grafana DB role
#   4. Bootstrap Secret (pg admin creds for the Job)
#   5. Bootstrap Job — creates grafana DB + role on v2 Postgres (idempotent)
#   6. Admin Secret — admin user/password (chart's `admin.existingSecret` pattern)
#   7. grafana-community/grafana Helm release configured with:
#        - external Postgres backend (`grafana.ini.database`)
#        - Grafana sidecar enabled for both `grafana_datasource: "1"` and
#          `grafana_dashboard: "1"` ConfigMaps cluster-wide → Mimir/Loki/Tempo
#          modules can register themselves later without editing this module
#        - ServiceMonitor for future Alloy/Mimir scrape (costs nothing if no
#          scraper is online yet)
#        - Persistence DISABLED — DB is external, plugins re-install at boot,
#          BLEVE search index regenerates
#   8. Tailscale Ingress at grafana.<domain>.ts.net (Homepage tile in
#      "Observability" group, mandatory `gethomepage.dev/href` annotation)
#
# Chart migration note (per memory feedback_module_migration_playbook):
#   The original `grafana/grafana` chart was deprecated 2026-01-30 and
#   relocated to `grafana-community/grafana`. This module uses the new repo.
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "grafana" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "grafana"
      "app.kubernetes.io/component"  = "observability-ui"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Admin password (random, persisted in tfstate)
# -----------------------------------------------------------------------------
# Retrieve via `terragrunt output -raw admin_password`. User logs in at
# https://grafana.<domain>.ts.net with admin / <this value>.
# -----------------------------------------------------------------------------
resource "random_password" "admin" {
  length  = 32
  special = false # alphanumeric — avoids paste/escape gotchas in the browser login form
}

# -----------------------------------------------------------------------------
# Grafana DB role password
# -----------------------------------------------------------------------------
resource "random_password" "db" {
  length  = 32
  special = false # alphanumeric — avoids URL-encoding edge cases in postgres:// URI
}

# -----------------------------------------------------------------------------
# Admin Secret — chart consumes this via `admin.existingSecret`
# -----------------------------------------------------------------------------
# Keys MUST be `admin-user` / `admin-password` (the chart's defaults at
# `admin.userKey` / `admin.passwordKey`).
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "admin" {
  metadata {
    name      = "${var.release_name}-admin"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    "admin-user"     = "admin"
    "admin-password" = random_password.admin.result
  }
}

# -----------------------------------------------------------------------------
# DB Secret — exposes GF_DATABASE_PASSWORD env var inside the pod
# -----------------------------------------------------------------------------
# `grafana.ini.database.password = $__env{GF_DATABASE_PASSWORD}` reads from
# this secret via the chart's `envFromSecrets` mechanism. Avoids putting the
# password in the rendered grafana.ini ConfigMap.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "db" {
  metadata {
    name      = "${var.release_name}-db"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    GF_DATABASE_PASSWORD = random_password.db.result
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Secret — admin creds for the bootstrap Job
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "bootstrap" {
  metadata {
    name      = "${var.release_name}-bootstrap"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    PGUSER     = var.postgres_admin_user
    PGPASSWORD = var.postgres_admin_password
    PGHOST     = var.postgres_host
    PGPORT     = tostring(var.postgres_port)
    NEW_DB     = var.grafana_db_name
    NEW_USER   = var.grafana_db_user
    NEW_PASS   = random_password.db.result
  }
}

# -----------------------------------------------------------------------------
# Bootstrap Job — idempotent CREATE DATABASE / CREATE ROLE
# -----------------------------------------------------------------------------
# Same pattern as the openwebui module. Job name embeds an 8-char hash of the
# DB password so any password rotation triggers a fresh Job run that re-applies
# the new password (ALTER ROLE).
# -----------------------------------------------------------------------------
locals {
  pw_hash = substr(sha256(random_password.db.result), 0, 8)
}

resource "kubernetes_job_v1" "bootstrap_db" {
  metadata {
    name      = "${var.release_name}-bootstrap-db-${local.pw_hash}"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "grafana-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "psql"
          image = "postgres:18-bookworm"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.bootstrap.metadata[0].name
            }
          }

          command = ["/bin/bash", "-c"]
          args = [<<-EOT
            set -euo pipefail
            echo "Bootstrapping Postgres for $NEW_DB / $NEW_USER..."

            # Create role if not exists; otherwise update password
            psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$NEW_USER'" | grep -q 1 && {
              echo "  role $NEW_USER exists, updating password"
              psql -c "ALTER ROLE \"$NEW_USER\" WITH LOGIN PASSWORD '$NEW_PASS';"
            } || {
              echo "  creating role $NEW_USER"
              psql -c "CREATE ROLE \"$NEW_USER\" WITH LOGIN PASSWORD '$NEW_PASS';"
            }

            # Create database if not exists
            psql -tc "SELECT 1 FROM pg_database WHERE datname='$NEW_DB'" | grep -q 1 || {
              echo "  creating database $NEW_DB"
              psql -c "CREATE DATABASE \"$NEW_DB\" OWNER \"$NEW_USER\";"
            }

            # Ensure privileges on public schema
            psql -d "$NEW_DB" -c "GRANT ALL ON SCHEMA public TO \"$NEW_USER\";"
            psql -d "$NEW_DB" -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO \"$NEW_USER\";"

            echo "Done."
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

  depends_on = [kubernetes_secret_v1.bootstrap]
}

# -----------------------------------------------------------------------------
# Helm release — grafana-community/grafana
# -----------------------------------------------------------------------------
resource "helm_release" "grafana" {
  name       = var.release_name
  repository = "https://grafana-community.github.io/helm-charts"
  chart      = "grafana"
  version    = var.chart_version
  namespace  = kubernetes_namespace_v1.grafana.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values.yaml.tpl", {
      admin_secret_name        = kubernetes_secret_v1.admin.metadata[0].name
      db_secret_name           = kubernetes_secret_v1.db.metadata[0].name
      postgres_host            = var.postgres_host
      postgres_port            = var.postgres_port
      grafana_db_name          = var.grafana_db_name
      grafana_db_user          = var.grafana_db_user
      replicas                 = var.replicas
      cpu_request              = var.cpu_request
      memory_request           = var.memory_request
      memory_limit             = var.memory_limit
      persistence_enabled      = var.persistence_enabled ? "true" : "false"
      storage_class            = var.storage_class
      storage_size             = var.storage_size
      sidecar_search_namespace = var.sidecar_search_namespace
      service_monitor_enabled  = var.service_monitor_enabled ? "true" : "false"
      domain                   = "${var.tailscale_hostname}.${var.tailscale_domain}"
    })
  ]

  wait          = true
  wait_for_jobs = true
  timeout       = 600

  depends_on = [
    kubernetes_job_v1.bootstrap_db,
    kubernetes_secret_v1.admin,
    kubernetes_secret_v1.db,
  ]
}

# -----------------------------------------------------------------------------
# Tailscale Ingress
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "ingress" {
  manifest = yamldecode(templatefile("${path.module}/k8s/ingress.yaml.tpl", {
    namespace          = kubernetes_namespace_v1.grafana.metadata[0].name
    release_name       = var.release_name
    tailscale_hostname = var.tailscale_hostname
    tailscale_domain   = var.tailscale_domain
    ingress_class_name = var.tailscale_ingress_class
  }))

  depends_on = [helm_release.grafana]
}
