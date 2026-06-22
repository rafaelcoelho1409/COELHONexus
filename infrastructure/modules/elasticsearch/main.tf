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
#   4. Snapshot backup CronJob hits the in-cluster ES API with inline S3
#      credentials in the repo registration JSON.
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
#   3. Wait for elastic user Secret + ES readiness
#   4. Register snapshot repo via REST
#   5. Create backup CronJob + Tailscale Ingress for Kibana
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
      es_version                    = var.es_version
      kibana_version                = var.kibana_version
      es_memory_request             = var.es_memory_request
      es_memory_limit               = var.es_memory_limit
      es_java_heap                  = var.es_java_heap
      es_cpu_request                = var.es_cpu_request
      kibana_memory_request         = var.kibana_memory_request
      kibana_memory_limit           = var.kibana_memory_limit
      kibana_node_max_old_space_mb  = var.kibana_node_max_old_space_mb
      kibana_cpu_request            = var.kibana_cpu_request
      storage_size                  = var.storage_size
      storage_class                 = var.storage_class
      elastic_file_realm_secret_name = local.elastic_password_override_enabled ? kubernetes_secret_v1.elastic_file_realm[0].metadata[0].name : ""
      app_file_realm_secret_name    = var.app_user_enabled ? kubernetes_secret_v1.app_user[0].metadata[0].name : ""
      app_roles_secret_name         = var.app_user_enabled ? kubernetes_secret_v1.app_roles[0].metadata[0].name : ""
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

