# Knowledge Distiller — Router Split Plan

**Status:** Planned — NOT YET APPLIED
**Date:** 2026-04-21
**Author-decision driver:** match the YouTube `content.py` + `agents.py` split already in production, enable per-node debug endpoints for faster KD pipeline iteration.

## Context

The KD router today is a single 891-line file (`apps/fastapi/routers/v1/knowledge/distiller.py`) hosting 9 endpoints that mix:
- LLM-heavy operations (scope classifier, pipeline trigger)
- Data-layer operations (Redis reads, file downloads, tree walkers, deletes)
- Export operations (Pandoc/Anki, no LLM)

YouTube solved the same shape by splitting into:
- `routers/v1/youtube/content.py` — data ops, no LLM
- `routers/v1/youtube/agents.py` — AI-heavy endpoints
- `routers/v1/youtube/helpers.py` — shared utilities (2,108 LoC)

This doc applies the same pattern to KD, and in the process gives us the skeleton for **per-node debug endpoints** (discussed in the "debug each graph node in parts" thread).

## Target layout

```
apps/fastapi/routers/v1/knowledge/
├── __init__.py
├── content.py        # data-layer endpoints (no LLM, no pipeline triggers)
├── agents.py         # AI/pipeline endpoints (LLM calls, pipeline orchestration, per-node debug)
└── helpers.py        # shared utilities (Redis ops, study-root builder, tree walker, auth gates, etc.)
```

`distiller.py` is deleted after the migration. During the deprecation window, a thin shim can stay that 301-redirects old paths to new ones.

## Mount points (`apps/fastapi/app.py`)

Symmetric with YouTube:

```python
app.include_router(
    knowledge_agents.router,
    prefix = "/api/v1/knowledge/agents",
    tags = ["Knowledge"],
)
app.include_router(
    knowledge_content.router,
    prefix = "/api/v1/knowledge/content",
    tags = ["Knowledge"],
)
```

## Endpoint mapping

### `content.py` — data layer (no LLM)

| New path | Source endpoint | What it does |
|---|---|---|
| `GET  /content/studies/{study_id}` | `GET /studies/{id}` | Read study state from Redis |
| `GET  /content/studies/{study_id}/stream` | `GET /studies/{id}/stream` | SSE progress stream |
| `GET  /content/studies/{study_id}/tree` | `GET /studies/{id}/tree` | MinIO artifact file tree |
| `GET  /content/studies/{study_id}/chapters/{n}` | `GET /studies/{id}/chapters/{n}` | Fetch one chapter's artifacts |
| `GET  /content/downloads/{user_id}/{slug}` | `GET /downloads/{user_id}/{slug}` | Download a file (tarball/PDF/etc.) |
| `DELETE /content/studies/{study_id}` | `DELETE /studies/{id}` | Delete a study + its artifacts |
| `POST /content/studies/{study_id}/export` | `POST /studies/{id}/export` | Trigger Pandoc/Anki export (pure file conversion, no LLM) |

### `agents.py` — AI / pipeline

**Existing endpoints to move:**

| New path | Source endpoint | What it does |
|---|---|---|
| `POST /agents/studies/resolve` | `POST /studies/resolve` | Scope classifier + LLM disambiguation |
| `POST /agents/studies` | `POST /studies` | Trigger full KD LangGraph pipeline |

**Per-node debug endpoints (NEW — implemented in phase 2):**

| New path | What it does |
|---|---|
| `POST /agents/studies/{id}/ingest` | Run only the ingest node (crawl+cache) |
| `POST /agents/studies/{id}/plan` | Run only the planner (map-reduce via `Send()`) |
| `POST /agents/studies/{id}/synth` | Run all chapters' synth in parallel |
| `POST /agents/studies/{id}/synth/{n}` | Run ONE chapter's synth (full Self-Refine loop) |
| `POST /agents/studies/{id}/curate` | Run only the curator (style normalization) |
| `POST /agents/studies/{id}/critic` | Run only the critic (RAGAS-style verification) |
| `POST /agents/studies/{id}/assemble` | Run only the assembler (summary.md + DEBT.md) |

Each per-node endpoint is a thin wrapper around the existing `KnowledgeDistillerGraph` method. Preconditions:
- `/plan` requires ingest done (cache HIT or explicit `/ingest` POST)
- `/synth` requires plan done
- `/curate` requires synth done
- `/critic` requires curate done
- `/assemble` requires critic done

Preconditions enforced by `helpers._check_study_prerequisites()` returning 409 with a clear message if violated.

### Why `/export` is in content.py (not agents.py)

- Export uses Pandoc (Markdown → PDF/HTML/EPUB) and `genanki` (.apkg builder)
- Zero LLM calls; it's a file-format conversion
- Matches YouTube's split where `/videos`, `/channel`, `/playlist` (extract + store, no LLM in handler) live in `content.py`

## `helpers.py` contents (extracted from current `distiller.py`)

Move these utilities from `distiller.py` to `helpers.py`:

- `_make_study_root(user_id, framework, version, level)` — folder-unified path builder (no timestamp)
- `_study_key(study_id)` → `"coelhonexus:knowledge:study:{id}"`
- `_save_study_record(redis_aio, study_id, record)` — Redis write with TTL
- `_load_study_record(redis_aio, study_id)` — Redis read
- `_build_tree(storage, study_root)` — MinIO artifact tree walker
- `_resolve_user_disambiguation(...)` — scope classifier wrapper
- `_check_study_prerequisites(state, required_node)` — precondition gate for per-node endpoints
- `STUDY_TTL_SECONDS` constant

`content.py` and `agents.py` both import from `helpers.py` — zero duplication.

## Execution modes

| Mode | How to use |
|---|---|
| **Full pipeline (production)** | `POST /agents/studies` — unchanged behavior, just new URL |
| **Per-node debug (dev)** | `POST /agents/studies/{id}/{node}` — rapid iteration, no full re-run |
| **State inspection** | `GET /content/studies/{id}` — same as before, new URL |
| **Artifact fetch** | `GET /content/studies/{id}/chapters/{n}` or `/tree` |
| **Export** | `POST /content/studies/{id}/export` — triggers Celery export task |

## Breaking changes

Every KD URL moves. Callers affected:
- Any curl scripts / smoke tests / docs examples
- The web frontend (if it calls `/api/v1/knowledge/studies` directly)
- Any external integrations

### Deprecation strategy (one release)

`distiller.py` stays as a shim for one release cycle. Each old endpoint:
```python
@router.post("/studies")
async def deprecated_post_studies(request: Request):
    return RedirectResponse(
        url = "/api/v1/knowledge/agents/studies",
        status_code = 308,  # permanent redirect, preserves method + body
    )
```

After one release with deprecation warnings in logs + docs, the shim file is deleted.

## Implementation plan

### Phase 1 — structural split (no behavior change)

1. Create `apps/fastapi/routers/v1/knowledge/helpers.py` — move shared utilities out of `distiller.py`
2. Create `apps/fastapi/routers/v1/knowledge/content.py` — non-LLM endpoints, import helpers
3. Create `apps/fastapi/routers/v1/knowledge/agents.py` — 2 existing LLM endpoints (`/resolve`, POST `/studies`), stubs for 7 per-node endpoints that `raise HTTPException(501, "not implemented yet")`
4. Update `apps/fastapi/app.py` — mount both routers at new prefixes
5. Convert `distiller.py` to a deprecation shim (308 redirects)
6. Update `docs/IMPLEMENTATION-PLAN-KNOWLEDGE-DISTILLER.md` — note the split
7. Create `docs/KNOWLEDGE-DISTILLER-TESTS.md` (mirror of `AGENTIC-RAG-TESTS.md`) — curl examples for each endpoint

**Test:** all existing KD smoke tests pass with new URLs. Old URLs 308-redirect correctly.

### Phase 2 — implement per-node debug endpoints

One endpoint at a time, each ~30 LoC. Order:

1. `POST /agents/studies/{id}/ingest` — easiest, existing cache layer already supports it
2. `POST /agents/studies/{id}/plan` — reads ingest cache, runs map-reduce planner
3. `POST /agents/studies/{id}/synth` (all chapters, parallel)
4. `POST /agents/studies/{id}/synth/{n}` (one chapter — highest debug value)
5. `POST /agents/studies/{id}/curate`
6. `POST /agents/studies/{id}/critic`
7. `POST /agents/studies/{id}/assemble`

Each endpoint:
- Loads study state from Redis
- Checks prerequisites (`helpers._check_study_prerequisites`)
- Invokes the existing `KnowledgeDistillerGraph.{node}` method directly
- Saves updated state back to Redis
- Returns structured JSON with the node's output

### Phase 3 — remove deprecation shim

After one release where logs confirm no traffic on old paths:
- Delete `distiller.py`
- Remove shim entries from `app.py`

## Estimated effort

| Phase | LoC delta | Time |
|---|---|---|
| Phase 1 (split) | +200 net new (helpers + routers), ~600 relocated from distiller.py | ~1-2 hours |
| Phase 2 (per-node endpoints) | +30-50 per endpoint × 7 = ~250 | ~3-5 hours |
| Phase 3 (shim removal) | -150 deleted | ~15 min |
| **Total** | ~+700 net | **4-7 hours** |

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Breaking existing callers during deployment | 308 redirects in `distiller.py` shim for one release |
| Per-node endpoint preconditions drift from graph behavior | Reuse existing `KnowledgeDistillerGraph.{node}` methods verbatim — no re-implementation |
| State corruption if two nodes run on same study concurrently | Add study-level Redis lock (`SET NX` on `coelhonexus:knowledge:lock:{id}` with short TTL) before each per-node POST |
| Helpers.py bloat (2,108 LoC in youtube version) | Keep KD helpers focused on state+tree+preconditions; don't migrate graph internals into helpers |
| Developer confusion over which file hosts what | Clear docstring at top of each file stating "LLM/pipeline" vs "data/storage" |

## Why this is the right shape

- **Matches YouTube pattern** — one less cognitive layout to memorize; dev intuition transfers
- **Explicit AI boundary** — anyone touching `agents.py` knows they're in LLM-cost / latency / fallback-chain territory
- **Per-node debug is natural** — all per-node endpoints are LLM-heavy, they belong in `agents.py` with the pipeline trigger
- **Data layer stays simple** — `content.py` is the stable, fast path for reads/exports/deletes
- **Each endpoint independently testable** — matches the user's goal of debugging each node in isolation rather than running full 2-hour pipelines

## What this does NOT change

- LangGraph compiled pipeline stays intact (full-pipeline mode still works via `POST /agents/studies`)
- Cache layer unchanged — every node still writes to `_cache/ingestion/...`, `_cache/planning/...`, `_cache/synthesis/...`
- Pydantic schemas unchanged
- Celery task structure unchanged
- `KnowledgeDistillerGraph` class and its node methods unchanged

Only the HTTP surface is reshuffled, and new per-node endpoints added.

## Files to be created / modified / deleted

### Created
- `apps/fastapi/routers/v1/knowledge/helpers.py`
- `apps/fastapi/routers/v1/knowledge/content.py`
- `apps/fastapi/routers/v1/knowledge/agents.py`
- `docs/KNOWLEDGE-DISTILLER-TESTS.md`

### Modified
- `apps/fastapi/app.py` — add the two `include_router` calls, remove the single `distiller.router` include
- `docs/IMPLEMENTATION-PLAN-KNOWLEDGE-DISTILLER.md` — note the split
- `docs/KNOWLEDGE-DISTILLER-ARCHITECTURE.md` — update endpoint list

### Deleted (phase 3)
- `apps/fastapi/routers/v1/knowledge/distiller.py` — after shim deprecation

## Approval checklist before implementation

- [ ] User approves this plan
- [ ] Is the URL prefix change acceptable? (`/api/v1/knowledge/agents/...` + `/api/v1/knowledge/content/...` vs current `/api/v1/knowledge/...`)
- [ ] Is 308-redirect deprecation preferred over hard-cutover?
- [ ] Any endpoint whose placement (content vs agents) should differ from the proposal above?
- [ ] Is the per-node endpoint naming (`/ingest`, `/plan`, `/synth`, etc.) acceptable?

Once checkbox-approved, execution follows the Phase 1→2→3 plan above.
