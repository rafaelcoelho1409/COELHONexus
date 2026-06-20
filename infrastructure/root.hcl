# =============================================================================
# COELHO Nexus standalone — Terragrunt root configuration
# =============================================================================
# Included by every leaf:
#     include "root" { path = find_in_parent_folders("root.hcl") }
#
# Differences from COELHO Cloud's root.hcl:
#   - LOCAL state backend (vs. MinIO S3) — no chicken-and-egg, no host-native deps
#   - NO SOPS — demo credentials live plaintext in env.hcl
#   - Same OpenTofu pin, same path-based env-name extraction
#
# To migrate this cluster to production-grade secret management later:
#   1. Install sops + age (`pacman -S sops age` / `brew install sops age`)
#   2. Move env.hcl `demo` map into `secrets/coelhonexus.enc.yaml`
#   3. Encrypt: `sops -e -i secrets/coelhonexus.enc.yaml`
#   4. Uncomment the `local.secrets` block below
#   5. Each leaf swaps `include.root.locals.env.demo.<x>` → `include.root.locals.secrets.<x>`
# =============================================================================

terraform_binary = "tofu"

# -----------------------------------------------------------------------------
# Locals — computed at parse time, available below.
# -----------------------------------------------------------------------------
locals {
  # Env name extracted from the leaf's relative path:
  #   live/coelhonexus/00-bootstrap/k3d → env_name = "coelhonexus"
  env_name = split("/", path_relative_to_include())[0]

  # env.hcl lives one directory up from the layer.
  env_hcl = find_in_parent_folders("env.hcl")
  env     = read_terragrunt_config(local.env_hcl).inputs

  # ---------------------------------------------------------------------------
  # SOPS migration hook (commented out — demo path uses env.hcl plaintext).
  # ---------------------------------------------------------------------------
  # secrets_file = "${get_repo_root()}/secrets/${local.env_name}.enc.yaml"
  # secrets      = yamldecode(sops_decrypt_file(local.secrets_file))
}

# -----------------------------------------------------------------------------
# Remote state — LOCAL file backend.
# -----------------------------------------------------------------------------
# State lives under infrastructure/.tfstate/<leaf path>/terraform.tfstate.
# Gitignored. No external dependencies, no chicken-and-egg.
#
# Migration to MinIO S3 state (production): swap `backend = "local"` for
# `backend = "s3"` with config pointing at the in-cluster MinIO. Requires
# bootstrapping MinIO BEFORE first apply (chicken-and-egg) — solved in
# COELHO Cloud by running a host-native MinIO. The standalone path avoids
# this complexity entirely.
# -----------------------------------------------------------------------------
remote_state {
  backend = "local"

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    path = "${get_repo_root()}/infrastructure/.tfstate/${path_relative_to_include()}/terraform.tfstate"
  }
}

# -----------------------------------------------------------------------------
# Common inputs — merged into every leaf's inputs map.
# -----------------------------------------------------------------------------
# Each leaf inherits env.hcl values via local.env. Leaf can override any key
# by setting it in its own `inputs = {...}` block.
# -----------------------------------------------------------------------------
inputs = merge(
  local.env,
  {
    # Truly-global-to-all-envs values go here. Empty for now (this repo has
    # only one env: coelhonexus).
  }
)
