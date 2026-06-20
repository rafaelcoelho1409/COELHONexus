# =============================================================================
# Leaf — k3d cluster (coelhonexus standalone, bootstrap layer)
# =============================================================================
# Creates the local k3d cluster everything else depends on.
#
# Apply:
#   cd infrastructure/live/coelhonexus/00-bootstrap/k3d
#   terragrunt apply
# OR (preferred — orchestrates every phase):
#   ./scripts/standalone-up.sh
#
# State after apply:
#   - Docker containers: k3d-coelhonexus-{server-0, agent-0..N, serverlb, coelhonexus-registry}
#   - Host registry:     localhost:5000 (for `docker push localhost:5000/...`)
#   - In-cluster registry: coelhonexus-registry:5000 (image references in k8s manifests)
#   - kubeconfig:        infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig
#   - Context:           k3d-coelhonexus  (auto-merged into ~/.kube/config)
# =============================================================================

include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${get_repo_root()}/infrastructure/modules/k3d"
}

inputs = {
  # K3s version — pinned to a stable -1 minor. Bumping forces cluster
  # replacement (module triggers map includes this).
  k3s_version = "v1.34.7-k3s1"

  # Single server + 2 agents — lighter than COELHO Cloud's 1+4 default, fits
  # the OSS clone-and-run posture. Tunable per-host.
  servers = 1
  agents  = 2

  # Host port for `docker push localhost:5000/...`. The k3d registry container
  # internally listens on 5000; the host port is independent. Standardizing
  # on 5000 keeps it natural for `IMAGE_REGISTRY=coelhonexus-registry:5000`.
  #
  # macOS users with AirPlay Receiver enabled: either disable it (System
  # Settings → General → AirDrop & Handoff → AirPlay Receiver) or change
  # this to 5001 and update IMAGE_REGISTRY accordingly.
  registry_port = 5000

  # Kubeconfig lives next to this leaf so it's easy to find.
  # Downstream leaves reference it via `dependency.k3d.outputs.kubeconfig_path`.
  kubeconfig_path = "${get_repo_root()}/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig"
}
