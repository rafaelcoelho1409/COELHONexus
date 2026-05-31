# LLM Rotator — BYOK Settings Window (SOTA + phased plan)

**Date:** 2026-05-31 · **Status:** ✅ ALL 5 PHASES BUILT (2026-05-31). Logic-validated
locally (cryptography/litellm absent from the thin venv → py_compile + standalone
algorithm tests); needs a `skaffold dev` redeploy to exercise live.
**Self-contained** so implementation can continue after a conversation compaction.

> **2026-05-31 UPDATE — OpenRouter / OpenAI / Anthropic REMOVED per user.** Those
> three were never used or tested with the rotator, so all scaffolding I added for
> them was reverted: the `openrouter`/`openai`/`anthropic` registry rows,
> `FreeFilter.OPENROUTER_FREE` + its predicate, the `_openrouter_/_openai_/_anthropic_entry`
> builders + `_record_to_entry` dispatch, the `anthropic` auth-style branches, the
> `ProviderConfig.kind` field, their `_PROVIDER_META`/`MANAGED_KEY_ENVS` entries, and the
> `OPENROUTER_API_KEY` Helm secret mapping. The registry is back to the **7 original
> providers** (groq, nim, cerebras, mistral, gemini, sambanova[off], deepseek[off]).
> **2026-05-31 (3rd pass) — provider keys NO LONGER injected from Helm.** The 7
> rotator-provider `secretMappings` rows (GROQ/NVIDIA/SAMBANOVA/CEREBRAS/MISTRAL/GOOGLE/
> DEEPSEEK) are commented out in `k8s/helm/values.yaml`, so they're no longer env vars in
> any pod → `resolve_key()` has NO env fallback for them and the rotator uses ONLY
> UI-supplied keys (BYOK). Key VALUES are NOT erased (still in `coelhonexus-secret`/`.env`);
> reversible by uncommenting, or one-time `llmCredentials.importEnvKeys=true` to migrate
> them into the store. KEPT injected: `AWS_*` (the store needs MinIO), Redis/Neo4j/Postgres,
> Qdrant, Langfuse, search APIs. (Zhipu fully removed 2026-05-31 — no longer used.)
> **Consequence:** after
> redeploy the rotator + mandatory NIM embeddings are keyless until the user enters keys in
> /settings — enter NVIDIA + chat providers there BEFORE running a DD pipeline.
>
> KEPT (unrelated, correct): the bandit-map `gemini→GOOGLE_API_KEY` fix. Also removed
> (2nd pass, per user) the *pre-existing* dormant OpenAI vestiges: bandit-map
> `openai→OPENAI_API_KEY`, the `OPENAI_API_KEY` Helm secretMapping, and
> `_PROVIDER_CHAPTER_CAPS["openai"]`. Remaining `openai` strings are the OpenAI-*compatible*
> response shape / Groq `/openai/v1/models` URL / the `openai/gpt-oss-120b` open-weights
> model on NIM — all required, not the OpenAI provider. Phase 5 (paid) = future "maybe".

## Build status (2026-05-31) — what shipped
- **Phase 1** — `apps/fastapi/domains/llm/credentials/` (Fernet MinIO store
  `llm/credentials.enc`; KEK = `KD_CREDS_KEY` env OR MinIO-autogen `llm/kek.key`;
  in-process TTL cache; sync **botocore** client so `resolve_key()` works from the
  sync entry-builders; never-raises). `chain/service.py` + `discovery/service.py`
  provider-key reads swapped `_env→resolve_key` (REDIS `_env` untouched; bandit map
  `gemini→GOOGLE_API_KEY` fixed, openrouter/anthropic added). OpenRouter registry
  row + `_filter_openrouter_free` + `_openrouter_entry` + `_record_to_entry`.
  `cryptography>=42` added to pyproject. `warm()` at app.py lifespan + celery
  worker init. 19/19 store logic tests pass.
- **Phase 2** — `api/v1/llm/settings.py` (GET /providers, GET /providers/{id}/models,
  POST/DELETE /key with test-connect, POST /test, PATCH enable, POST /models,
  GET /providers/health). Selection in `llm/settings.json`. `_apply_selection_filter`
  (keyed∩enabled∩selected; no-empty guard; infra dd-keylm/dd-embed never trimmed) lives
  inside the three `*_current()` catalog accessors — so it constrains BOTH the LiteLLM
  Router model_list AND the **FGTS-VA bandit** candidate pools (`chat_judge_bandit_async`,
  `pick_synth_deployment_bandit`, pinned chains, cascade), which call `litellm.acompletion`
  directly and bypass the Router. `read_settings()` is TTL-cached (hot-path safe).
  `reset_rotator()` + Redis settings-gen (`dd:rotator:settings_gen`, throttled ≤1 GET/10s/proc)
  rebuild the Router on change; the bandit picks up selection within one settings-TTL.
  `probe_provider_key` + `list_provider_free_models` in discovery. 13/13 filter tests pass.
- **Phase 3** — `apps/fasthtml/features/settings.py` (`/settings` page, `register` in
  main.py), `static/js/settings.js`, `static/css/settings.css`, **gear in shell.py
  row 1** (inline Feather cog SVG). All calls via the `/api` proxy BFF; keys never
  return to the browser. Provider rail: masked status, key save/test/remove, enable
  switch, All-free⦿/Custom○ model selection + select-all/deselect-all, "Enable all" +
  "Test all" global actions, toast.
- **Phase 4** — store-side **KEK migration** (autogen→env KEK re-encrypts on first read,
  so enabling the Helm KEK never orphans saved keys) + opt-in env-import
  (`KD_CREDS_IMPORT_ENV`). Helm `templates/creds-kek-secret.yaml` lookup-retain Fernet
  KEK (gated `llmCredentials.manageKek`, **default false**), env wired into pod spec;
  `OPENROUTER_API_KEY` added to `secretMappings`. 11/11 tests incl. migration +
  corrupt-blob safety; `helm template` renders a valid 32-byte Fernet key.
- **Phase 5** — paid `openai` + `anthropic` registry rows (`kind="paid"`,
  `enabled=False`, anthropic `x-api-key`/`anthropic-version` auth) +
  `_openai_entry`/`_anthropic_entry` + `_record_to_entry` dispatch. Opt-in only — NOT
  in the default chat catalog, so no spend unless explicitly enabled+selected+wired.

**Catalog model (2026-05-31 — selection-driven DYNAMIC catalog now ACTIVE):** the dynamic
catalog (previously dormant) is now built at FastAPI lifespan + Celery worker init and
drives the rotator. It pulls each provider's live `/v1/models`, filters to the user's BYOK
selection, benchmark-ranks, and builds `_dynamic_entries` (the `*_current()` accessors prefer
it; static is the fallback). Per-provider:
  - **All free** (mode=`all`, default) → every discovered free model is eligible, ranked,
    then the pool is capped at the per-step top-K (dd-all 30 / dd-synth 12 / dd-reduce-label
    10) to avoid the firehose.
  - **Custom** (mode=`custom`) → ONLY the checked model_ids, and they're ALWAYS kept — never
    dropped by the top-K cap — so an explicit test set of "3 NIM + 5 Groq" runs *exactly*
    those 8 (validated). A custom pick that isn't in discovery is simply ignored (can't add a
    nonexistent model).
This feeds BOTH the Router model_list AND the FGTS-VA bandit pools. Rebuilds lazily on the
async bandit path (`ensure_dynamic_catalog()`) when the Redis settings-gen moves — so a
/settings change propagates to every Celery worker within ≤ one gen-throttle (10s), no
redeploy. A failed build (e.g. keyless at fresh boot → 0 discovered) stamps the gen so it
won't hammer discovery; adding a key bumps the gen and kicks a fresh build. Any failure →
selection-filtered STATIC fallback. Toggle off with `DD_DYNAMIC_CATALOG=0` (reverts to the
tuned static catalog, still selection-filtered).

**Redeploy to verify live:** redeploy `skaffold dev`, open the gear → `/settings`, add a
key (e.g. OpenRouter), Test, then run a DD pipeline to confirm the rotator uses it.

---
## (Original design below — retained for reference)


## Goal
Replace the current "API keys via Helm configmap" supply with a beginner-friendly
**settings window** where a non-technical user picks **providers + (free) models** and
enters API keys through the UI. Built **global** (the rotator is already global), surfaced
across the whole project — not DD-scoped. Future: optional OpenAI/Anthropic **paid**
plans. Priority is **maximum free AI** (NVIDIA NIM, Groq, Gemini free tiers, OpenRouter
`:free` aggregator). Standing constraints: FREE-TIER hosted APIs only, NO local inference,
FastHTML is a server-side BFF that reverse-proxies to FastAPI.

## Current rotator state (analysis)
Already a sophisticated, **data-driven, OpenAI-compatible multi-provider router**. Only the
*key source* and a *UI* are missing.
- Location: `apps/fastapi/domains/llm/rotator/{bandit,benchmarks,chain,discovery,otel_metrics}`.
- **Provider registry** — `discovery/constants.py::PROVIDERS` (dict[str, ProviderConfig]).
  `ProviderConfig{name, url (=/models endpoint), key_env, auth_style (bearer|query-key),
  response_shape (openai|gemini), free_filter, enabled}`. 7 providers: groq, nim, cerebras,
  mistral, gemini, sambanova(`enabled=False`), deepseek(`enabled=False`). This IS the
  data-driven schema SOTA recommends — extend it, don't rebuild.
- `discovery/service.py` fetches each provider's models (reads key at line ~147 via
  `os.environ.get(cfg.key_env)`), filters to free.
- `chain/service.py` = `chat_judge_bandit_async` (chat entry). Builds the provider table
  with `api_key: _env("X_API_KEY")`. **`_env(key) = os.environ.get(key).strip()` (line ~142)
  is the single key chokepoint** for the chat path; also provider→key_env maps at ~992-1000
  (rerank/embed paths) and a NIM rerank read at ~1112.
- `bandit/` = FGTS-VA / ParetoBandit arm selection. `otel_metrics/` = telemetry.
- Keys today: env vars from Helm configmap — `GROQ_API_KEY`, `NVIDIA_API_KEY`,
  `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY` (gemini; `GEMINI_API_KEY` also
  referenced), `SAMBANOVA_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`.
- Gaps: (a) keys only from env; (b) no UI; (c) provider/model enablement is static in code,
  not user-controlled.

## SOTA (May 2026), condensed — see sources at bottom
- **UI = dedicated settings page** (provider rail → per-provider key + model multi-select),
  NOT a modal. Mask keys to **last-4**. **Validate on save** via cheap `GET /v1/models`.
  Group local-vs-remote. (Jan/LobeChat/Open WebUI/LibreChat.)
- **Storage = encrypted at rest in the backend**, decrypted at runtime with a **KEK from
  env**. `cryptography.Fernet` (AES-128-CBC+HMAC) is the low-footgun Python default; AES-GCM
  for 256-bit AEAD. **NEVER** plaintext, localStorage, logs, or return keys to client.
- **Keys live ONLY on the backend.** Browser is write-only; GET returns masked status. Maps
  onto FastHTML-BFF→FastAPI exactly.
- **Migration = user key (store) overrides → env key (fallback).** Keep Helm keys as working
  defaults; layer the store on top. No big-bang. (LibreChat `apiKey:"user_provided"` vs `${ENV}`.)
- **OpenRouter** = one OpenAI-compatible key fronting 25+ free models (`:free` suffix) →
  serves "max free AI" + easiest onboarding. Add as one registry row.

## Design decisions (LOCKED)
- **Credential store:** single **Fernet-encrypted MinIO blob** `llm/credentials.enc`
  (`{key_env: key}` map). Plaintext `llm/settings.json` for non-secret selection (enabled
  providers + selected models + per-provider mode). MinIO fits the codebase (no Postgres
  migration); Fernet means MinIO only ever holds ciphertext. (Postgres-column is the textbook
  alternative if ever desired.)
- **KEK:** `KD_CREDS_KEY` env **if set** (stronger — separated from data), **else
  auto-generate once and persist** (zero-config). Either way STABLE across deploys.
- **Resolution:** `resolve_key(key_env) = store.get(key_env) or os.environ.get(key_env)` —
  swap the two read points (`chain/service.py::_env`, `discovery/service.py`) to use it.
- **Persistence:** survives `skaffold dev` Ctrl+C + restart — **proven this session** (all
  synth/planner blobs survived the user's stop/starts; MinIO PVC is durable). Only wiped by
  deleting the MinIO PVC (same boundary as losing synth output) or changing the KEK.
- **UI placement (GLOBAL):** gear in `shell.py` `_Shell()` **row 1** (brand+nav, on every
  page) → new `features/settings.py` (`register(rt)` → **`/settings` page**, mirrors existing
  feature-module pattern). Calls route through the existing **`proxy` BFF** → keys never reach
  the browser. The backend is already global, so YouTube Content Search + future features
  inherit it for free.
- **Model selection UX:** per provider, default **⦿ All free models (auto-includes newly
  discovered)** vs **○ Custom** (checkboxes + select-all/deselect-all toggle). On valid key →
  all free auto-selected (opt-out, serves max-free + maximizes the bandit arm pool). Global
  "enable all keyed providers".
- **Safety:** never let the candidate pool go empty (block save / fall back to env) so the
  rotator never starves and DD/YCS don't break. Provider usable only with a valid key.
- **Add OpenRouter** registry row in Phase 1.

## Phases (each independently shippable; env fallback stays active throughout)

### Phase 1 — Backend foundation: credential store + key resolution
- New `domains/llm/credentials/`: Fernet store (MinIO `llm/credentials.enc`), KEK
  (env-or-autogen-persist), `resolve_key(key_env)`.
- Swap `chain/service.py::_env` + `discovery/service.py` (and the ~992-1000 / ~1112 reads) to
  `resolve_key`.
- Add **OpenRouter** to `discovery/constants.py::PROVIDERS`.
- DONE WHEN: headless test stores+resolves a key and the rotator uses it; env still works.

### Phase 2 — Settings API + provider/model selection
- `api/v1/llm/settings.py` (next to existing `health.py`): `GET /providers` (masked status:
  `has_key/last4/source(env|user)/enabled` + available models from discovery + selected);
  `POST /providers/{id}/key` (test-connect → encrypt → store → return masked, never the key);
  `DELETE /providers/{id}/key` (revert to env); `POST /providers/{id}/test`;
  `PATCH /providers/{id}` (enable); `POST /providers/{id}/models`.
- Selection store `llm/settings.json` (enabled + selected + mode); rotator now uses only
  `keyed ∩ enabled ∩ selected` with the **no-empty-pool guard**.
- DONE WHEN: full BYOK configurable via `curl`; keys never returned.

### Phase 3 — Global settings UI (headline)
- `features/settings.py` → `/settings` page + `register(rt)` in `main.py`; **gear in
  `shell.py` row 1**.
- Provider rail; per provider: masked write-only key entry + **Test** + status badge
  (set / reachable / invalid-401 / rate-limited-429) + enable toggle + model selection
  (⦿ All free / ○ Custom + select-all). Default: valid key → all free auto-selected.
- All calls via the `proxy` BFF (keys stay backend-side).
- DONE WHEN: a non-technical user configures everything with no DevOps.

### Phase 4 — Hardening + zero-config deploy
- Helm chart **auto-generates `KD_CREDS_KEY`** (lookup-retain idiom → stable across upgrades);
  per-provider keys leave the configmap (kept only as optional fallback).
- First-boot: optionally import existing env keys into the store.
- Periodic background key health re-check (catch expired keys).
- DONE WHEN: fresh install needs zero key config in Helm; all UI-driven.

### Phase 5 — Paid providers (future / "near future")
- OpenAI/Anthropic as registry rows (drop free_filter, add "paid" badge) — UI already supports.
- Optional: cost/usage display; OpenRouter OAuth-PKCE connect (paste-free onboarding).
- DONE WHEN: paid plans selectable alongside free.

**Global rollout = no separate phase.** Backend + `/settings` already global; YCS and future
features inherit the rotator + settings automatically.

**Dependencies:** 1 → 2 → 3 sequential; 4 can overlap 3; 5 anytime after 1.

## Open items / decisions
- Storage: MinIO-blob (recommended, no migration) vs Postgres column — pick MinIO unless reason.
- KEK security: auto-gen-persist (obfuscation-grade, zero-config) vs `KD_CREDS_KEY` env
  (true at-rest separation). Build the hybrid (env-or-autogen) so it just works either way.

## Key SOTA sources
- github.com/zhanymkanov/fastapi-best-practices (structure); docs.litellm.ai/.../security_encryption_faq
  (NaCl SecretBox + KEK); librechat.ai/docs/configuration/dotenv + custom_endpoints (AES-256-GCM
  user_provided keys + data-driven provider/model schema); blog.miguelgrinberg.com/.../encryption-at-rest-with-sqlalchemy
  (Fernet TypeDecorator — canonical Python); jan.ai/docs/desktop/settings (local/remote split,
  auto-populate models); openrouter.ai/docs (.../oauth, /free — free aggregator); open-webui
  discussion #8534 (plaintext anti-pattern + threat-model caveat).
