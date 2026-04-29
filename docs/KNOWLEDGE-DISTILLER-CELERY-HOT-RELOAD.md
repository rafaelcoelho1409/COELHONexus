# Celery Hot-Reload via `watchmedo` (Skaffold Dev Loop)

Status: pending implementation
Decision date: 2026-04-28
Motivation: Skaffold sync copies `*.py` changes into running pods, but the
long-running Celery worker process keeps the OLD module imports in
memory. Module changes only take effect after pod restart, breaking
the fast Claude-Code dev loop.

---

## Problem

Today's dev workflow:

1. Claude edits `services/knowledge/markdown_extractor.py`
2. Skaffold's `**/*.py → /app` sync rule copies the file into the running pod
3. **uvicorn `--reload`** detects the change and reloads → FastAPI sees new code
4. **Celery worker** keeps the old import cached → tasks still run old code
5. To pick up changes: `kubectl delete pod <celery-worker>` (forces restart) OR `b` to rebuild image

This breaks the "edit → sync → test" loop for any change that affects code
running inside Celery tasks (ingestion pipeline, post-ingest, markdown
extractor, tier orchestration). Currently every test cycle for those
modules requires a manual worker restart, killing the speed advantage of
Skaffold sync.

## Why Celery doesn't auto-reload

Celery had a `--autoreload` flag in 3.x but removed it in 4.x as unreliable.
There is no first-class hot-reload in Celery itself. The recommended modern
pattern is to use `watchdog`'s `watchmedo` CLI to wrap the celery command.

## Solution: `watchmedo auto-restart`

`watchmedo` from the `watchdog` package watches files matching a glob and
**restarts the wrapped process** when changes are detected. Concrete shell:

```bash
watchmedo auto-restart \
    --directory=/app \
    --pattern='*.py' \
    --recursive \
    --debounce-interval=2.0 \
    -- \
    celery -A celery_app worker --loglevel=INFO --concurrency=2
```

`--debounce-interval=2.0` matches the uvicorn `--reload-delay 2.0` setting
already in `apps/fastapi/entrypoint.sh` — bursts of file changes (Claude
editing 5 files in 1s) trigger one restart instead of five.

## Behavior comparison

| Today | With watchmedo |
|---|---|
| Edit `.py` → Skaffold syncs → worker keeps OLD import → test runs old code | Edit → sync → watchmedo detects → restarts worker (~3-5s) → new import |
| Manual `kubectl delete pod` to refresh | Automatic |
| Or `b` to rebuild image | Skip rebuild for pure-Python changes |
| Worker restart loses in-flight work | Same |

## Trade-offs

**Pros:**
- Eliminates "did the worker pick up my change?" friction
- Matches uvicorn dev loop velocity
- ~3-5s reload cost vs ~30-60s for full pod recreate or image rebuild

**Cons:**
- Worker restart aborts in-flight tasks (drops queued-but-not-acked messages)
- Already mitigated for KD by `acks_late=False` on `run_knowledge_distiller`
  and `run_knowledge_ingestion` — re-running is the user's responsibility
- During long Tier-4 Playwright crawls (~20 min), an accidental Python edit
  would abort the crawl. Mitigation: raise `--debounce-interval` higher
  (e.g., 10s), or use Skaffold's `--auto-sync=false` and manual `s` keypress

## Implementation

Three small changes:

### 1. `apps/fastapi/pyproject.toml` — add dev-only dep

```toml
"watchdog[watchmedo]>=4.0",  # dev-only Celery hot-reload via watchmedo (KD pipeline). See docs/KNOWLEDGE-DISTILLER-CELERY-HOT-RELOAD.md.
```

`watchdog` is small (~200 KB); `[watchmedo]` extra adds the CLI script.

### 2. `apps/fastapi/celery-entrypoint.sh` — new file (mirrors uvicorn `entrypoint.sh` pattern)

```bash
#!/bin/bash
set -e
mkdir -p /app/logs

# Hot-reload only in local/development (Skaffold), not production (ArgoCD).
# watchmedo wraps the celery worker and restarts on .py changes synced
# into /app. --debounce-interval matches uvicorn's --reload-delay so
# multi-file Claude edits trigger one restart instead of N.
if [ "$ENVIRONMENT" = "local" ] || [ "$ENVIRONMENT" = "development" ]; then
  echo "Starting celery worker with watchmedo auto-restart (development mode)"
  exec watchmedo auto-restart \
    --directory=/app \
    --pattern='*.py' \
    --recursive \
    --debounce-interval=2.0 \
    -- \
    celery -A celery_app worker --loglevel=INFO --concurrency=2
else
  echo "Starting celery worker (production mode)"
  exec celery -A celery_app worker --loglevel=INFO --concurrency=4
fi
```

Make executable: `chmod +x apps/fastapi/celery-entrypoint.sh`.

### 3. `k8s/helm/templates/fastapi/deployment.yaml` — point celery container at the new script

Find the celery-worker container's `command:` / `args:` block and replace
with `["/bin/bash", "/app/celery-entrypoint.sh"]` (mirrors how the FastAPI
container uses `entrypoint.sh`).

## Skaffold interaction

Existing `skaffold.yaml` sync rule:

```yaml
manual:
  - src: "**/*.py"
    dest: /app
```

This already syncs Python files into the celery container (same image as
fastapi container). Adding watchmedo doesn't require any sync rule change
— files arrive in `/app`, watchmedo notices, restarts celery. Done.

`celery-entrypoint.sh` itself is NOT in any sync rule (intentional —
changes to the entrypoint script require a real rebuild, same as
`entrypoint.sh`).

## Validation plan

After deploying:

1. Verify both pods up: `kubectl get pods -n coelhonexus-dev`
2. Tail celery worker logs: `kubectl logs -n coelhonexus-dev <worker-pod> -f`
3. Edit any `.py` file inside `apps/fastapi/services/knowledge/` (e.g., a
   one-line comment change in `markdown_extractor.py`)
4. Watch logs — within ~3-5s should see watchmedo restart message and
   celery worker re-initializing
5. Run a `/ingestion` task — verify the new import is in effect

## Out of scope

- Production worker behavior (kept on plain `celery worker` — no watchmedo)
- Other workers (YouTube transcript pipeline, etc.) — same change applies
  if desired, but separate scope
- Grace period for in-flight tasks before restart (could add via SIGTERM
  handler in celery_app.py if needed; current `acks_late=False` is the
  mitigation)

## References

- watchdog (PyPI): https://pypi.org/project/watchdog/
- watchmedo CLI docs: https://python-watchdog.readthedocs.io/en/stable/quickstart.html
- Celery #1898 (autoreload removal rationale): https://github.com/celery/celery/issues/1898
- Existing uvicorn pattern: `apps/fastapi/entrypoint.sh` (`--reload --reload-delay 2.0`)
- Skaffold config: `skaffold.yaml` (sync rules)
