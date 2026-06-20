# Infrastructure Port — COELHO Cloud → COELHO Nexus (standalone)

**Date:** 2026-06-19
**Goal:** Make COELHO Nexus pluggable — runs on COELHO Cloud (existing) or on its own `coelhonexus` k3d cluster (new), with `apps/` and `k8s/` untouched.
**Substrate contract:** identical in-cluster DNS names + identical secret-key names + image registry contract, regardless of which cluster is plugged in.

---

## 0. Hard constraints (locked)

- **`apps/` untouched** — Python code unchanged.
- **`k8s/` untouched** — Helm chart + ArgoCD Application unchanged.
- **Modules copied verbatim** from `~/COELHOCloud/infrastructure/modules/<X>/` — no `.tf` file edits.
- **Generic credentials** seeded by `.env.example` (open-source demo-friendly).
- **No Tailscale.** k3d cluster is local-only; tailnet exposure is irrelevant for distributed users.
- **Pluggable via kubeconfig context** — `kubectl config use-context k3d-coelho-cloud` vs `kubectl config use-context k3d-coelhonexus`. No app flag, no Helm `--set` from the app side.

---

## 1. COELHO Cloud audit — what we found

`~/COELHOCloud/infrastructure/` is a Terragrunt + OpenTofu layout with 24 modules and a 6-layer live tree (`00-bootstrap`, `10-platform`, `20-data`, `30-observability`, `40-apps`, `80-automation`).

### 1.1 Layout pattern (per leaf unit)

Every leaf unit is a `terragrunt.hcl` that:
- `include "root"` pulls `root.hcl` + `env.hcl` (shared inputs: `env_name`, `cluster_name`, `tailscale_domain`, `data_path`).
- `terraform.source = "${get_repo_root()}/infrastructure/modules/<X>"` points at the module.
- `dependency "<other>"` blocks declare upstream modules (k3d, tailscale-operator, minio, monitoring-crds, etc.).
- `inputs = { ... }` overrides module defaults (admin password, hostnames, resource limits).
- Secrets pulled via `include.root.locals.secrets.<x>.password` (SOPS-encrypted).

### 1.2 Module structure (per module under `modules/<X>/`)

```
modules/postgresql/
  main.tf
  variables.tf
  outputs.tf
  versions.tf
  helm/values.yaml.tpl        # rendered into the Helm release values
  k8s/*.yaml.tpl              # raw K8s manifests (Tailscale services, backup CronJobs, etc.)
```

### 1.3 Modules COELHO Nexus does NOT use (drop)

`airflow`, `gitlab`, `homepage`, `mlflow`, `openwebui`, `pgadmin`, `rancher`, `redisinsight`, `tailscale-operator`, `vaultwarden`, `kube-state-metrics`, `node-exporter`.

Also confirmed **not in COELHO Cloud at all**: `tor`, `warp`, `argocd-image-updater` (image-updater is bundled inside the `argocd` module via `helm/image-updater-values.yaml.tpl` — already covered).

---

## 2. Modules to port (17)

| # | Module | Layer | Used by | Upstream deps (excl. k3d) |
|---|---|---|---|---|
| 1 | `k3d` | 00-bootstrap | everything | — |
| 2 | `monitoring-crds` | 10-platform | postgres, redis, langfuse (ServiceMonitor) | k3d |
| 3 | `argocd` | 10-platform | apps deploy (includes image-updater) | k3d |
| 4 | `minio` | 20-data | postgres backups, langfuse, app storage | k3d |
| 5 | `postgresql` | 20-data | langfuse, app conversation history, RR stores | minio, monitoring-crds |
| 6 | `redis` | 20-data | Celery, rotator, RR runtime | monitoring-crds |
| 7 | `qdrant` | 20-data | YCS vector store, RR papers | — |
| 8 | `neo4j` | 20-data | YCS graph, RR graph | — |
| 9 | `elasticsearch` | 20-data | YCS metadata + transcripts | — |
| 10 | `alloy` | 30-observability | OTLP collector | k3d |
| 11 | `loki` | 30-observability | logs (Loki↔Tempo correlation) | minio |
| 12 | `tempo` | 30-observability | traces | minio |
| 13 | `mimir` | 30-observability | metrics | minio |
| 14 | `grafana` | 30-observability | dashboards | loki, tempo, mimir |
| 15 | `langfuse` | 40-apps | LLM observability | postgresql, redis, minio |
| 16 | `searxng` | 50-edge | DD metasearch | — |
| 17 | `playwright` | 50-edge | YCS transcript scraping | — |

**Note: `coelhonexus` (the ArgoCD Application for the apps themselves) is the final "leaf" — lives in `99-apps/coelhonexus/` and references `k8s/helm` via the Application's `path` field.**

---

## 3. Folder structure (target)

```
COELHONexus/
  apps/                              ← UNTOUCHED
  k8s/                               ← UNTOUCHED
  observability/                     ← existing
  infrastructure/                    ← NEW
    README.md                        ← clone-and-run guide
    env.hcl                          ← inputs: env_name=coelhonexus, cluster_name=coelhonexus, data_path
    root.hcl                         ← root terragrunt: backend (local), providers, secrets loader (.env-based)
    live/coelhonexus/
      00-bootstrap/k3d/terragrunt.hcl
      10-platform/
        monitoring-crds/terragrunt.hcl
        argocd/terragrunt.hcl
      20-data/
        minio/terragrunt.hcl
        postgresql/terragrunt.hcl
        redis/terragrunt.hcl
        qdrant/terragrunt.hcl
        neo4j/terragrunt.hcl
        elasticsearch/terragrunt.hcl
      30-observability/
        alloy/terragrunt.hcl
        loki/terragrunt.hcl
        tempo/terragrunt.hcl
        mimir/terragrunt.hcl
        grafana/terragrunt.hcl
      40-apps/
        langfuse/terragrunt.hcl
      50-edge/
        searxng/terragrunt.hcl
        playwright/terragrunt.hcl
      99-apps/
        coelhonexus/terragrunt.hcl   ← ArgoCD Application; overrides registry via Helm parameters
    modules/                         ← copied 1:1 from ~/COELHOCloud/infrastructure/modules/
      k3d/
      monitoring-crds/
      argocd/
      minio/
      postgresql/
      redis/
      qdrant/
      neo4j/
      elasticsearch/
      alloy/
      loki/
      tempo/
      mimir/
      grafana/
      langfuse/
      searxng/
      playwright/
  scripts/
    standalone-up.sh                 ← orchestrator: terragrunt + skaffold + smoke checks
    standalone-down.sh               ← teardown
    sync-from-coelhocloud.sh         ← future-drift sync
  .env.example                       ← generic credentials + BYOK template
```

---

## 4. Phased install order (incremental — install + validate per module)

Per the agreed pattern: **one module at a time, smoke test, then move on.** End-user `terragrunt run-all apply` is the validated end-state.

### Phase 1 — Foundation (3 modules, ~10 min)

| Step | Module | Smoke test |
|---|---|---|
| P1.1 | `k3d` | `kubectl get nodes` shows control-plane + agents Ready |
| P1.2 | `monitoring-crds` | `kubectl get crd servicemonitors.monitoring.coreos.com` |
| P1.3 | `argocd` | `kubectl get pods -n argocd` all Running; `argocd-server` reachable |

### Phase 2 — Storage (1 module, ~3 min)

| Step | Module | Smoke test |
|---|---|---|
| P2.1 | `minio` | `kubectl exec -n minio minio-0 -- mc admin info local` returns 200 |

### Phase 3 — Data layer (5 modules, ~15 min)

| Step | Module | Smoke test |
|---|---|---|
| P3.1 | `postgresql` | `kubectl exec -n postgresql postgresql-0 -- psql -U postgres -c "SELECT 1"` |
| P3.2 | `redis` | `kubectl exec -n redis redis-master-0 -- redis-cli ping` returns PONG |
| P3.3 | `qdrant` | `curl http://qdrant.qdrant.svc.cluster.local:6333/healthz` |
| P3.4 | `neo4j` | `kubectl exec -n neo4j neo4j-0 -- cypher-shell "RETURN 1"` |
| P3.5 | `elasticsearch` | `curl -k -u coelhonexus:coelhonexus-demo-password https://elasticsearch-es-http.elasticsearch.svc.cluster.local:9200/_cluster/health` |

### Phase 4 — Observability (5 modules, ~12 min)

| Step | Module | Smoke test |
|---|---|---|
| P4.1 | `alloy` | `curl http://alloy.alloy.svc.cluster.local:12345/-/healthy` |
| P4.2 | `loki` | `curl http://loki.loki.svc.cluster.local:3100/ready` |
| P4.3 | `tempo` | `curl http://tempo.tempo.svc.cluster.local:3200/ready` |
| P4.4 | `mimir` | `curl http://mimir.mimir.svc.cluster.local:9009/ready` |
| P4.5 | `grafana` | `curl http://grafana.grafana.svc.cluster.local:3000/api/health` |

### Phase 5 — LLM observability (1 module, ~3 min)

| Step | Module | Smoke test |
|---|---|---|
| P5.1 | `langfuse` | `curl http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/health` |

### Phase 6 — Edge (2 modules, ~5 min)

| Step | Module | Smoke test |
|---|---|---|
| P6.1 | `searxng` | `curl http://searxng.searxng.svc.cluster.local:8080/healthz` |
| P6.2 | `playwright` | `curl http://playwright-headless.playwright.svc.cluster.local:9224/json/version` |

### Phase 7 — Apps (1 leaf, ~5 min)

| Step | Leaf | Smoke test |
|---|---|---|
| P7.1 | `99-apps/coelhonexus` | ArgoCD Application reaches `Synced + Healthy`; `kubectl get pods -n coelhonexus` all Ready |

**Total: 17 modules + 1 app leaf, ~55 min including smoke tests.**

---

## 5. The `.env.example` schema

```bash
# =============================================================================
# CLUSTER + REGISTRY
# =============================================================================
# Cluster name — drives k3d cluster creation + kubectl context name.
CLUSTER_NAME=coelhonexus
# Image registry — pluggable.
#   coelhonexus k3d cluster: coelhonexus-registry:5000
#   COELHO Cloud:            coelho-cloud-registry:5000
IMAGE_REGISTRY=coelhonexus-registry:5000

# =============================================================================
# CLUSTER DEFAULTS — generic credentials for the standalone coelhonexus
# k3d cluster. Safe demo values; the cluster is reachable only from your
# own machine. Override only if you want non-defaults.
# =============================================================================
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
REDIS_PASSWORD=redis
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j-demo-password
ELASTICSEARCH_USERNAME=coelhonexus
ELASTICSEARCH_PASSWORD=coelhonexus-demo-password
QDRANT_API_KEY=
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
LANGFUSE_HOST=http://langfuse-web.langfuse.svc.cluster.local:3000
LANGFUSE_PUBLIC_KEY=lf_pk_demo000000000000000000000000
LANGFUSE_SECRET_KEY=lf_sk_demo000000000000000000000000000000000000000000000000000000
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin

# =============================================================================
# OPTIONAL BYOK — bring your own keys for LLM providers + paid search APIs.
# The platform boots without these; LLM-dependent features (DD, YCS, RR)
# need at least one provider key to actually do work.
# =============================================================================
NVIDIA_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=
MISTRAL_API_KEY=
GOOGLE_API_KEY=
DEEPSEEK_API_KEY=
SAMBANOVA_API_KEY=
EXA_API_KEY=
TAVILY_API_KEY=
JINA_API_KEY=
LINKUP_API_KEY=
```

---

## 6. Key adaptations vs COELHO Cloud verbatim

These are the **only** differences between COELHO Cloud's `live/homelab/` and our `live/coelhonexus/`. The modules themselves are copied byte-identical.

### 6.1 Tailscale removal

Every COELHO Cloud leaf has `dependency "tailscale_operator"` + `enable_tailscale_exposure = true` inputs.

**Adaptation:** in our `live/coelhonexus/*/terragrunt.hcl`:
- Remove the `dependency "tailscale_operator"` block entirely
- Set `enable_tailscale_exposure = false` in the `inputs` map
- Drop the `tailscale_hostname` + `tailscale_domain` inputs

Modules support the disabled path via their existing `variables.tf` — no module-code changes.

### 6.2 SOPS → `.env`

COELHO Cloud reads secrets via `include.root.locals.secrets.<x>.password` (SOPS).

**Adaptation:** our `root.hcl` reads from `.env` instead:
```hcl
locals {
  env_vars = {
    for line in split("\n", file("${get_repo_root()}/.env")) :
    split("=", line)[0] => split("=", line)[1]
    if line != "" && !startswith(line, "#") && length(split("=", line)) > 1
  }
}
```

Then each leaf uses `local.env_vars.POSTGRES_PASSWORD` instead of `include.root.locals.secrets.postgres.password`.

### 6.3 `env.hcl` swap

```hcl
inputs = {
  env_name         = "coelhonexus"
  cluster_name     = "coelhonexus"          # was "coelho-cloud"
  data_path        = "${get_repo_root()}/.data"   # was /home/rafaelcoelho/COELHOCloud/data
  enable_tailscale = false                  # NEW flag — propagates to all leaves
}
```

### 6.4 Registry variable

Defaults in `k8s/helm/values.yaml` remain `coelho-cloud-registry:5000`. The standalone path overrides via the ArgoCD Application defined in `99-apps/coelhonexus/terragrunt.hcl`:

```hcl
helm = {
  parameters = [
    { name = "registry.url",     value = local.env_vars.IMAGE_REGISTRY },
    { name = "fastapi.image",    value = "${local.env_vars.IMAGE_REGISTRY}/coelhonexus-fastapi:latest" },
    { name = "fasthtml.image",   value = "${local.env_vars.IMAGE_REGISTRY}/coelhonexus-fasthtml:latest" },
    { name = "fastmcp.image",    value = "${local.env_vars.IMAGE_REGISTRY}/coelhonexus-fastmcp:latest" },
  ]
}
```

For COELHO Cloud users, the existing `k8s/argocd/application.yaml` keeps working — no change.

---

## 7. `scripts/standalone-up.sh` orchestration

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Ensure .env exists
[ -f .env ] || cp .env.example .env

# 2. Source it
set -a; source .env; set +a

# 3. Phased apply with smoke tests
PHASES=(
  "00-bootstrap/k3d"
  "10-platform/monitoring-crds"
  "10-platform/argocd"
  "20-data/minio"
  "20-data/postgresql"
  "20-data/redis"
  "20-data/qdrant"
  "20-data/neo4j"
  "20-data/elasticsearch"
  "30-observability/alloy"
  "30-observability/loki"
  "30-observability/tempo"
  "30-observability/mimir"
  "30-observability/grafana"
  "40-apps/langfuse"
  "50-edge/searxng"
  "50-edge/playwright"
  "99-apps/coelhonexus"
)
for phase in "${PHASES[@]}"; do
  echo "[standalone-up] applying ${phase}..."
  (cd "infrastructure/live/coelhonexus/${phase}" && terragrunt apply -auto-approve)
  echo "[standalone-up] ${phase} applied; running smoke test..."
  bash "infrastructure/live/coelhonexus/${phase}/smoke.sh" || {
    echo "[standalone-up] smoke test failed for ${phase}; aborting"; exit 1
  }
done
echo "[standalone-up] all 17 modules + apps applied; cluster ready"
echo "[standalone-up] kubectl config use-context k3d-${CLUSTER_NAME}"
```

Each leaf gets its own `smoke.sh` with the corresponding smoke test from §4.

---

## 8. Risks + rollback

| Risk | Mitigation |
|---|---|
| Module `.tf` file references SOPS path → fails on apply | `root.hcl` provides `.env` shim; SOPS path never resolved |
| Tailscale operator removal breaks module → fails on apply | Modules support `enable_tailscale_exposure=false` via existing variables; verify per module on first apply |
| Disk inflated by `.terragrunt-cache` | Add `.gitignore` entry; clean via `find . -name .terragrunt-cache -type d -exec rm -rf {} +` |
| COELHO Cloud running while we port → resource contention | `k3d cluster stop coelho-cloud` (state preserved); restart anytime to roll back |
| Module updates upstream in COELHO Cloud → drift | `scripts/sync-from-coelhocloud.sh` diffs `infrastructure/modules/<X>/` vs `~/COELHOCloud/infrastructure/modules/<X>/` and copies newer files (manual review before commit) |
| Standalone-up.sh stops mid-phase → partial cluster | `k3d cluster delete coelhonexus` rebuilds from scratch in ~30 s |

---

## 9. Execution plan (next steps)

1. Stop COELHO Cloud (`k3d cluster stop coelho-cloud`) to free ~30 GB RAM + 40% CPU.
2. Create `.env.example` + scaffolding (`infrastructure/{env.hcl, root.hcl, live/coelhonexus/, modules/}` + `scripts/standalone-up.sh`).
3. **Phase 1, Module 1: `k3d`.** Copy module, write live config, apply, smoke test.
4. Wait for user approval → continue Phase 1 (monitoring-crds, argocd).
5. Iterate through Phases 2-7, one module at a time, with explicit user checkpoint after each phase.
6. Once Phase 7 passes, run `scripts/standalone-up.sh` end-to-end as the final reproducibility check.
7. Commit the entire `infrastructure/` directory + `.env.example` + scripts in one logical batch.

---

## 10. Open questions to confirm before scaffolding

1. **Tailscale removal blanket-applied.** Confirm: every leaf gets `enable_tailscale_exposure = false` and drops the `dependency "tailscale_operator"` block. (Per §6.1.)
2. **SOPS bypass via `.env`.** Confirm the `.env` shim in `root.hcl` (§6.2) is acceptable. Alternative: keep a SOPS-encrypted file with the same demo values (loses simplicity, gains realistic-prod parity).
3. **Grafana credentials.** `admin/admin` for demo (per `.env.example` §5), or different?
4. **Skip kube-state-metrics + node-exporter?** Our Grafana dashboards don't reference them. Confirm we don't need cluster-level metrics for any operational view.
5. **`coelhonexus` ArgoCD Application repo URL.** What does it point at for the standalone case? Options:
   - Local file path (`/path/to/COELHONexus/k8s/helm`) — simplest, requires Argo to mount the repo
   - GitHub fork (`https://github.com/<user>/COELHONexus`) — clean GitOps; user must fork first
   - Same in-cluster GitLab URL — only works if standalone user also runs GitLab (nope)
   Likely answer: local file path via `repoURL: cnrm://k8s/helm`-style path or a small "argocd-vending" deploy.

Answer those and I scaffold.
