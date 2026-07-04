# COELHONexus standalone — local access reference

Quick-reference for accessing every UI on the standalone k3d cluster. Bookmark this — it's the answer to "where do I log in?"

---

## Start everything

Infrastructure services are reachable immediately after `terragrunt run --all apply` — native k3d NodePort, no extra step, no script.

The four app services need `skaffold dev` running in the foreground — it forwards them itself via its own `portForward:` config (cross-platform, no shell script involved):

```bash
skaffold dev
```

Three deploy mechanisms, three disjoint port ranges (kept deliberately non-overlapping): `23000-23019` ArgoCD/production, `23001`+`23011-23023` Terragrunt-managed native NodePort infra, `23030-23039` Skaffold dev. See `README.md`'s Service Access section for the full budget table.

---

## URLs + credentials

### Application UIs (need `skaffold dev` running)

| Service | URL | Login |
|---|---|---|
| **FastAPI** | http://localhost:23030 | — (no auth) |
| **Flower** (Celery) | http://localhost:23032 | — (only if `flower.basicAuth.enabled=true` in chart) |
| **FastHTML** | http://localhost:23033 | — (no auth) |
| **FastMCP** | http://localhost:23034 | — (no auth; internal MCP gateway) |

Until `skaffold dev` is running, these will all show "connection refused" — `skaffold run` alone won't forward them (it's one-shot and exits, no foreground process to proxy through).

### Infrastructure UIs (available immediately after `terragrunt run --all apply`)

| Service | URL | Username | Password |
|---|---|---|---|
| **Neo4j Browser** | http://localhost:23001 | `neo4j` | `neo4j-demo-password` (Bolt URI field needs `bolt://localhost:23012`) |
| **Qdrant Dashboard** | http://localhost:23011 | — (API key field, not user/pass) | `qdrant-demo-api-key` |
| **Elasticsearch REST API** | https://localhost:23013 | `coelhonexus` | `coelhonexus-demo-password` |
| **Kibana** | https://localhost:23014 | `coelhonexus` | `coelhonexus-demo-password` (no separate credential — authenticates against Elasticsearch directly) |
| **MinIO S3 API** | http://localhost:23015 | `minioadmin` | `minioadmin` (for `mc admin` / `aws-cli` / S3 SDKs) |
| **MinIO Console** | http://localhost:23016 | `minioadmin` | `minioadmin` |
| **LangFuse** | http://localhost:23017 | `admin@demo.local` | `admin-demo-password` |
| **Playwright noVNC** | http://localhost:23018 | — (VNC has no username) | `vnc-demo-password` |
| **Playwright headed CDP** | ws://localhost:23019 | — (not a browser UI — CDP WebSocket endpoint) | — |
| **Playwright headless CDP** | ws://localhost:23020 | — (not a browser UI — CDP WebSocket endpoint) | — |
| **Rancher** | **https://localhost:23021** *(HTTPS, self-signed cert)* | `admin` | `rancher-demo-bootstrap` *(forced reset on first login)* |
| **Grafana** | http://localhost:23022 | `admin` | `admin` |
| **ArgoCD** | http://localhost:23023 | `admin` | `admin` (set by a post-install sync Job; fall back to `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` if it hasn't landed yet) |

---

## Important quirks

### Rancher (HTTPS, self-signed)

- Browse to **`https://localhost:23021`** (not http). The chart now uses `tls: rancher` — Rancher generates self-signed certs via cert-manager.
- Your browser will warn about the cert → "Advanced" → "Proceed to localhost (unsafe)".
- First login forces a password change. Pick anything; that becomes the real password going forward.
- After login, Rancher's "Server URL" setup screen wants you to confirm `https://localhost:23021` and scroll the EULA box to the bottom before the checkbox enables.

### LangFuse — initial setup

LangFuse v3 may show an initial onboarding flow on first access. The org/project/user are pre-seeded via env.hcl `demo.langfuse_init_*` values; just log in.

### ArgoCD — initial password rotates

The auto-generated `argocd-initial-admin-secret` is deleted by ArgoCD after the FIRST successful login. After that, the password is whatever you set in the UI. To get the current initial-password command:

```bash
export KUBECONFIG=$HOME/Workbench/COELHONexus/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

If the secret no longer exists, you've already rotated the password — use whatever you set.

### Browser HSTS cache for Rancher

If Rancher misbehaves after you've previously used the URL on a different config (e.g., `tls: external`), browsers cache HSTS policies and may refuse to load HTTP or insist on the wrong port. **Always test in a fresh incognito/private window** when changing Rancher's TLS settings.

---

## Source of truth for credentials

| | Where it lives |
|---|---|
| **Cluster data layer** (postgres, redis, neo4j, elasticsearch, minio, qdrant, langfuse) | `infrastructure/env.hcl` `demo` block — the data modules are provisioned with these |
| **App-side env vars** (read by FastAPI/Celery/FastHTML/FastMCP pods) | `.env` (gitignored; copy from `.env.example`) → uploaded as `coelhonexus-secret` K8s Secret via `python upload_env_to_k3d.py .env coelhonexus-dev` (cross-platform — Windows/macOS/Linux) |
| **Rancher / Grafana / LangFuse admin passwords** | `infrastructure/env.hcl` `demo.*_password` entries |
| **BYOK LLM API keys** | `.env` for one-time bootstrap, then via the `/settings` UI (Fernet-encrypted in MinIO) |

Change values in `env.hcl` → re-run `terragrunt apply` on the relevant leaf. Change BYOK keys → just edit them in the `/settings` UI (no redeploy).

---

## Connectivity smoke test (one-liner per service)

```bash
export KUBECONFIG=$HOME/Workbench/COELHONexus/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig

# Infrastructure — native NodePort, no extra step needed (all should return
# HTTP 200/302/401 — anything but timeout/refused is healthy; -k handles the
# self-signed certs on Elasticsearch/Kibana/Rancher)
for svc in 23001:neo4j 23011:qdrant 23013:elasticsearch 23014:kibana 23015:minio-s3 23016:minio-ui 23017:langfuse 23018:playwright-novnc 23021:rancher 23022:grafana 23023:argocd; do
  port=${svc%:*}; name=${svc#*:}
  code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 3 "https://localhost:$port/" 2>/dev/null)
  echo "  $name (localhost:$port): HTTP $code"
done

# Playwright's CDP endpoints are WebSocket, not plain HTTP — check the version
# introspection endpoint instead
for svc in 23019:playwright-headed-cdp 23020:playwright-headless-cdp; do
  port=${svc%:*}; name=${svc#*:}
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:$port/json/version" 2>/dev/null)
  echo "  $name (localhost:$port): HTTP $code"
done

# Apps — only respond while `skaffold dev` is running in the foreground
for svc in 23030:fastapi 23032:flower 23033:fasthtml 23034:fastmcp; do
  port=${svc%:*}; name=${svc#*:}
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:$port/" 2>/dev/null)
  echo "  $name (localhost:$port): HTTP $code"
done
```

Any `000` means the forward isn't up (native NodePort service not yet applied, `skaffold dev` not running, or the pod is down). Any `200`/`302`/`401`/`403` means the service is responding — that's "healthy" for this check.
