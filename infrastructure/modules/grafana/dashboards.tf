# =============================================================================
# Grafana dashboards — auto-download from grafana.com on every apply
# =============================================================================
#
# Workflow:
#   1. dashboards/dashboard-ids.json holds the curated list.
#   2. data.http fetches each dashboard's `revisions/latest/download` JSON
#      from grafana.com. data.http always re-runs on plan, so a `terragrunt
#      apply` on this module syncs to the latest published revision.
#   3. A chain of replace() calls rewrites datasource UIDs to match this
#      stack's conventions (`mimir` for metrics, `loki` for logs, `tempo`
#      for traces). grafana.com dashboards typically use ${DS_PROMETHEUS},
#      ${DS_LOKI} placeholders or hardcoded "Prometheus" datasource refs.
#   4. Each processed dashboard becomes a kubernetes_config_map_v1 with
#      label `grafana_dashboard: "1"`. Grafana's sidecar (configured for
#      cluster-wide watch, see helm/values.yaml.tpl) imports them at runtime.
#
# Why centralize in the grafana module:
#   - One file (dashboards/dashboard-ids.json) to curate the catalog.
#   - Mimir/Loki ALREADY ship excellent self-monitoring dashboards via their
#     own charts — this catalog focuses on apps/data-services that don't.
#
# Disable per-deploy by setting `provision_dashboards = false` in the leaf.
# =============================================================================

# -----------------------------------------------------------------------------
# Parse the catalog
# -----------------------------------------------------------------------------
locals {
  dashboard_config = jsondecode(file("${path.module}/dashboards/dashboard-ids.json"))

  dashboards_to_provision = var.provision_dashboards ? {
    for d in local.dashboard_config.dashboards :
    d.name => {
      id          = d.id
      folder      = d.folder
      description = d.description
    }
  } : {}
}

# -----------------------------------------------------------------------------
# Download — data.http re-fetches on every plan
# -----------------------------------------------------------------------------
data "http" "grafana_dashboards" {
  for_each = local.dashboards_to_provision

  url = "https://grafana.com/api/dashboards/${each.value.id}/revisions/latest/download"

  request_headers = {
    Accept = "application/json"
  }
}

# -----------------------------------------------------------------------------
# Datasource UID rewriting — staged replace() chain
# -----------------------------------------------------------------------------
# grafana.com dashboards reference datasources by `${DS_*}` template variables
# (set at import time) or hardcoded names like "Prometheus". Our datasource
# UIDs are: `mimir`, `loki`, `tempo`. We need to rewrite each form to point
# at our stack's UIDs.
#
# Order matters — handle Loki and Tempo BEFORE the catch-all Prometheus->Mimir
# pass, otherwise ${DS_LOKI} would be matched by the generic ${DS_*} regex.
# -----------------------------------------------------------------------------
locals {
  # Step 1: ${DS_LOKI...} -> Loki (handle FIRST, before catch-all)
  d_step1 = {
    for name in keys(local.dashboards_to_provision) :
    name => replace(
      data.http.grafana_dashboards[name].response_body,
      "/\\$\\{DS_LOKI[A-Za-z0-9_-]*\\}/",
      "Loki",
    )
  }

  # Step 2: ${DS_TEMPO...} -> Tempo (handle BEFORE catch-all)
  d_step2 = {
    for name, json in local.d_step1 :
    name => replace(json, "/\\$\\{DS_TEMPO[A-Za-z0-9_-]*\\}/", "Tempo")
  }

  # Step 3: any remaining ${DS_*} variables -> Mimir (catch-all)
  d_step3 = {
    for name, json in local.d_step2 :
    name => replace(json, "/\\$\\{DS_[A-Za-z0-9_-]+\\}/", "Mimir")
  }

  # Step 4: $DS_* variables (no braces, less common but seen in older dashboards)
  d_step4 = {
    for name, json in local.d_step3 :
    name => replace(json, "/\\$DS_[A-Za-z0-9_-]+/", "Mimir")
  }

  # Step 5: hardcoded "datasource": "Prometheus" -> "Mimir" (case-sensitive)
  d_step5 = {
    for name, json in local.d_step4 :
    name => replace(json, "\"datasource\": \"Prometheus\"", "\"datasource\": \"Mimir\"")
  }

  # Step 6: hardcoded "uid": "Prometheus" -> "mimir"
  d_step6 = {
    for name, json in local.d_step5 :
    name => replace(json, "\"uid\": \"Prometheus\"", "\"uid\": \"mimir\"")
  }

  # Step 7: hardcoded "uid": "prometheus" (lowercase) -> "mimir"
  d_step7 = {
    for name, json in local.d_step6 :
    name => replace(json, "\"uid\": \"prometheus\"", "\"uid\": \"mimir\"")
  }

  # Step 8: variable selector "text": "Prometheus" -> "Mimir"
  d_step8 = {
    for name, json in local.d_step7 :
    name => replace(json, "\"text\": \"Prometheus\"", "\"text\": \"Mimir\"")
  }

  # Step 9: variable selector "value": "Prometheus" / "prometheus" -> "Mimir"
  d_step9 = {
    for name, json in local.d_step8 :
    name => replace(replace(
      json,
      "\"value\": \"Prometheus\"",
      "\"value\": \"Mimir\"",
      ),
      "\"value\": \"prometheus\"",
    "\"value\": \"Mimir\"")
  }

  # Step 10: default selector "text"/"value": "default" -> "Mimir" (some dashboards use 'default')
  processed_dashboards = {
    for name, json in local.d_step9 :
    name => replace(replace(
      json,
      "\"text\": \"default\"",
      "\"text\": \"Mimir\"",
      ),
      "\"value\": \"default\"",
    "\"value\": \"Mimir\"")
  }
}

# -----------------------------------------------------------------------------
# Materialize as labeled ConfigMaps in the grafana namespace
# -----------------------------------------------------------------------------
resource "kubernetes_config_map_v1" "grafana_dashboards" {
  for_each = local.dashboards_to_provision

  metadata {
    name      = "grafana-dashboard-${each.key}"
    namespace = kubernetes_namespace_v1.grafana.metadata[0].name

    labels = {
      grafana_dashboard              = "1"
      "app.kubernetes.io/name"       = "grafana-dashboard"
      "app.kubernetes.io/part-of"    = "grafana"
      "app.kubernetes.io/managed-by" = "terraform"
    }

    annotations = {
      grafana_folder = each.value.folder
      description    = each.value.description
      dashboard_id   = tostring(each.value.id)
      source         = "https://grafana.com/grafana/dashboards/${each.value.id}"
    }
  }

  data = {
    "${each.key}.json" = local.processed_dashboards[each.key]
  }

  depends_on = [helm_release.grafana]
}
