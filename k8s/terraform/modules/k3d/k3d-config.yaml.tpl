# =============================================================================
# K3D Cluster Configuration for Reco Project
# =============================================================================
# Usage:
#   k3d cluster create --config k3d-config.yaml
#
# For development, use Skaffold which handles port forwarding automatically.
# =============================================================================

apiVersion: k3d.io/v1alpha5
kind: Simple
metadata:
  name: ${cluster_name}
servers: ${servers}
agents: ${agents}
image: rancher/k3s:${k3s_version}

# Local registry for pushing images
registries:
  create:
    name: ${cluster_name}-registry
    host: "0.0.0.0"
    hostPort: "${registry_port}"
  config: |
    mirrors:
      "localhost:${registry_port}":
        endpoint:
          - "http://${cluster_name}-registry:5000"
      "${cluster_name}-registry:5000":
        endpoint:
          - "http://${cluster_name}-registry:5000"

# Port mappings for Rancher access
ports:
  - port: 7081:30081
    nodeFilters:
      - loadbalancer
  - port: 7444:30444
    nodeFilters:
      - loadbalancer

# Volume mounts for data persistence
volumes:
%{ for vol in volumes ~}
  - volume: ${vol.host_path}:${vol.container_path}
    nodeFilters:
      - ${vol.node_filter}
%{ endfor ~}

options:
  k3d:
    wait: true
    timeout: "120s"
  k3s:
    extraArgs:
      - arg: --disable=traefik
        nodeFilters:
          - server:*
  kubeconfig:
    updateDefaultKubeconfig: true
    switchCurrentContext: true
