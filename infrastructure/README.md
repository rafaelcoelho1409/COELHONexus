# `infrastructure/` — COELHO Nexus standalone Kubernetes infrastructure

Self-contained Terragrunt + OpenTofu setup that brings up a local k3d cluster with every backend COELHO Nexus needs. Decoupled from COELHO Cloud — clone the repo and run one script.

**Use this when you want to run COELHO Nexus locally without depending on the COELHO Cloud production cluster.** The `apps/` and `k8s/` folders deploy unchanged against either cluster (`k3d-coelho-cloud` or `k3d-coelhonexus` kubeconfig context).

---

## Prerequisites

| Tool | Minimum version | Install |
|---|---|---|
| `docker` | 24.0+ | https://docs.docker.com/engine/install/ |
| `k3d` | v5.8+ | `curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh \| bash` |
| `kubectl` | matches your k3s version (~v1.34) | https://kubernetes.io/docs/tasks/tools/ |
| `terragrunt` | 0.68+ | https://terragrunt.gruntwork.io/docs/getting-started/install/ |
| `tofu` | 1.10+ | https://opentofu.org/docs/intro/install/ |
| `jq` | 1.6+ | system package manager |

Verify with `./scripts/standalone-prereqs.sh` (TODO).

Disk: ~15 GB free in `~/Workbench/COELHONexus/infrastructure/.data/` (persistent volumes for all data services). RAM: ~12-16 GB depending on which features you exercise.

---

## Quick start

**Canonical install — cross-platform, safe on any hardware (~25-30 min):**

```bash
cd ~/Workbench/COELHONexus/infrastructure/live/coelhonexus
terragrunt run --all apply --non-interactive --parallelism 1
```

This is the only command you need — works identically on Linux, macOS, and Windows (PowerShell or WSL). Terragrunt walks the dependency graph from each leaf's `dependency` blocks, applies one module at a time, and auto-approves (`-auto-approve` is appended automatically by `run --all`).

**Why `--parallelism 1` is the default:** without it, Terragrunt's default parallelism lets siblings without declared deps run concurrently — on this graph that means up to **6 helm installs at once** in the data layer (minio + redis + postgresql + qdrant + neo4j + elasticsearch) plus **3 more** in the observability layer (loki + tempo + mimir). The combined memory + Docker daemon + k3s API server load can OOM resource-constrained machines or trigger image-pull timeouts. Sequential is safe everywhere.

> **Flag-name notes**: `run --all` replaced the deprecated `run-all` subcommand. Flags lost the `--terragrunt-` prefix (`--terragrunt-non-interactive` → `--non-interactive`, `--terragrunt-parallelism` → `--parallelism`). If you see `flag provided but not defined`, your Terragrunt is recent enough that you're using an old-syntax command — switch to the form above.

**Bumping parallelism (faster, if your hardware can take it):**

Once you've installed the cluster once and confirmed your hardware has headroom, you can re-apply faster with concurrent siblings:

```bash
# Moderate parallelism — ~20 min, ~2-4 GB extra peak RAM
terragrunt run --all apply --non-interactive --parallelism 3

# Full parallelism — ~15 min, can spike 4-8 GB extra peak RAM + bursty Docker
terragrunt run --all apply --non-interactive
```

**Dry-run first (optional):**

```bash
cd ~/Workbench/COELHONexus/infrastructure/live/coelhonexus
terragrunt run --all plan --non-interactive --parallelism 1
```

**Alternative — phased bash script with per-leaf smoke tests** (Linux/macOS/WSL only):

```bash
cd ~/Workbench/COELHONexus
bash scripts/standalone-up.sh
```

This applies 17 phases one at a time, running each leaf's `smoke.sh` between phases (the `run --all` path skips smoke). Same end state as the terragrunt path; prefer it on Unix when you're adding a new leaf or debugging an install regression and want to see smoke output between phases.

When it's green:

```bash
bash scripts/standalone-port-forward.sh   # opens host ports 23000-23010
```

| Service | URL | Login |
|---|---|---|
| FastAPI (main app) | http://localhost:23000 | — |
| Flower (Celery) | http://localhost:23002 | — |
| FastHTML | http://localhost:23003 | — |
| FastMCP | http://localhost:23004 | — |
| Grafana | http://localhost:23005 | `admin / admin` |
| LangFuse | http://localhost:23006 | `admin@demo.local / admin-demo-password` |
| ArgoCD | http://localhost:23007 | `admin` / `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` |
| MinIO Console | http://localhost:23008 | `minioadmin / minioadmin` |
| MinIO S3 API | http://localhost:23009 | — |
| Rancher | http://localhost:23010 | `admin / rancher-demo-bootstrap` (forces password change on first login) |

---

## Layout

```
infrastructure/
├── env.hcl                  # Single config file — demo credentials, cluster name, data path
├── root.hcl                 # Terragrunt root — local backend, no SOPS
├── README.md                # ← this file
├── modules/                 # Reusable Terraform modules (byte-identical to COELHO Cloud where possible)
│   ├── k3d/                 # k3d cluster create
│   ├── monitoring-crds/     # Prometheus Operator CRDs
│   ├── rancher/             # Rancher cluster UI
│   ├── argocd/              # ArgoCD + Image Updater
│   ├── minio/               # MinIO object storage
│   ├── postgresql/          # Bitnami Postgres
│   ├── redis/               # Bitnami Redis
│   ├── qdrant/              # Vector store (YCS + RR)
│   ├── neo4j/               # Knowledge graph (YCS + RR)
│   ├── elasticsearch/       # ECK Operator + cluster (YCS metadata)
│   ├── loki/                # LGTM logs
│   ├── tempo/               # LGTM traces
│   ├── mimir/               # LGTM metrics
│   ├── alloy/               # OTel collector → LGTM fan-out
│   ├── grafana/             # LGTM UI
│   ├── langfuse/            # LLM observability (chart-bundled ClickHouse)
│   └── playwright/          # Multi-container CDP sidecars for YCS scraping
├── live/coelhonexus/        # Environment-specific leaves
│   ├── 00-bootstrap/        # k3d cluster + registry
│   ├── 10-platform/         # monitoring-crds, rancher, argocd
│   ├── 20-data/             # minio, redis, postgresql, qdrant, neo4j, elasticsearch
│   ├── 30-observability/    # loki, tempo, mimir, alloy, grafana
│   ├── 40-apps/             # langfuse
│   └── 50-edge/             # playwright
├── .tfstate/                # Local Terragrunt state (gitignored)
└── .data/                   # Persistent volumes mounted into k3d (gitignored)
```

`scripts/`:
- `standalone-up.sh` — sequential phased bring-up (alternative to `terragrunt run --all`; Unix only)
- `standalone-port-forward.sh` — opens host ports 23000-23010 (auto-reconnecting `while true` loops; Unix only)
- `argocd-port-forward.sh` — same for the COELHO Cloud cluster
- `redis-check.sh` — generic Redis CLI sanity check
- `observability/` — LangFuse prompt publishers, dataset bootstrap, eval runners

App build + deploy is handled by **Skaffold** at the repo root (`skaffold run` / `skaffold dev`), not by a shell script — cross-platform, single tool.

---

## How it works

- **Terragrunt** orchestrates per-leaf state, dependency resolution, and `inputs = merge(env.hcl, ...)`. Backend = local (`infrastructure/.tfstate/`); no MinIO chicken-and-egg.
- **OpenTofu** (`terraform_binary = "tofu"` in `root.hcl`) is the engine — Terraform-compatible, FOSS license.
- **k3d** wraps k3s in Docker. The cluster name is `coelhonexus`; kubeconfig context is `k3d-coelhonexus`.
- Every leaf has a `smoke.sh` that runs after `terragrunt apply` succeeds. The script validates the leaf is actually usable (e.g., MinIO accepts S3 calls, not just "the pod is running"). Failure aborts the chain.
- **Pluggable**: COELHO Nexus' `apps/` and `k8s/` work against both this cluster AND COELHO Cloud's homelab — switch with `kubectl config use-context k3d-coelhonexus | k3d-coelho-cloud`.

---

## Common operations

### Apply one leaf

```bash
cd infrastructure/live/coelhonexus/<layer>/<module> && terragrunt apply -auto-approve
```

### Apply a subset (sequential script path only)

```bash
PHASES="20-data/minio 20-data/redis" bash scripts/standalone-up.sh
```

### Run smoke tests after a `run --all apply` (which skips them)

```bash
for f in infrastructure/live/coelhonexus/*/*/smoke.sh; do bash "$f" || break; done
```

### Deploy the COELHO Nexus apps (`fastapi`, `fasthtml`, `fastmcp`)

After the infrastructure is up, build and deploy with **Skaffold** — one cross-platform Go binary (works on Linux, macOS, and Windows identically; install at https://skaffold.dev/docs/install/).

```bash
# One-shot build + push + deploy (CI-style)
skaffold run

# Interactive dev mode (auto-rebuild on edit, hot-reload via file sync)
skaffold dev
```

A single `skaffold.yaml` at the repo root works for both clusters:
- **Base config** targets the COELHO Cloud k3d cluster (`localhost:5001` registry, default `values.yaml`).
- **`coelhonexus` profile** auto-activates when your current kubectl context is `k3d-coelhonexus` — flips the registry to `localhost:5000`, layers `values-coelhonexus.yaml`, and re-maps the image template variables.

So plain `skaffold run` after `kubectl config use-context k3d-coelhonexus` Just Works on the standalone cluster. Force the profile explicitly with `skaffold run -p coelhonexus` if you want to be paranoid.

**Why not ArgoCD on the standalone cluster?** ArgoCD GitOps requires a Git source the cluster can reach. `k8s/argocd/application.yaml` points at an in-cluster GitLab service that exists only on COELHO Cloud (GitLab was intentionally dropped from the standalone infrastructure port — it would add ~3 GB RAM for a single-user demo). On standalone, Skaffold is the deploy path; ArgoCD GitOps stays as the COELHO Cloud demo. See `docs/K8S-DUAL-CLUSTER-FLEX-2026-06-19.md` for the full rationale.

### Destroy + reset

```bash
pkill -f "coelhonexus-pf" 2>/dev/null || true
k3d cluster delete coelhonexus
rm -rf infrastructure/.tfstate/    # wipe Terragrunt state
rm -rf infrastructure/.data/        # wipe persistent volumes (optional)
```

For a graph-walking destroy (slower, but uses Terragrunt's dep order in reverse):
```bash
cd infrastructure/live/coelhonexus
terragrunt run --all destroy --non-interactive
```
Note: `run --all destroy` can hang on Helm finalizers (notably Rancher/Fleet). When in doubt prefer `k3d cluster delete coelhonexus` + state wipe — much faster and guaranteed clean.

### Build + deploy apps

```bash
skaffold run               # one-shot build + push + helm install
skaffold dev               # interactive watch mode w/ hot-reload
```

Skaffold's `coelhonexus` profile auto-activates via kubeContext — no extra flags needed when you've selected `k3d-coelhonexus`. See the "Deploy the COELHO Nexus apps" section above for details.

### Inspect failing pod

```bash
export KUBECONFIG=$PWD/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig
kubectl get pods -A | grep -vE "Running|Completed"
kubectl describe pod -n <ns> <pod>
kubectl logs -n <ns> <pod> --tail=100
```

---

## Configuration

All demo credentials live in `infrastructure/env.hcl` under the `demo` map. Edit that file and re-apply if you need different passwords. They're plain-text on purpose — this is a local demo, not a production cluster. **Do not commit real secrets** to this file.

For real BYOK secrets (LLM provider API keys), use the in-app Settings UI at `http://localhost:23003/settings` (Fernet-encrypted, stored in MinIO).

---

## Known quirks + gotchas

- **Rancher takes 8-12 min** to fully bootstrap on first install. The `tune_rancher_webhook` and `starve_turtles_capi` null_resources wait up to 8 min for their target Deployments to appear. Don't interrupt the apply.
- **Rancher `rancher_features=""`** at install time is intentional — setting `features=X=false` at chart-install deadlocks fresh installs. See [[project_rancher_fresh_install_bug_2026_06_19]] memory + `infrastructure/live/coelhonexus/10-platform/rancher/terragrunt.hcl` comment.
- **Qdrant API key** is deterministic and comes from `infrastructure/env.hcl` `demo.qdrant_api_key`. The module writes it to K8s secret `qdrant-api-key`, and Qdrant reads that same secret at runtime.
- **Playwright headed/headless images** are ~870 MB + ~1.5 GB respectively. First pull on each node takes 30-60 s. The smoke test has a 600 s wait budget per pod.
- **MinIO + Postgres data persists** in `infrastructure/.data/` across cluster recreations — `k3d cluster delete coelhonexus` keeps your PVCs unless you also `rm -rf infrastructure/.data/`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `terragrunt apply` hangs in `helm install` for >10 min | Image pull blocked, OOMKill loop, or webhook stuck | `kubectl get events -A --sort-by='.lastTimestamp' \| tail -20` + `kubectl get pods -A \| grep -v Running` |
| Rancher pod restarts every 10 min with startup probe failures | Either `rancher_features=X=false` set at install OR k3d image cache evicted | Check `terragrunt.hcl` for `rancher_features = ""`; re-pull image with `docker exec k3d-coelhonexus-agent-0 crictl pull rancher/rancher:v2.14.1` |
| Smoke test times out on first-pull | Image too big for default `--timeout=` | Bump the smoke's `kubectl wait --timeout=` (Playwright is already at 600s; others may need similar bumps) |
| `kubectl` errors with "context k3d-coelhonexus not found" | Cluster destroyed but `~/.kube/config` not refreshed | `k3d kubeconfig merge coelhonexus --kubeconfig-merge-default` |

---

## See also

- COELHO Cloud production infrastructure: `~/COELHOCloud/infrastructure/` (same modules, different leaves; uses Tailscale + SOPS)
- `docs/INFRASTRUCTURE-PORT-2026-06-19.md` — the port plan + module inventory + 7-phase order
- `docs/OBSERVABILITY-LANGFUSE-OTEL-SOTA-2026-06-18.md` — the LGTM-stack-consuming workload that motivated this infrastructure port
