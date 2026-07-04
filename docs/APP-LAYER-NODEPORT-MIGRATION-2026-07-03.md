# App-layer NodePort migration — replace `standalone-port-forward.sh`

> **Related, separate thread (2026-07-03, same day):** while testing `skaffold dev` registry targeting, found the `coelhonexus` Skaffold profile was dead/commented-out config (removed entirely — see git history) and that `--default-repo=<registry>` is the actual, correct mechanism (a CLI flag, not something `skaffold.yaml` or `~/.skaffold/config` controls). Also started exploring deploying the standalone cluster's own `coelhonexus` namespace via its `k8s/argocd/prod/application.yaml` + Image Updater (build/push commands documented in `infrastructure/README.md`'s "Common operations" section) — not yet applied/tested end-to-end. Not part of THIS doc's scope (that's the app-layer NodePort work below), but shares the same port-budget and registry-targeting context.
>
> **Update, later the same day:** the standalone registry's host port changed from `5000` to **`5001`** — `infrastructure/modules/k3d/variables.tf`'s `registry_port` default now matches COELHO Cloud's own override (`~/COELHOCloud/infrastructure/live/homelab/00-bootstrap/k3d/terragrunt.hcl:60`), since the two clusters are never run concurrently and a single shared port removes the need to track two different registry addresses across docs/commands. The `curl http://localhost:5000/v2/` → HTTP 200 check mentioned above was valid at the time (old port) — re-verify against `:5001` after the cluster is recreated with the new default.

**Date:** 2026-07-03
**Status:** Planned, not started. Discovered while diagnosing a Rancher `ERR_SSL_PROTOCOL_ERROR` during a live fresh-install test (docs pointed at the port-forward-script URL for a service that was actually already reachable via native NodePort at a different port).
**Goal:** Make the app layer (FastAPI, FastHTML, FastMCP, Flower) reachable the same OS-agnostic way the infra layer already is — native k3d NodePort, baked in at cluster-creation time, zero long-running processes — instead of `scripts/standalone-port-forward.sh` (bash, Unix/WSL-only).

---

## Why this exists

`scripts/standalone-port-forward.sh` is the last bash-only, non-cross-platform piece of the standalone access story. Everything else already moved off bash for the same reason: `upload_env_to_k3d.py` replaced `upload_env_to_k3d.sh`, and Skaffold was chosen over shell scripts for the whole app deploy path (`K8S-DUAL-CLUSTER-FLEX-2026-06-19.md`). This closes the last gap.

The infra layer (Grafana, LangFuse, ArgoCD, MinIO, Rancher, Neo4j, Qdrant, Elasticsearch, Kibana, Playwright) already works this way: `infrastructure/modules/k3d/main.tf` bakes `--port HOST:NODEPORT@loadbalancer` flags into the `k3d cluster create` call, so those services are reachable on `localhost` immediately after `terragrunt apply` — no script, no background process, works identically on any OS running Docker.

## What's already true (verified 2026-07-03, don't re-derive)

- `k8s/helm/values.yaml` — the **shared** Helm chart (also backs COELHO Cloud production, not standalone-only):
  - `fastapi` — already `type: LoadBalancer`, pinned `nodePort: 30020` (container port 8000).
  - `flower` — already `type: LoadBalancer`, pinned `nodePort: 30022` (container port 5555).
  - `fasthtml` — already `type: LoadBalancer`, pinned `nodePort: 30023` (container port 3000).
  - `fastmcp` — still `type: ClusterIP`, **no nodePort at all**. Comment above it says "Internal ClusterIP — Research Radar agent connects as MCP client via Streamable-HTTP at `/mcp/`" — internal-by-design, but the old port-forward script exposed it externally anyway (for debugging tool calls directly). Preserve that capability, just via the new mechanism.
- `infrastructure/modules/k3d/main.tf` — the `--port` list currently only covers the infra NodePorts (30474-30487, host ports 23001/23011-23023). It does **not** map 30020/30022/30023 (the app-layer nodePorts that already exist in the chart) to any host port yet — so even though FastAPI/Flower/FastHTML are already `LoadBalancer` type, nothing forwards them to `localhost` today.
- k3d backs `LoadBalancer`-type Services via its own klipper-lb through the `k3d-coelhonexus-serverlb` container — same container/mechanism already confirmed working for every infra service (`docker port k3d-coelhonexus-serverlb`).

## The plan (4 steps, not yet executed)

1. **`k8s/helm/values.yaml`** — flip `fastmcp.portsSettings` from `type: ClusterIP` (no nodePort) to `type: LoadBalancer` with `nodePort: 30024` (next free slot after 30020/30022/30023).
2. **`infrastructure/modules/k3d/main.tf`** — add 4 new `--port` flags to the `k3d cluster create` command, reusing the *same host port numbers* the old bash script used so documented URLs don't change, only the access mechanism does:
   ```
   --port "23000:30020@loadbalancer" \    # FastAPI
   --port "23002:30022@loadbalancer" \    # Flower
   --port "23003:30023@loadbalancer" \    # FastHTML
   --port "23004:30024@loadbalancer" \    # FastMCP
   ```
3. **Patch the live cluster** (since `--port` flags only apply at cluster-create time, and recreating from scratch isn't warranted just for this): `k3d cluster edit coelhonexus --port-add "HOST:NODEPORT@loadbalancer"` per port above — same workaround pattern already used earlier this session to add the infra ports to a running cluster without a full recreate.
4. **Once verified working end-to-end** (after a `skaffold run` actually deploys the app Services so there's something listening behind the new NodePorts): delete `scripts/standalone-port-forward.sh`, and update `README.md`, `docs/STANDALONE-ACCESS.md`, and `infrastructure/README.md` — all three currently document the app layer as requiring the port-forward script; that requirement goes away.

## Open question (unresolved)

Whether to stop after step 3 and let the user verify actual browser/curl access before proceeding to step 4 (script removal + doc updates), or do all 4 in one pass. Leaning toward stopping after step 3 — same "verify against live state before documenting" discipline used throughout this session's other doc work.

## Update 2026-07-03 (same day, later): interim state changed, new collision found

User asked to remove all `scripts/standalone-port-forward.sh` citations from `README.md` before this migration was executed. Handled by switching the README's documented mechanism to `skaffold.yaml`'s **already-existing** `portForward:` stanza (lines 155-183) instead — genuinely OS-agnostic (Skaffold itself forwards, not a wrapper script), no code changes needed for this part.

**But this exposed a real, currently-unresolved port collision**: `skaffold.yaml`'s `portForward:` block uses its own port scheme (23020 FastAPI, 23022 Flower, 23023 FastHTML, 23024 FastMCP) — DIFFERENT from both the old bash script's ports (23000/23002/23003/23004) AND this doc's step-2 plan (which assumed reusing the old script's numbers). Two of Skaffold's dev ports collide with EXISTING native NodePort infra services:
- Flower `:23022` collides with Grafana's native NodePort (`:23022`).
- FastHTML `:23023` collides with ArgoCD's native NodePort (`:23023`).

`README.md` now documents this collision inline with a ⚠️ warning rather than hiding it — not fixed yet at time of writing (superseded a few hours later, see next update).

## Update 2026-07-03 (same day, later still): collision resolved, full port budget formalized

User's explicit decision: keep ArgoCD/prod's `23000-23019` block exactly as-is (don't move it), and give Skaffold its **own dedicated range** that overlaps with nothing else — rather than trying to make Skaffold match either of the other two zones. This supersedes the "implication" note directly above (which suggested aligning Skaffold to the 23000/23002/23003/23004 numbers — no longer the plan).

Resolved by moving `skaffold.yaml`'s `portForward:` block from `23020/23022/23023/23024` to **`23030/23032/23033/23034`** (fastapi/flower/fasthtml/fastmcp respectively) — one tens-digit shift, preserves the existing "last digit matches prod" mnemonic. `23024-23029` deliberately left unclaimed as headroom for future Terragrunt modules. Full 4-zone budget (now documented in both `skaffold.yaml`'s own header comment and `README.md`'s Service Access section):

| Range | Zone | Fixed? |
|---|---|---|
| `23000-23019` | ArgoCD / `coelhonexus` prod (`kubectl port-forward`) | Fixed, by explicit user request |
| `23001`, `23011-23023` | Terragrunt-managed native k3d NodePort infra | Fixed, already live |
| `23024-23029` | Unused — headroom for future Terragrunt modules | — |
| `23030-23039` | Skaffold / `coelhonexus-dev` (`portForward:`) | Fixed, resolved today |

**This does NOT touch or supersede the 4-step plan above** (native NodePort for the app layer, still not started). That plan's step 2 port choice (23000/23002/23003/23004, reusing the deleted bash script's numbers) would now put the FUTURE native-NodePort app layer inside the SAME range as ArgoCD/prod's reserved block — worth re-deciding when that work actually starts, given the reserved-range budget above didn't exist yet when step 2 was originally written. Two reasonable options at that point: (a) keep step 2's original choice since it's a different *mechanism* (native NodePort vs kubectl port-forward) even if the numbers overlap conceptually with ArgoCD's reserved block, or (b) give the future native-NodePort app layer its own slice of the `23024-23029` headroom instead, keeping all 4 zones fully numerically disjoint too. Not decided — flag for whoever picks up the 4-step plan.

No Terragrunt reinstall/reapply was needed for today's fix — `portForward:` is pure client-side Skaffold config, doesn't touch any Kubernetes Service, Helm chart, or Terraform-managed resource.

## Not in scope here

- `scripts/argocd-port-forward.sh` (COELHO Cloud-specific, different cluster) — untouched.
- `scripts/standalone-up.sh` (Unix-only phased alternative to `terragrunt run --all apply`) — untouched; a separate, lower-priority cross-platform gap, not blocking this migration.
