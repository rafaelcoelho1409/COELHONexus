# =============================================================================
# Leaf — alloy (coelhonexus standalone, 30-observability layer)
# =============================================================================
# Alloy — single-Deployment LGTM telemetry collector. Receives:
#   - OTLP gRPC :4317 + HTTP :4318 (from in-cluster apps — DD/YCS/RR spans
#     and metrics from FastAPI + Celery)
#   - K8s pod logs → Loki push
#   - ServiceMonitor + PodMonitor discovery → Mimir remote_write
# All three storage backends MUST be applied first.
#
# Adaptations vs COELHO Cloud's leaf:
#   - DROP the external-ingress-operator dependency
#   - External OTLP Ingress capability REMOVED from main.tf entirely
#     (2026-07-02) — was already off by default (the OTLP-via-external-proxy flag
#     defaulted false) and always would be on this cluster.
#   - keep dependency "mimir" / "loki" / "tempo" — state files exist now
#   - cluster_label = "coelhonexus" (via env.hcl env_name)
# =============================================================================

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/alloy"
}

dependency "k3d" {
  config_path = "../../00-bootstrap/k3d"

  mock_outputs = {
    cluster_name    = "mock"
    kubeconfig_path = "/tmp/nonexistent-kubeconfig"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "mimir" {
  config_path = "../mimir"

  mock_outputs = {
    remote_write_url = "http://mimir-distributor.mimir.svc.cluster.local:8080/api/v1/push"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "loki" {
  config_path = "../loki"

  mock_outputs = {
    push_url = "http://loki.loki.svc.cluster.local:3100/loki/api/v1/push"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependency "tempo" {
  config_path = "../tempo"

  mock_outputs = {
    otlp_grpc_endpoint = "tempo.tempo.svc.cluster.local:4317"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "init", "plan"]
}

dependencies {
  paths = ["../../10-platform/monitoring-crds"]
}

generate "providers" {
  path      = "providers.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "kubernetes" {
      config_path = "${dependency.k3d.outputs.kubeconfig_path}"
    }
    provider "helm" {
      kubernetes = {
        config_path = "${dependency.k3d.outputs.kubeconfig_path}"
      }
    }
  EOF
}

inputs = {
  cluster_label = include.root.locals.env.cluster_name

  mimir_remote_write_url   = dependency.mimir.outputs.remote_write_url
  loki_push_url            = dependency.loki.outputs.push_url
  tempo_otlp_grpc_endpoint = dependency.tempo.outputs.otlp_grpc_endpoint


  # Critical: OTLP receiver REQUIRED — FastAPI + Celery push gen_ai.* spans
  # via gRPC :4317 and metrics via gRPC :4317.
  alloy_enable_otlp_receiver = true

  # Defaults from variables.tf are appropriate:
  #   chart 1.8.0, Deployment 1 replica, 100m/256Mi/768Mi resources,
  #   ServiceMonitor on, RBAC for ServiceMonitor/PodMonitor discovery.
}
