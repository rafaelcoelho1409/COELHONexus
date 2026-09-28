# =============================================================================
# elasticsearch module — ECK Operator + ECK Stack (Elasticsearch + Kibana)
# =============================================================================
#
# Architecture:
#   1. Install eck-operator (chart `elastic/eck-operator`) — controller
#      + CRDs (Elasticsearch, Kibana, etc.) cluster-wide in `elastic-system`.
#   2. Install eck-stack (chart `elastic/eck-stack`) in this module's
#      namespace — creates Elasticsearch CR + Kibana CR; the operator
#      reconciles them into actual Pods/Services/Secrets.
#   3. ECK auto-generates: TLS certs (CA + node certs), the `elastic` user
#      password (Secret `elasticsearch-es-elastic-user`).
#   4. MinIO snapshot backups (ported from COELHO Cloud, 2026-09-28): S3
#      creds go into ES's keystore via `secureSettings` (ES 8.18 rejects
#      inline creds in repo settings) — see the "MinIO snapshot backups"
#      section below for the keystore Secret + bootstrap Jobs + CronJob.
#
# Why ECK over the Helm-chart-only options:
#   - elastic/elasticsearch chart was archived 2023, broken with newer ES
#   - bitnami/elasticsearch chart pulls images that no longer exist on
#     Docker Hub since the 2025 Bitnami commercial transition
#   - ECK Operator is officially supported by Elastic, has a clean CRD
#     model, and auto-handles certs + passwords + upgrades.
#
# Apply order (Terraform handles via depends_on):
#   1. ECK Operator (CRDs first — must exist before eck-stack chart renders)
#   2. ECK Stack (Elasticsearch + Kibana CRs)
#   3. Bootstrap Job — ensure MinIO backup bucket exists
#   4. Bootstrap Job — register MinIO as an ES snapshot repository via REST
#   5. Snapshot CronJob + local k3d_expose access
# =============================================================================

# -----------------------------------------------------------------------------
# Namespace for the CRs (operator runs in a separate namespace)
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "elasticsearch" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/component"  = "search"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

locals {
  elastic_password_override_enabled = trimspace(var.elastic_password_override) != ""
  admin_password_secret_name        = local.elastic_password_override_enabled ? kubernetes_secret_v1.elastic_admin[0].metadata[0].name : "elasticsearch-es-elastic-user"
  admin_password_secret_key         = "elastic"
  elastic_pw_hash                   = local.elastic_password_override_enabled ? substr(sha256(var.elastic_password_override), 0, 8) : ""
}

# -----------------------------------------------------------------------------
# ECK Operator — installs CRDs + controller cluster-wide
# -----------------------------------------------------------------------------
resource "helm_release" "eck_operator" {
  name             = "eck-operator"
  repository       = "https://helm.elastic.co"
  chart            = "eck-operator"
  version          = var.operator_chart_version
  namespace        = var.operator_namespace
  create_namespace = true

  wait    = true
  timeout = 300
}

# -----------------------------------------------------------------------------
# Optional deterministic admin Secret — mirrors the desired built-in `elastic`
# password so app-side demo secrets and infra-side admin jobs read the same value.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "elastic_admin" {
  count = local.elastic_password_override_enabled ? 1 : 0

  metadata {
    name      = "elasticsearch-admin-credentials"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/component"  = "admin-user"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  type = "Opaque"

  data = {
    elastic = var.elastic_password_override
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

# -----------------------------------------------------------------------------
# Optional app user — public-demo/local credential without using `elastic`
# -----------------------------------------------------------------------------
# Elastic's ECK docs recommend avoiding the generated `elastic` superuser for
# application traffic. For demo clusters we create a deterministic file-realm
# user whose role is constrained to Nexus's YCS index namespace.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "app_user" {
  count = var.app_user_enabled ? 1 : 0

  metadata {
    name      = "elasticsearch-coelhonexus-app-user"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/component"  = "app-user"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  type = "kubernetes.io/basic-auth"

  data = {
    username = var.app_username
    password = var.app_password
    roles    = var.app_role_name
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

resource "kubernetes_secret_v1" "app_roles" {
  count = var.app_user_enabled ? 1 : 0

  metadata {
    name      = "elasticsearch-coelhonexus-app-roles"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/component"  = "app-user"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    "roles.yml" = yamlencode({
      (var.app_role_name) = {
        cluster = ["monitor"]
        indices = [
          {
            names = ["coelhonexus-youtube-*"]
            privileges = [
              "create_index",
              "delete",
              "index",
              "manage",
              "monitor",
              "read",
              "view_index_metadata",
              "write",
            ]
          }
        ]
      }
    })
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

# -----------------------------------------------------------------------------
# ECK Stack — Elasticsearch + Kibana CRs
# -----------------------------------------------------------------------------
resource "helm_release" "eck_stack" {
  name       = "eck-stack"
  repository = "https://helm.elastic.co"
  chart      = "eck-stack"
  version    = var.stack_chart_version
  namespace  = kubernetes_namespace_v1.elasticsearch.metadata[0].name

  values = [
    templatefile("${path.module}/helm/values-stack.yaml.tpl", {
      es_version                     = var.es_version
      kibana_version                 = var.kibana_version
      es_memory_request              = var.es_memory_request
      es_memory_limit                = var.es_memory_limit
      es_java_heap                   = var.es_java_heap
      es_cpu_request                 = var.es_cpu_request
      kibana_memory_request          = var.kibana_memory_request
      kibana_memory_limit            = var.kibana_memory_limit
      kibana_node_max_old_space_mb   = var.kibana_node_max_old_space_mb
      kibana_cpu_request             = var.kibana_cpu_request
      storage_size                   = var.storage_size
      storage_class                  = var.storage_class
      elastic_file_realm_secret_name = local.elastic_password_override_enabled ? kubernetes_secret_v1.elastic_file_realm[0].metadata[0].name : ""
      app_file_realm_secret_name     = var.app_user_enabled ? kubernetes_secret_v1.app_user[0].metadata[0].name : ""
      app_roles_secret_name          = var.app_user_enabled ? kubernetes_secret_v1.app_roles[0].metadata[0].name : ""
    })
  ]

  wait    = true
  timeout = 900

  depends_on = [
    helm_release.eck_operator,
    kubernetes_namespace_v1.elasticsearch,
    kubernetes_secret_v1.elastic_file_realm,
    kubernetes_secret_v1.app_user,
    kubernetes_secret_v1.app_roles,
    kubernetes_secret_v1.es_s3_keystore,
  ]
}

# -----------------------------------------------------------------------------
# Optional elastic file-realm Secret — sets a deterministic password for the
# built-in `elastic` user via ECK's file realm.
#
# Why NOT the _password API: ECK configures the reserved realm as disabled for
# API-based password changes (returns 400 "reserved realm is disabled"). The
# correct mechanism is ECK's spec.auth.fileRealm: ECK bcrypt-hashes the
# password from this kubernetes.io/basic-auth Secret and writes the file realm
# entry, which takes priority over the reserved realm for authentication.
#
# Requires disableElasticUser: true in the ECK Elasticsearch CR (see
# values-stack.yaml.tpl) so ECK doesn't race-reconcile its own elastic entry.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "elastic_file_realm" {
  count = local.elastic_password_override_enabled ? 1 : 0

  metadata {
    name      = "elasticsearch-elastic-filerealm"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/component"  = "elastic-user"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  type = "kubernetes.io/basic-auth"

  data = {
    username = "elastic"
    password = var.elastic_password_override
    roles    = "superuser"
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

# -----------------------------------------------------------------------------
# MinIO snapshot backups (ported from COELHO Cloud, 2026-09-28)
# -----------------------------------------------------------------------------
# ES keystore Secret — S3 client credentials for the snapshot repo. ECK reads
# `secureSettings` from this Secret (see helm/values-stack.yaml.tpl) and
# auto-injects each KEY into ES's keystore on pod start. ES 8.18 REQUIRES
# this — inline credentials in repo settings are rejected outright.
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "es_s3_keystore" {
  metadata {
    name      = "elasticsearch-s3-keystore"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    "s3.client.default.access_key" = var.minio_access_key
    "s3.client.default.secret_key" = var.minio_secret_key
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

# -----------------------------------------------------------------------------
# Backup CronJob env-from Secret — MinIO + ES host (no S3 creds — keystore
# handles it).
# -----------------------------------------------------------------------------
resource "kubernetes_secret_v1" "backup_creds" {
  metadata {
    name      = "elasticsearch-backup-creds"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch-backup"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    MINIO_ENDPOINT     = var.minio_endpoint
    MINIO_ACCESS_KEY   = var.minio_access_key
    MINIO_SECRET_KEY   = var.minio_secret_key
    MINIO_BUCKET       = var.backup_bucket
    ELASTICSEARCH_HOST = "elasticsearch-es-http.${kubernetes_namespace_v1.elasticsearch.metadata[0].name}.svc.cluster.local"
    SNAPSHOT_REPO      = var.snapshot_repo_name
  }

  depends_on = [kubernetes_namespace_v1.elasticsearch]
}

# -----------------------------------------------------------------------------
# Bootstrap Job — ensure backup bucket exists
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "ensure_bucket" {
  metadata {
    name      = "elasticsearch-ensure-bucket"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "elasticsearch-bootstrap"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name = "mc"
          # minio/mc is dead upstream (archived Jul 2026, Docker Hub pulls
          # denied) — amazon/aws-cli is the maintained S3-compatible replacement.
          image = "amazon/aws-cli:2.37.1"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.backup_creds.metadata[0].name
            }
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            export AWS_ACCESS_KEY_ID="$MINIO_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
            aws configure set s3.addressing_style path
            if aws --endpoint-url "$MINIO_ENDPOINT" s3api head-bucket --bucket "$MINIO_BUCKET" 2>/dev/null; then
              echo "Bucket $MINIO_BUCKET ready (exists)."
            else
              aws --endpoint-url "$MINIO_ENDPOINT" s3api create-bucket --bucket "$MINIO_BUCKET"
              echo "Bucket $MINIO_BUCKET ready (created)."
            fi
          EOT
          ]

          # aws-cli (Python) is heavier than mc — 64Mi/128Mi headroom.
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
# Bootstrap Job — register MinIO as snapshot repository
# -----------------------------------------------------------------------------
# Reads the effective `elastic` password Secret at runtime (mounted as an
# env). Inline S3 creds are NOT in the JSON body — ES 8.18 rejects those;
# ES reads s3.client.default.access_key/secret_key from its keystore
# (loaded by ECK from kubernetes_secret_v1.es_s3_keystore above).
# -----------------------------------------------------------------------------
resource "kubernetes_job_v1" "register_repo" {
  metadata {
    name      = "elasticsearch-register-repo"
    namespace = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "elasticsearch-bootstrap"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 5

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name" = "elasticsearch-register-repo"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "register"
          image = "curlimages/curl:8.5.0"

          # Inject MinIO creds + ES host
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.backup_creds.metadata[0].name
            }
          }

          # Inject the effective elastic password (override Secret if set,
          # else ECK's auto-generated Secret) as ELASTIC_PASSWORD env var.
          env {
            name = "ELASTIC_PASSWORD"
            value_from {
              secret_key_ref {
                name = local.admin_password_secret_name
                key  = local.admin_password_secret_key
              }
            }
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -euo pipefail
            ES="https://$ELASTICSEARCH_HOST:9200"

            echo "Waiting for Elasticsearch..."
            for i in $(seq 1 60); do
              if curl -sfku "elastic:$ELASTIC_PASSWORD" "$ES/_cluster/health" | grep -q '"status"'; then
                echo "ES ready"
                break
              fi
              sleep 5
            done

            echo "Registering S3 snapshot repo $SNAPSHOT_REPO ..."
            ES_S3_ENDPOINT=$(echo "$MINIO_ENDPOINT" | sed -E 's,^https?://,,')
            # NO inline access_key/secret_key — ES 8.18 rejects these as insecure.
            # ES reads s3.client.default.access_key/secret_key from its keystore
            # (loaded by ECK from kubernetes_secret_v1.es_s3_keystore).
            curl -sfku "elastic:$ELASTIC_PASSWORD" -X PUT \
              "$ES/_snapshot/$SNAPSHOT_REPO" \
              -H 'Content-Type: application/json' \
              -d "{
                \"type\": \"s3\",
                \"settings\": {
                  \"bucket\": \"$MINIO_BUCKET\",
                  \"endpoint\": \"$ES_S3_ENDPOINT\",
                  \"protocol\": \"http\",
                  \"path_style_access\": true,
                  \"base_path\": \"elasticsearch\"
                }
              }"
            echo
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

  depends_on = [helm_release.eck_stack]
}

# -----------------------------------------------------------------------------
# Snapshot CronJob
# -----------------------------------------------------------------------------
resource "kubernetes_manifest" "backup_cronjob" {
  manifest = yamldecode(templatefile("${path.module}/k8s/backup-cronjob.yaml.tpl", {
    namespace        = kubernetes_namespace_v1.elasticsearch.metadata[0].name
    backup_schedule  = var.backup_schedule
    backup_retention = var.backup_retention
    creds_secret     = kubernetes_secret_v1.backup_creds.metadata[0].name
    admin_secret     = local.admin_password_secret_name
  }))

  depends_on = [kubernetes_job_v1.register_repo]
}

# -----------------------------------------------------------------------------
# Local access (k3d dev only) — NodePort Services, opt-in via enable_local_expose
# -----------------------------------------------------------------------------
# Separate from any external Ingress — that stays unconditional and works
# as-is on any environment with a real external ingress controller. This is for k3d
# standalone dev clusters. Elasticsearch and Kibana are reconciled by the ECK
# operator into separate pods with different selectors, so each needs its own
# k3d_expose instance. Selectors verified via `kubectl get svc -n <namespace>
# elasticsearch-es-http -o yaml` / `kibana-kb-http -o yaml` against the live
# cluster.
# -----------------------------------------------------------------------------
module "k3d_expose_es" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.elasticsearch.metadata[0].name
  service_name = "elasticsearch-es"
  pod_selector = {
    "common.k8s.elastic.co/type"                = "elasticsearch"
    "elasticsearch.k8s.elastic.co/cluster-name" = "elasticsearch"
  }
  ports = [
    { name = "https", target_port = 9200, node_port = var.k3d_es_node_port },
  ]

  depends_on = [helm_release.eck_stack]
}

module "k3d_expose_kibana" {
  count  = var.enable_local_expose ? 1 : 0
  source = "../k3d_expose"

  namespace    = kubernetes_namespace_v1.elasticsearch.metadata[0].name
  service_name = "kibana-kb"
  pod_selector = {
    "common.k8s.elastic.co/type" = "kibana"
    "kibana.k8s.elastic.co/name" = "kibana"
  }
  ports = [
    { name = "https", target_port = 5601, node_port = var.k3d_kibana_node_port },
  ]

  depends_on = [helm_release.eck_stack]
}

