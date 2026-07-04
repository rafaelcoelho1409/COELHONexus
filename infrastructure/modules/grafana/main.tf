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
#   8. External Ingress at grafana.<domain>.example.com (Homepage tile in
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
# Admin login secret
# -----------------------------------------------------------------------------
# Defaults to a generated password persisted in tfstate, but callers can supply
# deterministic `admin_user` / `admin_password` values for demo-style localhost
# access contracts.
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

locals {
  grafana_admin_user     = trimspace(var.admin_user) != "" ? trimspace(var.admin_user) : "admin"
  grafana_admin_password = var.admin_password != null && trimspace(var.admin_password) != "" ? trimspace(var.admin_password) : random_password.admin.result
  grafana_root_url       = var.root_url != null && trimspace(var.root_url) != "" ? trimspace(var.root_url) : "https://${var.tailscale_hostname}.${var.tailscale_domain}/"
  grafana_domain         = var.root_url != null && trimspace(var.root_url) != "" ? try(regex("^https?://([^/]+)", trimspace(var.root_url))[0], "${var.tailscale_hostname}.${var.tailscale_domain}") : "${var.tailscale_hostname}.${var.tailscale_domain}"
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
    "admin-user"     = local.grafana_admin_user
    "admin-password" = local.grafana_admin_password
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
# Admin sync config — lets the Grafana CLI target the real external Postgres DB
# -----------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "admin_sync" {
  metadata {
    name      = "${var.release_name}-admin-sync"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana-admin-sync"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    "grafana.ini" = <<-EOT
      [database]
      type = postgres
      host = ${var.postgres_host}:${var.postgres_port}
      name = ${var.grafana_db_name}
      user = ${var.grafana_db_user}
      password = $__env{GF_DATABASE_PASSWORD}
      ssl_mode = disable
    EOT
  }
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
      domain                   = local.grafana_domain
      root_url                 = local.grafana_root_url
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
# Admin sync Job — forces the persisted Grafana admin password to match the
# Kubernetes admin secret even when Grafana is using an external Postgres DB.
# -----------------------------------------------------------------------------
# Grafana's admin env vars / Helm secret wiring are bootstrap-time inputs.
# Once the admin user already exists in Postgres, updating the Secret alone
# does not reliably reset the persisted password. The official Grafana docs
# recommend `grafana cli admin reset-admin-password` with BOTH `--homepath`
# and `--config` when using an external DB so the CLI does not accidentally
# reset a default local SQLite database instead.
# -----------------------------------------------------------------------------
locals {
  admin_sync_hash = substr(sha256(join(":", [
    local.grafana_admin_password,
    random_password.db.result,
    var.grafana_db_name,
    var.grafana_db_user,
    var.grafana_cli_image,
  ])), 0, 8)
}

resource "kubernetes_job_v1" "sync_admin_password" {
  metadata {
    name      = "${var.release_name}-sync-admin-${local.admin_sync_hash}"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "grafana-admin-sync"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "grafana-admin-sync"
        }
      }

      spec {
        restart_policy = "OnFailure"

        security_context {
          fs_group = 472
        }

        container {
          name  = "grafana-cli"
          image = var.grafana_cli_image

          command = ["/bin/sh", "-ec"]
          args = [<<-EOT
            for attempt in $(seq 1 60); do
              if grafana cli --homepath /usr/share/grafana --config /etc/grafana/grafana.ini admin reset-admin-password "$GRAFANA_ADMIN_PASSWORD"; then
                exit 0
              fi
              echo "grafana admin password reset attempt $${attempt}/60 failed; retrying in 5s"
              sleep 5
            done

            echo "grafana admin password reset did not succeed after 60 attempts"
            exit 1
          EOT
          ]

          env {
            name = "GF_DATABASE_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.db.metadata[0].name
                key  = "GF_DATABASE_PASSWORD"
              }
            }
          }

          env {
            name = "GRAFANA_ADMIN_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.admin.metadata[0].name
                key  = "admin-password"
              }
            }
          }

          volume_mount {
            name       = "grafana-config"
            mount_path = "/etc/grafana"
            read_only  = true
          }

          security_context {
            allow_privilege_escalation = false
            run_as_non_root            = true
            run_as_user                = 472
            run_as_group               = 472

            capabilities {
              drop = ["ALL"]
            }

            seccomp_profile {
              type = "RuntimeDefault"
            }
          }

          resources {
            requests = {
              cpu    = "10m"
              memory = "64Mi"
            }
            limits = {
              memory = "128Mi"
            }
          }
        }

        volume {
          name = "grafana-config"

          config_map {
            name = kubernetes_config_map_v1.admin_sync.metadata[0].name
          }
        }
      }
    }
  }

  wait_for_completion = true
  timeouts {
    create = "10m"
  }

  depends_on = [
    helm_release.grafana,
    kubernetes_config_map_v1.admin_sync,
    kubernetes_secret_v1.admin,
    kubernetes_secret_v1.db,
  ]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Service, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from the external Ingress above — that stays unconditional and
# works as-is on any environment with a real external ingress controller. This is for
# k3d standalone dev clusters. Selector matches the chart's `grafana` Service
# (`app.kubernetes.io/instance: grafana, app.kubernetes.io/name: grafana`),
# verified via `kubectl get svc grafana -n grafana -o yaml` against the live
# cluster. target_port 3000 is the pod's actual containerPort behind the
# Service's named "grafana" targetPort — confirmed via `kubectl get pods -n
# grafana -o json` (Service target ports can be names, but this module's
# k3d_expose only accepts numeric ports).
# -----------------------------------------------------------------------------
module "k3d_expose" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.grafana.metadata[0].name
  service_name = var.release_name
  pod_selector = {
    "app.kubernetes.io/instance" = var.release_name
    "app.kubernetes.io/name"     = "grafana"
  }
  ports = [
    { name = "http", target_port = 3000, node_port = var.k3d_node_port },
  ]

  depends_on = [helm_release.grafana]
}
