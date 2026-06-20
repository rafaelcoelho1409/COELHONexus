# `k8s/` dual-cluster flexibilization — analysis & options

**Date:** 2026-06-19
**Scope:** Make `k8s/helm/` + `k8s/argocd/` work against BOTH the COELHO Cloud production cluster AND the COELHONexus standalone k3d cluster without touching `apps/`.
**Status:** Analysis complete. Implementation deferred — category 3 below requires a decision before any edits.

---

## Context

The user has decided to keep:
- `apps/` exactly where it is (already cluster-agnostic; same code targets either cluster)
- `k8s/helm/` and `k8s/argocd/` exactly where they are (NOT moved into `infrastructure/`)

The remaining question: which variables inside `k8s/` need to FLEX between clusters, and how?

Pre-analysis cleanup (done 2026-06-19) — removed dead refs to dropped tools so this doc starts from accurate state:
- WARP proxy (0 refs in `apps/`)
- Tor proxy (0 refs in `apps/`)
- SearXNG metasearch (0 refs in `apps/`)
- Exa, Jina, Tavily, Linkup search APIs (0 refs in `apps/`)
- 2 dedicated research docs (`YOUTUBE-TRANSCRIPTION-RESEARCH.md`, `IP-ROTATION-TOOLS-RESEARCH.md`) deleted

---

## The ~95% that's already cluster-agnostic — DO NOT touch

These values are IDENTICAL between COELHO Cloud and COELHONexus because both clusters use the same module patterns (`<svc>.<svc>.svc.cluster.local` namespace-per-service).

| Category | Examples |
|---|---|
| **In-cluster service FQDNs** | `minio.host`, `redis.host`, `postgresql.host`, `neo4j.uri`, `qdrant.url`, `elasticsearch.host`, `playwright.cdp_*`, `otel.alloy_endpoint`, `otel.langfuse_otlp_endpoint` |
| **Pipeline tunables** | `dd.useClassical*` flags, `dd.llmGlobalConcurrency`, `dd.studySem`, `dd.paretoBanditDisable`, `dd.dynamicCatalog`, `dd.refineUseGmm`, etc. |
| **Probes, resources, replicas** | All probe configs, resource requests/limits, replica counts |
| **Secret references** | `secretName: coelhonexus-secret` (same NAME, different VALUES populated per cluster outside the chart) |
| **`secretMappings` envName↔key** | 20+ mappings stay identical; only the underlying secret values differ |
| **Storage class** | `local-path` (k3d default on both clusters) |
| **Environment, namespaces, NodePorts** | All static |

---

## The ~5% that needs to flex — three categories

### Category 1 — Container registry hostname (4 vars in `values.yaml`)

| Line | Variable | COELHO Cloud | COELHONexus |
|---|---|---|---|
| `~344` | `registry.url` | `coelho-cloud-registry:5000` | `coelhonexus-registry:5000` |
| `~356` | `fastapi.image` | `coelho-cloud-registry:5000/coelhonexus-fastapi:latest` | `coelhonexus-registry:5000/coelhonexus-fastapi:latest` |
| `~439` | `fasthtml.image` | same prefix | same prefix |
| `~474` | `fastmcp.image` | same prefix | same prefix |

These 4 are **mechanically linked** — change `registry.url` and the 3 image refs MUST follow.

### Category 2 — Container registry hostname in image-updater (2 vars in `image-updater.yaml`)

| Line | Variable |
|---|---|
| `22` | `imageName: coelho-cloud-registry:5000/coelhonexus-fastapi:latest` |
| `29` | `imageName: coelho-cloud-registry:5000/coelhonexus-fasthtml:latest` |

Same registry-prefix flex as category 1, in a separate file.

### Category 3 — GitOps source repository (1 var in `application.yaml`) ⚠️ BLOCKER

`application.yaml` line 34:
```yaml
repoURL: http://gitlab-webservice-default.gitlab.svc.cluster.local:8181/root/COELHONexus.git
```

- ✅ **COELHO Cloud**: GitLab is installed (`gitlab` module). The in-cluster URL works; `argocd-gitlab-repo-creds` Secret holds the PAT.
- ❌ **COELHONexus**: GitLab is **NOT** installed (intentionally dropped from the standalone infrastructure port). This URL points to nothing. ArgoCD can't sync from it.

**This is the only real architectural decision** — categories 1 and 2 are mechanical once 3 is decided.

---

## Three options for category 3 — DECIDED: Option B (Skaffold)

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A. External Git (GitHub mirror)** | Mirror the repo to GitHub; both clusters point at `https://github.com/<user>/COELHONexus.git`. One `application.yaml` for both. | One artifact for both clusters. Standard GitOps pattern. | Requires public/private repo on GitHub. Adds external dependency. PATs need management for private repos. |
| **B. Skaffold for COELHONexus (DECIDED 2026-06-19)** | Skip ArgoCD GitOps entirely on the standalone. Use `skaffold run` / `skaffold dev` (Skaffold is a single Go binary that works identically on Linux, macOS, and Windows). A `coelhonexus` profile in `skaffold.yaml` auto-activates via `kubeContext: k3d-coelhonexus`. Keep ArgoCD GitOps only for COELHO Cloud. | Zero extra setup on standalone. **Cross-platform** — no bash dependency. Standard Skaffold idiom for dual-environment dev. Honest separation: standalone = "see the stack work locally", production = "see GitOps demo". | Two different deploy models to document. Standalone doesn't demonstrate GitOps. |
| **C. Self-hosted lightweight Git on COELHONexus** | Add a Gitea module to `infrastructure/`. | Symmetric to COELHO Cloud (one deploy model). | User rejected Gitea earlier ("requires manual git push commands"). Adds infrastructure surface. |

---

## Implementation shape — Option B (SHIPPED 2026-06-19)

```
k8s/helm/
├── Chart.yaml
├── values.yaml                    # shared 95% + COELHO Cloud defaults (registry, images)
└── values-coelhonexus.yaml        # 4-line registry/image override (overlay for standalone)

k8s/argocd/                        # ANNOTATED as "COELHO Cloud only" via header comments
├── application.yaml               # repoURL points at in-cluster GitLab (doesn't exist on standalone)
└── image-updater.yaml             # pairs with application.yaml

skaffold.yaml                      # single file, two profiles
                                   #   - base config:        COELHO Cloud (localhost:5001)
                                   #   - `coelhonexus` profile: standalone (localhost:5000)
                                   #     auto-activated by kubeContext = k3d-coelhonexus
```

**End-to-end workflow on the standalone cluster:**

```bash
# Once: select the cluster
kubectl config use-context k3d-coelhonexus

# Then any of:
skaffold run                       # one-shot build + push + helm install
skaffold dev                       # interactive watch + hot-reload
skaffold run -p coelhonexus        # same as above, explicit profile (paranoid mode)
```

Skaffold rewrites the chart's image refs with unique sha256 tags on every build → every deploy gets a fresh rollout automatically. No `kubectl rollout restart` step needed. No shell scripts. Works on Windows.

### If Option A were chosen instead (not done):

ArgoCD `ApplicationSet` with a per-cluster generator picks the right `values-*.yaml`. Would require GitHub mirror automation + secret management on both sides.

### If Option C were chosen (not done):

A Gitea module under `infrastructure/`. Out of scope.

---

## Variables that are NOT a category — already handled correctly

These look like they might need flex but actually don't:

- **`secretName: coelhonexus-secret`** — same NAME on both clusters; the secret VALUES are populated outside the chart per cluster (via `upload_env_to_k3d.sh` or its production analog). No flex needed in the chart.
- **`secretMappings`** — same envName↔key mappings on both. The mappings define the BRIDGE; the values are in the underlying secret which is populated per cluster.
- **NodePorts (30020-30023)** — both clusters use NodePort the same way; the port numbers don't conflict.
- **`storageClassName: local-path`** — both clusters are k3d-based and use the default `local-path` provisioner.
- **`environment: production`** — interpreted by `_helpers.tpl` for ClusterIP-vs-LoadBalancer choice; same value on both clusters' production deploys. Skaffold dev uses `environment: local`.

---

## What's NOT covered by this analysis

- **GitLab CI on COELHO Cloud** — the production CI pipeline (GitLab → Registry → ArgoCD → Image Updater) is COELHO Cloud-only and intentionally untouched.
- **Image build context** (Dockerfile paths) — same for both clusters; not a flex point.

---

## Shipped 2026-06-19

1. **`k8s/helm/values-coelhonexus.yaml`** — 4-line registry/image override
2. **`skaffold.yaml`** — added `coelhonexus` profile with `kubeContext` auto-activation; replaced the previous `scripts/ci-simulate.sh` (deleted)
3. **`k8s/argocd/*.yaml`** — header comments marking them COELHO Cloud-only
4. **`k8s/helm/values.yaml`** — comment near `registry.url` pointing operators at the override file
5. **`infrastructure/README.md`** — new "Deploy the COELHO Nexus apps" section documenting the Skaffold workflow + the rationale for the two deploy models

---

## Open questions worth thinking about

- Should the chart use `{{ .Values.registry.url }}` template interpolation for image refs (DRY) instead of hardcoding the prefix in each image var? Currently violates DRY — 4 places to change instead of 1. Minor refactor worth doing alongside future image-related work.
- Does `apps/` have any code paths that would fail loudly on missing services (e.g., if SEARCH_API is empty), or do they silently degrade? Already verified zero refs for the 7 removed tools — but worth a similar audit for any cluster-conditional features.

---

## Reference

- Investigation transcript: 2026-06-19 session in `~/.claude/projects/-home-rafaelcoelho-Workbench-COELHONexus/`
- Related: [[INFRASTRUCTURE-PORT-2026-06-19.md]] — the underlying infrastructure setup this builds on
- Related: [[project_rancher_fresh_install_bug_2026_06_19]] memory — the Rancher debugging that prompted the infrastructure deep-dive
