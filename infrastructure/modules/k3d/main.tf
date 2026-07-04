# =============================================================================
# k3d module — cluster lifecycle via the k3d CLI
# =============================================================================
#
# k3d has no first-class Terraform provider, so we orchestrate it via local-exec.
# `null_resource` is just a vehicle for triggers + provisioners — it has no
# real state of its own beyond "created" / "destroyed".
#
# The triggers map causes REPLACEMENT (destroy + create) when any value changes.
# That's the desired behavior: changing servers/agents/k3s_version means a new
# cluster — k3d itself can't update those in place.
# =============================================================================

# ---------------------------------------------------------------------------
# Render registries.yaml from template — k3s/containerd registry mirror config.
# ---------------------------------------------------------------------------
# CRITICAL: contains a `localhost:<registry_port>` mirror entry pointing at
# the in-cluster registry container. Without this, Skaffold's hot-reload
# breaks (its sync matcher requires artifact and pod image strings to be
# literally identical — see infrastructure/modules/k3d/files/registries.yaml.tpl).
#
# NOT in the cluster's triggers map — content changes here must NOT cause
# cluster recreation. Existing clusters get the new file via the
# `configure_registry_mirrors` null_resource below (docker cp + k3s file
# watcher reload, no restart). Fresh clusters get it baked in via
# `--registry-config` in the create command.
# ---------------------------------------------------------------------------
resource "local_file" "registries_yaml" {
  filename = "${path.module}/.generated/registries.yaml"
  content = templatefile("${path.module}/files/registries.yaml.tpl", {
    cluster_name  = var.cluster_name
    registry_port = var.registry_port
  })
}

resource "null_resource" "cluster" {
  triggers = {
    cluster_name  = var.cluster_name
    k3s_version   = var.k3s_version
    servers       = var.servers
    agents        = var.agents
    registry_port = var.registry_port
    data_path     = var.data_path
    # NOTE: registries.yaml content deliberately NOT in this triggers map —
    # changing the mirror config should not destroy the cluster. The
    # `configure_registry_mirrors` resource below handles re-syncing.
  }

  # The cluster's create-provisioner reads registries.yaml from disk, so the
  # local_file must be rendered first.
  depends_on = [local_file.registries_yaml]

  # ---------------------------------------------------------------------------
  # Create provisioner — runs on resource creation (and on replace-after-destroy).
  # ---------------------------------------------------------------------------
  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail

      # Idempotency guard: if the cluster already exists with this name,
      # skip the create. This handles `tofu apply` re-runs without errors.
      #
      # --port flags below: local-expose ports for k3d dev clusters, baked in
      # here so a FRESH cluster has them from first boot. Already-running
      # clusters were patched once via `k3d cluster edit --port-add` (manual,
      # not Terraform — see infrastructure/modules/k3d_expose/). This list is
      # hand-maintained: add one --port line per port each time a data-store
      # module turns on enable_local_expose, or (for the four app-layer
      # entries below) when the Helm chart's own Service NodePort changes.
      #   23001:30474 — Neo4j HTTP Browser
      #   23012:30475 — Neo4j Bolt (Browser's login step needs this too)
      #   23011:30476 — Qdrant REST/Dashboard
      #   23013:30477 — Elasticsearch REST API (HTTPS, self-signed)
      #   23014:30478 — Kibana dashboard (HTTPS, self-signed)
      #   23015:30479 — MinIO S3 API
      #   23016:30480 — MinIO Console UI
      #   23017:30481 — LangFuse web UI
      #   23018:30482 — Playwright noVNC web UI
      #   23019:30483 — Playwright headed-mode CDP
      #   23020:30484 — Playwright headless-mode CDP
      #   23021:30485 — Rancher UI (HTTPS, self-signed)
      #   23022:30486 — Grafana UI
      #   23023:30487 — ArgoCD UI
      #   23024:30020 — FastAPI (app layer, k8s/helm/values.yaml)
      #   23025:30022 — Flower (app layer)
      #   23026:30023 — FastHTML (app layer)
      #   23027:30024 — FastMCP (app layer, internal-by-design but exposed for debugging)
      if k3d cluster list -o json 2>/dev/null | jq -e \
           '.[] | select(.name == "${var.cluster_name}")' >/dev/null 2>&1; then
        echo "Cluster ${var.cluster_name} already exists — skipping create."
      else
        echo "Creating k3d cluster ${var.cluster_name}..."
        k3d cluster create "${var.cluster_name}" \
          --servers ${var.servers} \
          --agents ${var.agents} \
          --image "rancher/k3s:${var.k3s_version}" \
          --registry-create "${var.cluster_name}-registry:0.0.0.0:${var.registry_port}" \
          --registry-config "${local_file.registries_yaml.filename}" \
          --volume "${var.data_path}/storage:/var/lib/rancher/k3s/storage@all" \
          --k3s-arg "--disable=traefik@server:*" \
          --port "23001:30474@loadbalancer" \
          --port "23012:30475@loadbalancer" \
          --port "23011:30476@loadbalancer" \
          --port "23013:30477@loadbalancer" \
          --port "23014:30478@loadbalancer" \
          --port "23015:30479@loadbalancer" \
          --port "23016:30480@loadbalancer" \
          --port "23017:30481@loadbalancer" \
          --port "23018:30482@loadbalancer" \
          --port "23019:30483@loadbalancer" \
          --port "23020:30484@loadbalancer" \
          --port "23021:30485@loadbalancer" \
          --port "23022:30486@loadbalancer" \
          --port "23023:30487@loadbalancer" \
          --port "23024:30020@loadbalancer" \
          --port "23025:30022@loadbalancer" \
          --port "23026:30023@loadbalancer" \
          --port "23027:30024@loadbalancer" \
          --kubeconfig-update-default \
          --wait \
          --timeout 10m
      fi
      # NOTE: --kubeconfig-update-default merges this cluster's context into
      # ~/.kube/config so `kubectl` works without setting KUBECONFIG env var.
      # We deliberately do NOT pass --kubeconfig-switch-context — switching
      # the current context while v1 cluster is still running is disruptive.
      # Set context manually with `kubectl config use-context k3d-${var.cluster_name}`.

      # Always (re)write the kubeconfig — covers both fresh-create and re-apply.
      mkdir -p "$(dirname "${var.kubeconfig_path}")"
      k3d kubeconfig get "${var.cluster_name}" > "${var.kubeconfig_path}"
      chmod 600 "${var.kubeconfig_path}"
      echo "Kubeconfig written to ${var.kubeconfig_path}"
    EOT
  }

  # ---------------------------------------------------------------------------
  # Destroy provisioner — runs on `tofu destroy` (or replace).
  # ---------------------------------------------------------------------------
  # Note: destroy provisioners CANNOT reference var.* — only self.* (a snapshot
  # of triggers taken at create time). This is a Terraform 0.12+ limitation.
  # `|| true` keeps destroy idempotent (don't fail if cluster already gone).
  # ---------------------------------------------------------------------------
  provisioner "local-exec" {
    when    = destroy
    command = "k3d cluster delete ${self.triggers.cluster_name} || true"
  }
}

# ---------------------------------------------------------------------------
# Wait for cluster API + nodes Ready before signaling complete.
# ---------------------------------------------------------------------------
# k3d's --wait only confirms k3d's view of "ready" (containers up). Downstream
# Terragrunt units that immediately deploy workloads need:
#   1. Kubernetes API responsive
#   2. All nodes in Ready condition (otherwise pods can't schedule)
# This null_resource enforces both before its `id` becomes available.
# ---------------------------------------------------------------------------
resource "null_resource" "wait_for_cluster" {
  depends_on = [null_resource.cluster]

  triggers = {
    cluster_id = null_resource.cluster.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      KUBECONFIG="${var.kubeconfig_path}"

      echo "Waiting for Kubernetes API..."
      for i in {1..30}; do
        if kubectl --kubeconfig="$KUBECONFIG" cluster-info >/dev/null 2>&1; then
          echo "API ready, waiting for nodes..."
          kubectl --kubeconfig="$KUBECONFIG" wait \
            --for=condition=Ready nodes --all --timeout=120s
          echo "All nodes ready."
          exit 0
        fi
        echo "API not ready yet... (attempt $i/30)"
        sleep 2
      done
      echo "Timeout waiting for cluster API."
      exit 1
    EOT
  }
}

# ---------------------------------------------------------------------------
# Configure auto-restart on the k3d Docker containers.
# ---------------------------------------------------------------------------
# Without this, the cluster dies on Docker daemon restart (host reboot, docker
# update, OOM-kill). With unless-stopped, containers come back automatically
# unless explicitly stopped via `k3d cluster stop`. Critical for homelab.
# ---------------------------------------------------------------------------
resource "null_resource" "auto_restart" {
  depends_on = [null_resource.wait_for_cluster]

  triggers = {
    cluster_id = null_resource.cluster.id
  }

  provisioner "local-exec" {
    command = "docker update --restart=unless-stopped $(docker ps -aq --filter 'name=k3d-${var.cluster_name}') >/dev/null 2>&1 || true"
  }
}

# ---------------------------------------------------------------------------
# Sync the rendered registries.yaml into every running node container.
# ---------------------------------------------------------------------------
# Why this exists: `--registry-config` only takes effect at `k3d cluster
# create` time. For an EXISTING cluster (created before this resource was
# added, or after the registries.yaml content changes), we must push the
# updated file into each node's /etc/rancher/k3s/registries.yaml.
#
# k3s watches that file at runtime and reloads containerd's mirror config
# when the contents change — no node restart, no cluster recreate. (Per
# the k3s docs: https://docs.k3s.io/installation/private-registry — k3s
# auto-reloads on file change.)
#
# Triggers on `registries_yaml_content` so the sync re-runs whenever the
# template is re-rendered with different content. Idempotent: writing the
# same content is a no-op.
# ---------------------------------------------------------------------------
resource "null_resource" "configure_registry_mirrors" {
  depends_on = [null_resource.wait_for_cluster, local_file.registries_yaml]

  triggers = {
    registries_yaml_content = local_file.registries_yaml.content
    cluster_id              = null_resource.cluster.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      echo "Syncing registries.yaml to all k3d cluster nodes..."
      # Filter ONLY k3s nodes (server-N, agent-N). The serverlb container
      # is the load balancer — not a k3s node, has no /etc/rancher/k3s.
      for node in $(docker ps \
            --filter "name=k3d-${var.cluster_name}" \
            --format "{{.Names}}" | grep -E "(server|agent)-[0-9]+$"); do
        echo "  → $node"
        docker cp "${local_file.registries_yaml.filename}" \
          "$node:/etc/rancher/k3s/registries.yaml"
      done

      echo "Done. k3s file watcher will reload containerd mirrors within a few seconds."
    EOT
  }
}

# ---------------------------------------------------------------------------
# Track the kubeconfig file so downstream units can read it via outputs.
# ---------------------------------------------------------------------------
# `data "local_file"` reads at plan time. The depends_on chain forces it to
# wait until both the cluster is up AND the API is verified responsive.
# ---------------------------------------------------------------------------
data "local_file" "kubeconfig" {
  filename   = var.kubeconfig_path
  depends_on = [null_resource.wait_for_cluster]
}
