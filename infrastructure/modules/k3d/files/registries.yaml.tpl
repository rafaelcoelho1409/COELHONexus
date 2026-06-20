# =============================================================================
# k3s/containerd registry mirrors — k3d cluster registry config
# =============================================================================
# Mounted by k3d into every node container at /etc/rancher/k3s/registries.yaml.
# k3s watches the file at runtime and reloads containerd's mirror config when
# the contents change — no node restart required.
#
# Why we need both `localhost:${registry_port}` AND `${cluster_name}-registry`:
#   - `localhost:${registry_port}` is what HOST-side tools (Skaffold,
#     `docker push`) reach via the k3d host port mapping.
#   - `${cluster_name}-registry:5000` is what's resolvable from INSIDE the
#     cluster via the Docker bridge DNS.
# Without the localhost mirror, kubelet can't pull `localhost:.../foo:bar`
# image references — which is exactly what Skaffold needs because Skaffold's
# sync feature requires the artifact image string to LITERALLY EQUAL the
# deployed pod's image string (no normalization).
#
# Variables interpolated:
#   ${cluster_name}    e.g. "coelho-cloud"
#   ${registry_port}   e.g. 5001 (host port mapping)
# =============================================================================
mirrors:
  "localhost:${registry_port}":
    endpoint:
      - "http://${cluster_name}-registry:5000"
  "${cluster_name}-registry:5000":
    endpoint:
      - "http://${cluster_name}-registry:5000"
  "${cluster_name}-registry:${registry_port}":
    endpoint:
      - "http://${cluster_name}-registry:5000"
configs:
  "localhost:${registry_port}":
    tls:
      insecure_skip_verify: true
  "${cluster_name}-registry:5000":
    tls:
      insecure_skip_verify: true
auths: {}
