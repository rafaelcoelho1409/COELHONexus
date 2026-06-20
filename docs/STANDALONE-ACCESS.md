# COELHONexus standalone — local access reference

Quick-reference for accessing every UI on the standalone k3d cluster. Bookmark this — it's the answer to "where do I log in?"

---

## Start everything

```bash
# Open all host ports (auto-reconnects on pod restarts; safe to run twice)
bash scripts/standalone-port-forward.sh

# Stop everything
pkill -f coelhonexus-pf
```

The script logs each forward to `/tmp/coelhonexus-pf-<service>.log` if something looks broken.

---

## URLs + credentials

### Application UIs (need `skaffold run` to be deployed first)

| Service | URL | Login |
|---|---|---|
| **FastAPI** | http://localhost:23000 | — (no auth) |
| **Flower** (Celery) | http://localhost:23002 | — (only if `flower.basicAuth.enabled=true` in chart) |
| **FastHTML** | http://localhost:23003 | — (no auth) |
| **FastMCP** | http://localhost:23004 | — (no auth; internal MCP gateway) |

Until you've run `skaffold run`, these will all show "connection refused" — port-forward retries silently every 10 s; works as soon as pods come up.

### Infrastructure UIs (available immediately after `terragrunt run --all apply`)

| Service | URL | Username | Password |
|---|---|---|---|
| **Rancher** | **https://localhost:23010** *(HTTPS, self-signed cert)* | `admin` | `rancher-demo-bootstrap` *(forced reset on first login)* |
| **Grafana** | http://localhost:23005 | `admin` | `admin` |
| **LangFuse** | http://localhost:23006 | `admin@demo.local` | `admin-demo-password` |
| **ArgoCD** | http://localhost:23007 | `admin` | `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` |
| **MinIO Console** | http://localhost:23008 | `minioadmin` | `minioadmin` |
| **MinIO S3 API** | http://localhost:23009 | (for `mc admin` / `aws-cli` / S3 SDKs) | same as Console |

---

## Important quirks

### Rancher (HTTPS, self-signed)

- Browse to **`https://localhost:23010`** (not http). The chart now uses `tls: rancher` — Rancher generates self-signed certs via cert-manager.
- Your browser will warn about the cert → "Advanced" → "Proceed to localhost (unsafe)".
- First login forces a password change. Pick anything; that becomes the real password going forward.
- After login, Rancher's "Server URL" setup screen wants you to confirm `https://localhost:23010` and scroll the EULA box to the bottom before the checkbox enables.

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
| **App-side env vars** (read by FastAPI/Celery/FastHTML/FastMCP pods) | `.env` (gitignored; copy from `.env.example`) → uploaded as `coelhonexus-secret` K8s Secret via `./upload_env_to_k3d.sh .env coelhonexus-dev` |
| **Rancher / Grafana / LangFuse admin passwords** | `infrastructure/env.hcl` `demo.*_password` entries |
| **BYOK LLM API keys** | `.env` for one-time bootstrap, then via the `/settings` UI (Fernet-encrypted in MinIO) |

Change values in `env.hcl` → re-run `terragrunt apply` on the relevant leaf. Change BYOK keys → just edit them in the `/settings` UI (no redeploy).

---

## Connectivity smoke test (one-liner per service)

```bash
export KUBECONFIG=$HOME/Workbench/COELHONexus/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig

# Infrastructure (all should return HTTP 200/302/401 — anything but timeout/refused is healthy)
for svc in 23005:grafana 23006:langfuse 23007:argocd 23008:minio-ui 23009:minio-s3; do
  port=${svc%:*}; name=${svc#*:}
  code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:$port/" 2>/dev/null)
  echo "  $name (localhost:$port): HTTP $code"
done

# Rancher (HTTPS, self-signed)
echo "  rancher (localhost:23010): HTTP $(curl -sk -o /dev/null -w '%{http_code}' --max-time 3 https://localhost:23010/)"

# Apps (only after skaffold run)
for svc in 23000:fastapi 23002:flower 23003:fasthtml 23004:fastmcp; do
  port=${svc%:*}; name=${svc#*:}
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:$port/" 2>/dev/null)
  echo "  $name (localhost:$port): HTTP $code"
done
```

Any `000` means the port-forward isn't up (or the pod is down). Any `200`/`302`/`401`/`403` means the service is responding — that's "healthy" for this check.
