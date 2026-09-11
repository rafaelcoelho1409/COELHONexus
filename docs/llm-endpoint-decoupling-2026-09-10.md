# LLM endpoint decoupling — migration plan (2026-09-10)

Goal: make the LLM endpoint a **runtime choice** instead of a build-time Helm
dependency, so:
- `skaffold dev` on COELHOLLMRotator + `skaffold dev` on COELHONexus can run side by
  side; rotator changes iterate in seconds, no commit → GitHub Actions → chart publish →
  `helm dependency update` cycle.
- The Settings page becomes a single OpenAI-compatible endpoint field (URL + key +
  model) instead of per-provider BYOK management.
- Eventually: delete the bundled `coelho-llm-rotator` subchart and the provider-key
  settings from COELHONexus entirely, once every app (Planner, Synth, YCS agents) is
  proven against an external COELHO LLM Rotator.

## Current integration (as of `51e20d2` rotator / this Nexus tree)

- `apps/fastapi/domains/llm/rotator/chain/service.py` in **COELHONexus is already a
  thin HTTP adapter** — `AsyncOpenAI(base_url=COELHO_ROTATOR_URL)`, OpenAI SDK, no
  in-process routing. `mark_inaccessible`, `_get_router`, `_redis_for_bandit` etc. are
  no-op shims. All FGTS-VA / bandit / discovery logic runs **server-side** in the
  standalone rotator (the 2389-line `chain/service.py` in the COELHOLLMRotator repo).
- URL already env-configurable: `COELHO_LLM_ROTATOR_URL` (default
  `http://coelho-llm-rotator-fastapi:8000/api/v1/llm/openai/v1`), plus `COELHO_LLM_MODEL`
  (default `auto`), `COELHO_LLM_API_KEY` (default `dummy`). `_normalize_base_url()`
  already accepts OpenAI's `/v1` shape and external hosts.
- **Second coupling point:** `domains.llm.rotator.discovery` / `.benchmarks` are
  imported *in-process* by:
  - `api/v1/llm/settings/router.py` (the settings page backend)
  - `api/v1/dd/planner/router.py` + `synth/router.py` — `missing_required_keys` (the
    readiness gate that blocks DD runs when a provider key is missing)
  - `api/v1/ycs/agents/router.py` + `.../byok/domain.py` — `PROVIDERS`, `rank_for_step`
  - `api/v1/llm/openai/router.py` — `PROVIDERS`, `list_all_alive_models`

---

## Phase 1 — Helm decoupling + env-var endpoint config  ✅ DONE (2026-09-10)

Chart-only, no app-logic change. Unblocks the two-`skaffold dev` workflow immediately.

- `k8s/helm/Chart.yaml` — added `condition: coelho-llm-rotator.enabled` to the subchart
  dependency.
- `k8s/helm/values.yaml` — added `coelho-llm-rotator.enabled: true` and a new `llm.endpoint`
  block (`url` / `apiKey` / `model`).
- `k8s/helm/templates/_helpers.tpl` — `commonEnvVars` now renders `COELHO_LLM_ROTATOR_URL`
  / `COELHO_LLM_API_KEY` / `COELHO_LLM_MODEL` from `llm.endpoint.*` into all three app
  configmaps (fastapi, celery, fasthtml).

Verified: `helm template . --set coelho-llm-rotator.enabled=false` drops the rotator
Deployment/Service/etc.; default still renders it.

### How to run the two-skaffold workflow

The rotator's `skaffold.yaml` deploys release `coelho-llm-rotator` to namespace
**`coelho-llm-rotator-dev`**, service `coelho-llm-rotator-fastapi`. It serves the
OpenAI-compat API at `/api/v1/llm/openai/v1/chat/completions` (verified) — same path
as the Nexus default, only the host differs (cross-namespace FQDN).

1. `skaffold dev` in COELHOLLMRotator → rotator + Valkey in `coelho-llm-rotator-dev`.
2. `skaffold dev --profile external-llm` in COELHONexus — the profile (added to
   `skaffold.yaml`, 2026-09-10) sets:
   - `coelho-llm-rotator.enabled=false` (bundled subchart not deployed)
   - `llm.endpoint.url=http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1`
3. Edit rotator code → its skaffold syncs `**/*.py` in seconds. No chart cycle.

Verified: `helm template --set coelho-llm-rotator.enabled=false` renders the FQDN into
the app configmaps and drops the rotator Deployment.

Caveats:
- `COELHO_LLM_API_KEY` rides in the configmap (blank by default). Move it to a Secret
  before any real external/OpenAI use — see `creds-kek-secret.yaml`.
- `is_external_endpoint()` (Phase 3a) is now "resolved URL != the literal in-namespace
  default, OR an API key is set" — so the cross-namespace rotator reads as external and
  the NIM-key readiness gate is skipped. Corrected 2026-09-10 (the first version used a
  service-name substring check, which matched the same-named cross-namespace service).

---

## Phase 2 — Settings page endpoint field (runtime override)  ✅ DONE (2026-09-10)

Storage: the **credential store** (MinIO-backed, shared across fastapi/celery/rotator
deployments, already the settings-persistence layer) — not a new Redis key. URL + model
live in `settings.json` under `llm_endpoint`; the API key goes through the encrypted
credentials blob via the managed env name `COELHO_LLM_API_KEY`.

- `domains/llm/credentials/keys.py` — added `COELHO_LLM_API_KEY` to `MANAGED_KEY_ENVS`
  so `set_key`/`delete_key`/`key_status` accept it.
- `domains/llm/rotator/chain/service.py` — `COELHO_ROTATOR_URL` / `COELHO_ROTATOR_MODEL`
  / `COELHO_API_KEY` are now *resolved* globals, not fixed constants:
  - `_resolve_endpoint()` — precedence: `settings["llm_endpoint"]` + `resolve_key(
    "COELHO_LLM_API_KEY")` → env var → in-cluster default. Store reads are TTL-cached,
    never raise.
  - `_apply_endpoint(force=…)` — re-resolve + reassign globals; throttled to every
    `_ENDPOINT_RESOLVE_TTL_S` (10s) unless forced.
  - `_get_async_openai()` calls `_apply_endpoint()` each time and drops the pooled
    client if the resolved endpoint changed → a process that didn't call
    `reset_rotator()` itself (e.g. the celery worker) converges within ~10s.
  - `reset_rotator()` now calls `_apply_endpoint(force=True)` (forces a fresh store
    read) before clearing `_CLIENT`.
- `api/v1/llm/settings/router.py` + `schemas.py` — `EndpointBody` + three routes:
  - `GET /api/v1/llm/settings/endpoint` → `{url, model, has_key, source, last4}` (key masked)
  - `PUT /api/v1/llm/settings/endpoint` → writes store + `reset_rotator()`; `api_key`
    omitted = keep, `""` = clear
  - `POST /api/v1/llm/settings/endpoint/test` → one 5-token completion via
    `chat_judge_bandit_async`, returns `{ok, reply/error, latency_ms, deployment}`
- `apps/fasthtml/features/settings/page.py` — `LLMEndpointCard()` at the top of
  `SettingsBody()` (URL / key / model inputs + Save + Test + status line).
- `apps/fasthtml/static/js/settings_endpoint.js` — new module (own `api()`/`toast()`
  helpers, loaded before `settings.js`).
- `apps/fasthtml/static/css/settings/settings.css` — `.settings-endpoint-card` +
  `.set-ep-*` styles appended.

All Python compiles; JS passes `node --check`.

### Behaviour notes / follow-ups

- **Test uses the *saved* config, not unsaved field values.** Flow is Save → Test.
  If a "test before save" is wanted later, the test route would need to accept a body
  and build a one-off client.
- The provider-key / free-model section below the card is still shown. It only matters
  for the bundled rotator — consider hiding it when the URL points away from
  `coelho-llm-rotator-fastapi` (Phase 3 territory).
- `~10s` convergence for the celery worker on an endpoint change (throttled store
  re-read). A settings change mid-planner-run won't retro-apply to in-flight calls;
  fine for an ops action.

---

## Phase 3a/3b — Behave correctly in external-endpoint mode  ✅ DONE (2026-09-10)

Additive only — nothing deleted. Makes external mode functional so it can be validated
before the destructive Phase 3c.

- `rotator/chain/service.py` — `is_bundled_rotator()` / `is_external_endpoint()`
  (host check: resolved URL contains `coelho-llm-rotator-fastapi` → bundled). Exported
  from `chain/__init__.py`.
- `rotator/discovery/service.py` — `missing_required_keys()` returns `[]` early when
  `is_external_endpoint()` (lazy import, no cycle — `chain` doesn't import `discovery`).
  This satisfies the planner/synth run-start gates, the settings `/readiness` banner,
  and `/providers` `ready` flag in one place.
- `fasthtml/static/js/settings_endpoint.js` — sets
  `#settings-root[data-llm-external="1"]` from the configured URL.
- `settings.css` — that attr dims the provider-key section + prepends a note
  ("An external LLM endpoint is set — these provider keys configure the bundled
  rotator, which isn't in use.").

Verified: `is_bundled_rotator()` True on the default URL; import clean, no cycle.

### To validate external mode

1. `skaffold dev` the rotator standalone (or use OpenAI).
2. Settings → LLM Endpoint → set URL (+ key), Save, Test → expect OK.
3. Confirm `/settings` readiness banner goes green with no NIM key set.
4. Run a Planner + a Synth end to end. Check YCS agents too.

## Phase 3c-lite — Delete the bundled subchart itself  ✅ DONE (2026-09-11)

Triggered by a real incident: a Planner run silently served on a 20h-stale
rotator build because plain `skaffold dev` on Nexus deploys the bundled
`coelho-llm-rotator` subchart from a pinned registry image, while the F–L
wave batch was only ever built by the rotator's *own* `skaffold dev` into
`coelho-llm-rotator-dev`. Two copies, one drifting silently — the Settings
endpoint field existed (Phase 2) but nothing forced its use, so a store-read
hiccup (or a celery process older than the save) fell back to the bundled
in-namespace default with only a debug-level log. Decision: don't patch the
fallback — remove the second copy so there's nothing to fall back to.

- `k8s/helm/Chart.yaml` — deleted the `coelho-llm-rotator` dependency block
  entirely (no more `condition:`/`skaffold --profile external-llm` dance).
- `k8s/helm/values.yaml` — deleted the `coelho-llm-rotator:` block; `llm.endpoint.url`
  now defaults to the rotator's own dev-workflow FQDN:
  `http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1`.
- `k8s/helm/Chart.lock` + vendored `charts/coelho-llm-rotator-*.tgz` — deleted.
- `skaffold.yaml` — deleted the `external-llm` profile; plain `skaffold dev` is
  now the only mode. Run the rotator's own `skaffold dev` (namespace
  `coelho-llm-rotator-dev`) alongside it — the default already points there.
- `chain/service.py` — `_DEFAULT_ROTATOR_URL` updated to the same FQDN;
  `is_bundled_rotator()` → always `False`, `is_external_endpoint()` → always
  `True` (kept as named shims for the two remaining callers rather than
  inlining, so the readiness-gate call site still reads as intentional).
- Settings page (`page.py` / `settings_endpoint.js` / `settings.css`) — copy
  updated ("dev-workflow rotator" instead of "bundled/in-cluster"); the
  provider-key section is now *unconditionally* dimmed (there's no bundled
  rotator left for it to configure), instead of conditionally comparing the
  saved URL against a hardcoded default.

Result: one URL, one code path. The Settings-page field (with its store-backed
runtime override, Phase 2) is the single source of truth for where the rotator
is — including for the "default" case, which is no longer a distinct
in-cluster deployment, just that field's starting value. A store-read failure
now degrades to the *same* rotator (not a stale second copy), and if that
rotator is genuinely unreachable the failure is a visible connection error,
not silently-stale code serving 200s.

**Not addressed by this pass** (deferred to full Phase 3c below): the vendored
`domains/llm/rotator/{bandit,discovery,benchmarks}` modules and the
provider-key settings UI still exist, just permanently dimmed/unused for
routing. `missing_required_keys()` still contains an unreachable PROVIDERS
loop. No production rotator currently runs anywhere, so `llm.endpoint.url`'s
default only resolves inside the local dev cluster — a real prod deploy needs
an explicit override once a prod rotator exists.

## Phase 3c — Delete the vendored bandit/discovery/benchmarks + provider settings UI  ⬜ TODO (after a green Planner run against the current external rotator)

Only once every app is proven against an external endpoint:

1. `grep -rn "from domains.llm.rotator.\(bandit\|discovery\|benchmarks\)" apps/` — every
   hit must have migrated. Known hits: settings router (`PROVIDERS`,
   `list_provider_free_models`, `probe_provider_key`), `api/v1/llm/openai/router.py`,
   `api/v1/ycs/agents/router.py` (`rank_for_step` + discovery), `.../byok/domain.py`.
   The planner/synth readiness gates are already handled (3a).
2. Delete `domains/llm/rotator/{bandit,discovery,benchmarks}` from Nexus (keep `chain/`).
3. Delete the provider-key / free-model-selection routes + UI from the settings page;
   keep only the endpoint card. Drop the `[data-llm-external]` dimming CSS with it.
4. Remove the `coelho-llm-rotator` dependency from `Chart.yaml` + the `coelho-llm-rotator`
   block from `values.yaml`. Keep `llm.endpoint`. Delete `charts/coelho-llm-rotator-*.tgz`.
5. The external rotator now owns ALL provider-key management, model selection, bandit
   tuning — verify its own settings page covers every field Nexus's did before deleting.

## Caveats carried forward

- **The `(text, meta)` return.** `chat_judge_bandit_async` returns per-call metadata
  (`deployment`, `reward`, `attempts`, `latency_s`) used for logging. Confirm how the
  rotator returns it over HTTP today (custom response fields? headers?). Pointing at a
  plain OpenAI endpoint loses it — consumers must degrade gracefully. External rotator
  keeps it.
- **Two skaffold sessions, one cluster** — need a stable service-name/namespace contract
  so `llm.endpoint.url` resolves consistently.
