# Code Organization SOTA — FastAPI + FastHTML (2026-05-20)

**Purpose.** Reference findings gathered *before* reorganizing `apps/fastapi` and
`apps/fasthtml` into a clean, repeatable structure for future projects. Captures:
(1) how the current app is organized, (2) how the deprecated app was organized and
what already changed, (3) 2026 SOTA for FastAPI + FastHTML layout, (4) the gap, and
(5) a recommended target structure + migration order + open decisions.

> Scope: **organization only** (folders/files/imports/boundaries). Not behavior, not
> algorithms. No code was changed producing this document.

---

## 0. TL;DR — the decisions this doc supports

- **FastAPI → invert from type-first to domain-first.** Today the top level is
  *types* (`routers/`, `services/`, `tasks/`) each split by domain. SOTA for a 3+
  domain app is the opposite: top-level **domain packages** (`docs_distiller/`,
  `llm/`, `youtube/`) each owning `router/schemas/service/tasks/...`, with
  cross-cutting infra in `core/` + `shared/` and the versioned surface in `api/v1/`.
  Same files — the import graph just becomes local.
- **FastHTML → keep feature-based; formalize it.** The current `features/<name>.py`
  + `register(rt)` + `shell.py`/`proxy.py` split is already idiomatic and ahead of
  most public examples. Upgrade: define `fast_app()` **once** in an `app.py` to kill
  circular-import risk, promote each `features/<name>.py` to a `features/<name>/`
  package, and adopt the official `APIRouter(prefix=).to_app(app)` mechanism.
- **Both apps already did the hard part** in the move off `zdeprecated/`: god-files
  split, lean assembly entrypoints, domain grouping, LangGraph node/library split.
  The remaining work is mostly *finishing the inversion* + filling SOTA gaps
  (central settings, tests, exceptions/constants seams).
- **Top gaps to close:** no central `pydantic-settings` (raw `os.environ` in 16
  FastAPI files), **no `tests/` at all**, no `exceptions.py`/`constants.py` seams,
  `llm/chain.py` (1844) and `static/js/docs_distiller.js` (5795) monoliths.
- **Execution (DECIDED 2026-05-20):** *port, don't rewrite* into a fresh `apps/`
  (current → `apps_/` reference); **flat layout** — FastAPI domains under
  `domains/{docs_distiller,llm,youtube}` (infra `core/ shared/ api/` + `app.py`/`celery_app.py`
  at root), FastHTML under `features/`; on `master` (no branch). **Docs Distiller is the priority
  feature**; dependency-correct
  order is **core → llm → docs_distiller** (DD's nodes call the `llm` rotator; `llm` is a
  clean leaf), with **youtube deferred**. FastAPI then FastHTML. Tree §6, plan §7, decisions §8.
- **FastHTML↔FastAPI = server-side BFF (security):** browser ↔ FastHTML only; FastHTML calls
  FastAPI server-side with an internal credential; FastAPI stays private (ClusterIP). Drops the
  wildcard `/api` proxy + wildcard CORS. Full pattern + control checklist in §9.

---

## 1. Current structure — `apps/` (38,661 LoC)

### 1.1 FastAPI (`apps/fastapi/`) — *type-first, domain-nested*

```
apps/fastapi/
  app.py                  # 140 — lean assembly: lifespan (otel/MinIO/PG checkpointer) + CORS + 3 includes
  celery_app.py           # 86  — Celery shell, env-scoped queues, conf.include discovery, worker-init MinIO
  pyproject.toml          # per-app, Python 3.13, uv; litellm pinned (supply-chain notes)
  Dockerfile.fastapi
  files/sources.yaml      # 755 — framework catalog (data asset)
  routers/
    v1/
      docs_distiller/     # __init__.py aggregates 6 sub-routers via include_router(prefix=)
        resolver.py runs.py ingestion.py debug.py planner.py(633) synth.py(1076)
      llm/health.py
      youtube/__init__.py # 138 — endpoints INLINE (deliberate "split when it grows")
  services/
    docs_distiller/
      ingestion/          # crawler tiers 1-5 + dispatch/extract/filters/post/progress/seeder/snapshot/store/storage_minio/cdp
      planner/            # graph.py state.py checkpoint.py cancel.py progress.py observability/  + nodes/{8 nodes}
      synth/              # graph.py state.py cancel.py progress.py observability/ + nodes/{6} + pure-lib siblings
    llm/                  # chain.py(1844) benchmarks.py(956) pareto_bandit.py(835) discovery.py otel_setup.py otel_metrics.py pareto_drift.py
    youtube/              # ingestion.py rag.py search.py store.py
  tasks/
    docs_distiller/ingestion.py   # Celery tasks
```

**Conventions that are genuinely good (keep these):**

- **Lean assembly entrypoint.** `app.py` only wires lifespan + CORS + 3 domain
  router includes under `/api/v1/{docs-distiller,llm,youtube}`. Services own their
  own clients/config (e.g. `storage_minio.get_storage()`, planner `checkpoint`),
  *not* `app.state` god-object.
- **Nested router composition.** Each domain's `routers/v1/<domain>/__init__.py`
  builds one `APIRouter` from sub-routers with `include_router(prefix=...)`.
- **Repeatable LangGraph pipeline shape** (planner & synth identical):
  `graph.py` (build/compile + `NODE_ORDER`/`NODE_REGISTRY`/`NODE_TO_FIELD`/`IMPLEMENTED`
  registries; incremental-rollout gating) · `state.py` (TypedDict, one field per node) ·
  `nodes/<node>.py` · `checkpoint.py` · `cancel.py` · `progress.py` (SSE) ·
  `observability/spans.py`.
- **Node vs pure-library split (synth).** `synth/nodes/outline_sdp.py` = graph
  adapter (I/O, LLM calls, MinIO caching, state patch); `synth/outline.py` = pure
  library (Pydantic schemas + DAG primitives + prompt templates + validators, **no
  I/O**). Excellent, intentional, and worth generalizing.

**Organizational gaps (vs §3 SOTA):**

| Gap | Evidence |
|---|---|
| No central config / settings | `os.environ`/`getenv` in **16** files; no `BaseSettings`, no `config.py`/`settings.py` |
| No dedicated schemas seam | **39** `BaseModel` defs scattered (mostly `synth/*` structured-output + `runs.py` + youtube router) |
| No `dependencies.py` / `exceptions.py` / `constants.py` | none found anywhere in `apps/` |
| **No `tests/`** | none in either app |
| Type-first top level | `routers/`, `services/`, `tasks/` each re-split by domain → cross-tree imports |
| Residual large files | `llm/chain.py` 1844 · `synth/nodes/sawc_write.py` 1045 · `synth/sawc.py` 977 · `synth/digest.py` 759 |
| Inconsistent router granularity | `docs_distiller` split into 6 files; `youtube` still one 138-line `__init__.py` (deliberate, documented) |

### 1.2 FastHTML (`apps/fasthtml/`) — *feature-based, `register(rt)` callback*

```
apps/fasthtml/
  main.py     # 44  — assembly only: fast_app(pico/htmx/default_hdrs=False) + Mount /static + register() each module
  shell.py    # 115 — HEAD (CDN: fonts/marked/highlight.js/cytoscape/dagre) + _Shell chrome + FEATURES nav list
  proxy.py    # 128 — /api/{path} reverse proxy → FastAPI; separate no-read-timeout SSE client
  routes.py   # 18  — misc non-feature routes (coming-soon, health)
  features/
    home.py(211)  docs_distiller.py(604)  youtube_content_search.py(380)   # each: register(rt) + FastTags + server-side fetch
  static/
    css/app.css(2639)                       # single hand-written global stylesheet (no build step)
    js/docs_distiller.js(5795)  js/youtube_content_search.js(316)          # per-feature JS
  Dockerfile.fasthtml  pyproject.toml
```

**Good (keep):** pure-assembly `main.py`; `register(rt)` callback avoids import-time
side effects + circular imports; each feature co-locates routes + FastTags + server-
side fetch; shared chrome in `shell.py`; backend access centralized in `proxy.py`;
CSS/JS externalized (cache + IDE tooling) instead of inline Python strings.

**Gaps:** `fast_app()` lives in `main.py` (no standalone `app.py` seam — minor
circular-import risk as features grow); features are single files, not packages;
uses a bespoke `register(rt)` convention instead of the official `APIRouter`;
`docs_distiller.js` is a 5795-line monolith.

---

## 2. Deprecated structure — `zdeprecated/apps/` (43,439 LoC) + deltas already made

### 2.1 Old FastAPI — *type-first layers, monolithic files*

```
zdeprecated/apps/fastapi/
  app.py(381)        # ALL config consts + every client (redis/ES/qdrant/neo4j/PG) built in lifespan onto app.state
  agents/            # empty
  graphs/            # knowledge/{distiller 3300, helpers 3429, classical_map, hierarchical_synth, preview, reduce_cluster}, youtube/{adaptive,rag,helpers}
  schemas/           # knowledge/{agents,ingestion,inputs,prompts 985,resolver,state}, youtube/{...}   ← prompts treated as schemas
  services/          # FLAT llm files (llm_chain 1508, benchmarks, discovery, otel_*, pareto_*) + knowledge/(22 files) + resolver/ + youtube/(9)
  routers/v1/        # admin/rotator, knowledge/{debug 1228,distiller 1474,ingestion,inspect,resolve}, tasks.py, youtube/{agents,content,helpers 2108}
  tasks/             # knowledge/{distiller,ingestion}, youtube/{crawler,neo4j,pipeline,qdrant}
```

### 2.2 Old FastHTML — *type-first: components + routes + services*

```
zdeprecated/apps/fasthtml/
  main.py            # fast_app() + app.mount("/static") + router.to_app(app)  (used FastHTML APIRouter `ar`)
  components/        # base, home, sidebar, kd_inspect, kd_observability 1142, kd_studies 519, map_compare
  routes/            # home.py, kd.py  (ar = APIRouter(); @ar(...); ar.to_app(app))
  services/          # fastapi_client.py (reverse proxy)
  static/css/        # input.css + main.css (Tailwind/DaisyUI BUILD step) + tailwind.config.js + sw.js (PWA)
```

### 2.3 Deltas already made (deprecated → current) — the migration trend

| Dimension | Deprecated | Current |
|---|---|---|
| FastAPI top-level | type layers `graphs/ schemas/ services/ agents/ tasks/` | domain-nested-in-type `services/<domain>/<pipeline>/` |
| File size | god-files (helpers 3429, distiller 3300, youtube helpers 2108) | split into `nodes/` + libs (a few 700–1800 remain) |
| Schemas | dedicated `schemas/` (incl. `prompts.py` 985) | co-located near use (no central dir) |
| Config/clients | all in `app.py` → `app.state` | services own clients; `app.py` lean (381→140) |
| LLM services | flat at `services/` root | grouped `services/llm/` |
| FastHTML | `components/` + `routes/` + `APIRouter.to_app` | `features/<name>.py` + `register(rt)` co-location |
| Frontend CSS | Tailwind/DaisyUI build + PWA | single hand-written `app.css`, no build, no PWA |

> Net: the project already moved **toward** domain/feature grouping and away from
> god-files. The reorg below *completes* that direction rather than reversing it.

---

## 3. SOTA — FastAPI organization (2026)

**Consensus:** for a large multi-domain app, **domain-first ("vertical slice")** is
the baseline and **hybrid** (domain packages for business code + `core/`/`shared/`
for infra) is the pragmatic production default. Type-first (`api/`, `services/`,
`schemas/` as top level) is endorsed only for small services and "doesn't scale to
many domains." Official FastAPI "Bigger Applications" is the *floor*, not the target —
it explicitly imposes no structure. The canonical references are
`zhanymkanov/fastapi-best-practices` and Netflix **Dispatch**.

**Recommended tree (hybrid + explicit v1 seam):**

```
src/ (or apps/fastapi/)
  main.py                 # create_app() factory + lifespan + mount api router ONLY
  core/                   # cross-cutting INFRA, no business logic
    config.py             # global pydantic-settings BaseSettings; get_settings() @lru_cache
    celery_app.py         # one Celery() instance + autodiscover_tasks
    telemetry.py logging.py exceptions.py(base + handlers) database.py middleware.py
  shared/                 # domain-agnostic helpers: schemas.py (base/pagination), utils.py
  api/v1/                 # VERSIONED SURFACE — composition only
    router.py             # api_v1 = APIRouter(prefix="/v1"); includes domain routers
    docs_distiller.py llm.py youtube.py   # thin adapters mounting each domain router
  docs_distiller/         # ── DOMAIN PACKAGE (version-agnostic business code) ──
    router.py schemas.py service.py dependencies.py tasks.py constants.py exceptions.py
    crawlers/  planner/(graph.py state.py nodes/)  synth/(graph.py state.py nodes/)
  llm/      { router, schemas, service, tasks, rotation.py, pareto_bandit.py, benchmarks.py, clients/ }
  youtube/  { router, schemas, service, tasks, rag.py }
tests/                    # mirrors src/: tests/<domain>/{unit,integration} + conftest.py per level
```

**Per-concern rules:**

| Concern | Location | Rule |
|---|---|---|
| Endpoints | `<domain>/router.py`, mounted by `api/v1/<domain>.py` | thin: validate → call service → return; no business logic |
| Pydantic schemas | `<domain>/schemas.py` | request+response together; **don't** pre-split into req/resp files |
| Business logic | `<domain>/service.py` | testable without HTTP; router→service→models, never reverse |
| ORM models | `<domain>/models.py`; base in `shared/` | keep separate from Pydantic schemas |
| Dependencies (DI) | `<domain>/dependencies.py`; global in `core/` | prefer `async` deps; chain to dedupe |
| Config | `core/config.py` `BaseSettings` + `@lru_cache`; optional `<domain>/config.py` | read env once |
| Exceptions | `<domain>/exceptions.py` subclass `core/exceptions.py`; register handlers globally | |
| Constants/enums | `<domain>/constants.py` | |
| Celery | one `Celery()` in `core/`; tasks in `<domain>/tasks.py` co-located | autodiscover; Celery (not `BackgroundTasks`) for retried/heavy work |
| External clients | `<domain>/clients/<provider>.py` | Dispatch `client.py` convention (fits `llm` rotators) |
| LangGraph graphs | `<domain>/<graph>/` sub-pkg: `graph.py`+`state.py`+`nodes/` | one module per node |
| `main.py` | factory + lifespan + router include only | no logic |

**Versioning:** version only the *surface* (`api/v1/`); keep schemas/services/models
version-agnostic in the domain package. Option A (recommended): domain owns
`router.py`, `api/v1/<domain>.py` is a thin mount. Option B: endpoints live in
`api/v1/<domain>.py`, domain stays router-free (simpler but splits the domain again).

**Do:** group by domain first; thin routers; `create_app()` factory; `@lru_cache`
settings; `dependency_overrides` for tests; mirror `src/` in `tests/`; promote to
`core`/`shared` only when 2+ domains need it.
**Don't:** keep type-first at scale; business logic in routers/`main.py`; mix ORM +
Pydantic; pre-split schemas; `async def` over blocking I/O; long jobs in
`BackgroundTasks`; over-engineer a repository/4-layer DDD split before it's needed.

**Sources:** [FastAPI Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) ·
[FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/) ·
[fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) ·
[Netflix Dispatch](https://github.com/Netflix/dispatch) ·
[Production FastAPI Structure 2026 (DEV)](https://dev.to/thesius_code_7a136ae718b7/production-ready-fastapi-project-structure-2026-guide-b1g) ·
[Zestminds — hybrid is safest](https://www.zestminds.com/blog/fastapi-project-structure/) ·
[FastAPI-boilerplate (benavlabs)](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/project-structure/) ·
[Celery+FastAPI app factory (TestDriven)](https://testdriven.io/courses/fastapi-celery/app-factory/) ·
[Versioned APIs in FastAPI](https://dev.turmansolutions.ai/2025/08/04/implementing-versioned-apis-in-fastapi-structure-for-flexibility-and-reusability/)

---

## 4. SOTA — FastHTML organization (2026)

**Honest framing:** FastHTML (Answer.AI, Aug 2024) has **no official recommended
layout** — its philosophy is "a single file is all you need," and all official
examples are deliberately flat. So consensus is *emerging*, not settled. Three tiers
of evidence: official **mechanisms** (`APIRouter`, `Mount`, `fast_app(static_path=)`,
`hdrs`) → official **example conventions** (flat page-modules + `components.py`) →
**community** vertical-slice patterns (no canonical blueprint exists).

**Official multi-file mechanism = `APIRouter`** (added 2024 for exactly this):

```python
# features/products/routes.py
ar = APIRouter(prefix="/products")
@ar("/all")
def all_products(req): ...
# app.py
app, rt = fast_app(hdrs=HEAD)      # defined ONCE here
# main.py
from app import app
from features.products.routes import ar as products_ar
products_ar.to_app(app)            # blueprint-style registration
```

Other documented options: the **import-app pattern** (`app, rt` in `app.py`; route
modules `from app import rt`; `main.py` imports them for side-effect — this is what
the current `register(rt)` convention emulates) and **`Mount`** sub-apps (reserve for
features needing isolated `hdrs`/middleware/auth).

**Recommended tree (feature-based — smallest delta from current):**

```
apps/fasthtml/
  main.py        # import app; import each feature's `ar`; ar.to_app(app); serve()
  app.py         # app, rt = fast_app(hdrs=HEAD, ...) — defined ONCE (kills circular imports)
  shell.py       # page_layout() shell + shared HEAD hdrs tuple
  proxy.py       # backend HTTP client / reverse proxy (existing)
  settings.py    # backend base URL, env, flags
  features/
    home/        { __init__.py exposes ar, routes.py, components.py }
    docs_distiller/ { routes.py (ar=APIRouter(prefix="/docs-distiller")), components.py, service.py }
    youtube_content_search/ { routes.py, components.py }
  shared/        { components.py (nav/footer/buttons), htmx.py }
  static/
    css/app.css                       # ONE shared stylesheet, injected via hdrs
    js/<feature>.js                   # per-feature JS, loaded LAZILY by that feature's page (Script(src=...)), not globally
```

**Do:** define `fast_app()` once in `app.py`; `APIRouter(prefix=).to_app(app)` per
feature; reference routes via `fn.to(...)`/`ar.rt_funcs.fn` (not hardcoded URLs);
global CSS/theme/libs in `hdrs`; per-feature JS scoped to its page; co-locate route +
FastTags (+ optional service); name important routes.
**Don't:** expect/await an official scaffold or over-nest (≤3 levels); `@app.route` in
modules also imported by `app.py` (circular-import trap — use `APIRouter`); `Mount`
for ordinary feature splits; one global JS bundle; confuse FastHTML `APIRouter`
(`.to_app()`) with FastAPI's `include_router`.

**MonsterUI** (optional, Answer.AI's Tailwind/FrankenUI lib): flat import
`from monsterui.all import *`; centralizes theme into `hdrs` via
`Theme.blue.headers(...)`; no folder impact; would shrink hand-written `app.css`.

**Sources:** [FastHTML Routes](https://www.fastht.ml/docs/explains/routes.html) ·
[FastHTML Handlers/APIRouter](https://www.fastht.ml/docs/ref/handlers.html) ·
[AnswerDotAI/fasthtml](https://github.com/AnswerDotAI/fasthtml) ·
[fh-about (canonical multi-page)](https://github.com/AnswerDotAI/fh-about) ·
[fasthtml-example](https://github.com/AnswerDotAI/fasthtml-example) ·
[issue #217 — organizing routes](https://github.com/AnswerDotAI/fasthtml/issues/217) ·
[MonsterUI](https://monsterui.answer.ai/) ·
[DeepWiki — APIRouter & route org](https://deepwiki.com/AnswerDotAI/fasthtml/2.3-apirouter-and-route-organization)

---

## 5. Gap analysis — current vs SOTA (actionable)

**FastAPI**
1. Invert type-first → domain-first: promote `docs_distiller/`, `llm/`, `youtube/`
   to top-level packages owning `router/schemas/service/tasks/...`; move their
   `routers/v1/<domain>/*` into `<domain>/routers/` and add thin `api/v1/<domain>.py`.
2. Add `core/config.py` with `pydantic-settings` (+ `@lru_cache get_settings()`);
   replace scattered `os.environ` (16 files) with typed settings.
3. Add `core/` (celery_app, telemetry=otel_setup+otel_metrics, exceptions, logging)
   and `shared/` (base schemas, utils).
4. Introduce `<domain>/exceptions.py` + `<domain>/constants.py` seams.
5. Add `tests/` mirroring domains (`conftest.py` per level; unit vs integration);
   convert ad-hoc `scripts/test_*.py` into real tests.
6. Adopt `create_app()` factory in `main.py` (was `app.py`).
7. Optional: break `llm/chain.py` (1844) along its internal seams; keep node/lib
   split when splitting synth/planner libs.

**FastHTML**
1. Extract `fast_app()` into a one-line `app.py`; `main.py` becomes import+register.
2. Promote `features/<name>.py` → `features/<name>/` packages (routes + components
   + optional service).
3. Migrate `register(rt)` → official `APIRouter(prefix=).to_app(app)` (keeps the
   per-feature seam, gains prefix + type-safe route refs).
4. Add `shared/components.py` for nav/footer/shell helpers used across features.
5. Load per-feature JS lazily from each feature's page (already mostly the case).
6. Consider splitting `static/js/docs_distiller.js` (5795) by wizard step.

---

## 6. Target structure for COELHO Nexus — DECIDED

**Locked decisions (this session):** *port, don't rewrite* (`git mv` working files +
fix imports; re-author only files chosen for redesign). Rename current `apps/` →
`apps_/` as a read-only reference (like `zdeprecated/`) and build a fresh `apps/`.
Work directly on `master` (commit at each verified checkpoint = restore point).
**Flat layout** under `apps/<svc>/` — no wrapper package. FastAPI: infra at the root
(`core/ shared/ api/` + `app.py`/`celery_app.py`) and business domains grouped under a
**`domains/`** parent (`domains/{docs_distiller, llm, youtube}`, imported
`from domains.llm.chain import ...`). FastHTML: flat with **`features/`** (UI features).
Nesting under `domains/` also removes the bare-`llm` PyPI-collision risk (no project
wrapper needed) and keeps launch commands **unchanged** (`uvicorn app:app`,
`celery -A celery_app`). **Original filenames are kept** so the port stays a clean move;
cosmetic renames (`chain.py → rotation.py`, `otel_setup.py → telemetry.py`) are optional
polish for later.

**Preserved (already good):** lean assembly entrypoints; the LangGraph
`graph.py`/`state.py`/`nodes/` shape; the synth **node (I/O adapter) vs pure-library**
split; incremental-rollout `IMPLEMENTED` gating. (`proxy.py` is *not* preserved — replaced
by the BFF `api_client.py`, see §9.)

### 6.1 `apps/fastapi/`  (uvicorn target → `app:app` — unchanged)

```
apps/fastapi/
├── app.py                              # FastAPI entry: create_app()/lifespan + include api router   → uvicorn app:app
├── celery_app.py                       # Celery entry: conf.include=["domains.docs_distiller.tasks"]  → celery -A celery_app
├── pyproject.toml
├── Dockerfile.fastapi
├── entrypoint.sh
├── tests/                              # (new) mirrors domains: conftest.py + docs_distiller/ llm/ youtube/ (unit/ integration/)
│
├── core/                               # cross-cutting infra (no business logic)
│   ├── config.py                       # (new) pydantic-settings: Redis/MinIO/Postgres/LLM/OTel + get_settings() @lru_cache
│   ├── otel_setup.py                   # was services/llm/otel_setup.py (app-wide instrumentation)
│   ├── logging.py                      # (new) basicConfig lifted out of app.py
│   └── exceptions.py                   # (new) base exceptions + handlers
│
├── shared/                             # domain-agnostic helpers (thin at first)
│   ├── schemas.py                      # (new) base/pagination models
│   └── utils.py                        # (new)
│
├── api/v1/
│   └── router.py                       # APIRouter(prefix="/v1"); includes the 3 domain routers w/ prefix+tags
│
└── domains/                            # ═══ business domains (bounded contexts) ═══
    ├── docs_distiller/
    │   ├── routers/                    # was routers/v1/docs_distiller/
    │   │   ├── __init__.py             #   aggregates 6 sub-routers → exposes `router`
    │   │   └── resolver.py  runs.py  ingestion.py  debug.py  planner.py  synth.py
    │   ├── schemas.py  constants.py  exceptions.py  dependencies.py    # (new seams)
    │   ├── tasks.py                    # was tasks/docs_distiller/ingestion.py
    │   ├── files/sources.yaml          # was files/sources.yaml
    │   ├── ingestion/                  # was services/docs_distiller/ingestion/
    │   │   ├── dispatch.py extract.py filters.py post.py progress.py seeder.py
    │   │   │   snapshot.py store.py storage_minio.py cdp.py playwright_crawl.py
    │   │   └── tiers/  tier1_llms_full.py … tier5_github.py            # (grouped)
    │   ├── planner/                    # was services/docs_distiller/planner/  (LangGraph)
    │   │   ├── graph.py state.py checkpoint.py cancel.py progress.py
    │   │   ├── observability/spans.py
    │   │   └── nodes/  corpus_load embed_corpus off_topic cluster refine label reduce plan_write
    │   └── synth/                      # was services/docs_distiller/synth/  (LangGraph)
    │       ├── graph.py state.py cancel.py progress.py
    │       ├── observability/spans.py
    │       ├── nodes/   outline_sdp digest_construct sawc_write checklist_eval mgsr_replan render_audit_write   ← graph adapters (I/O)
    │       └── outline.py digest.py sawc.py checklist.py mgsr.py render.py corpus_normalize.py vault.py backfill.py   ← pure libs
    │
    ├── llm/
    │   ├── router.py                   # was routers/v1/llm/health.py
    │   ├── schemas.py                  # (new)
    │   └── chain.py  pareto_bandit.py  pareto_drift.py  benchmarks.py  discovery.py  otel_metrics.py   # was services/llm/*
    │
    └── youtube/
        ├── router.py                   # was routers/v1/youtube/__init__.py (inline OK until it grows)
        ├── schemas.py                  # RunRequest/SearchRequest (were inline in the router)
        └── rag.py  search.py  ingestion.py  store.py     # was services/youtube/*
```

Imports read `from domains.llm.chain import …`, `from core.config import …`,
`from api.v1.router import …`. `app.py` + `celery_app.py` stay at the root, so
`uvicorn app:app` and `celery -A celery_app` are **unchanged** at cutover.

### 6.2 `apps/fasthtml/`  (serve target → `main` — unchanged)

FastHTML groups by **`features/`** (UI features), not `domains/` — `domains/` is the
FastAPI (backend) grouping. Also flat (no wrapper package).

```
apps/fasthtml/
├── main.py                             # entry: fast_app() + register feature routers (ar.to_app) + serve()
├── settings.py                         # (new) FASTAPI_URL / env / flags
├── shell.py                            # _Shell chrome + HEAD hdrs + FEATURES nav
├── api_client.py                       # server-side httpx → FastAPI w/ internal credential — BFF (§9); replaces proxy.py
├── routes.py                           # system routes (health) + ONE scoped SSE-relay route; NO wildcard /api passthrough
├── pyproject.toml
├── Dockerfile.fasthtml
├── entrypoint.sh
├── features/                           # vertical slices (each a package)
│   ├── home/                    routes.py  components.py
│   ├── docs_distiller/          routes.py  components.py  service.py
│   └── youtube_content_search/  routes.py  components.py
├── shared/
│   ├── components.py                   # nav/topbar/brand (extracted from shell.py)
│   └── htmx.py
└── static/                             # served from disk
    ├── css/app.css
    └── js/  docs_distiller.js  youtube_content_search.js
```

Every package dir gets an `__init__.py`. With the `register(rt)`→`APIRouter` pattern,
feature modules don't import the app, so no separate `app.py` is needed to dodge circular
imports. Launch stays unchanged; at cutover only the build context in `skaffold.yaml` /
Dockerfiles points back at the new `apps/` (§7 step 6).

---

## 7. Porting plan (decided — Docs Distiller prioritized)

Approach: fresh tree + **port** (§6). On `master`, no branch; `apps_/` is the reference.
**Dependency reality (verified 2026-05-20):** Docs Distiller is *not* an island — 9
planner/synth nodes import the LLM rotator (`services.llm.chain` →
`chat_judge_bandit_async`, `embed_via_router_async`) and tracing
(`services.llm.otel_setup` → `get_tracer`), and its Celery task imports root
`celery_app`. `llm` is a **clean leaf** (imports nothing else in the app). So the
dependency-correct path to a working DD is **core → llm → docs_distiller**. **youtube is
deferred** (not a current priority; it also depends on `llm`, so nothing is lost).

0. ✓ `git mv apps apps_ && mkdir apps` (done) — fresh tree under `apps/`.
1. ✓ **Scaffold + core.** Create the `apps/fastapi/` skeleton (`core/ shared/ api/v1/ domains/`);
   `git mv` `otel_setup.py → core/otel_setup.py`; keep `app.py` + `celery_app.py` at the root;
   add `core/config.py` (pydantic-settings), `core/logging.py`, `core/exceptions.py`,
   `api/v1/router.py`.
2. ✓ **Port `llm`** (clean leaf): whole `services/llm/` → `domains/llm/` (chain, pareto_bandit,
   pareto_drift, discovery, benchmarks, otel_metrics) + `routers/v1/llm/health.py →
   domains/llm/router.py`. Fix imports: `services.llm.X → domains.llm.X`,
   `services.llm.otel_setup → core.otel_setup`. Verify llm health locally. Commit.
3. **Port `docs_distiller`** (the main feature): all files → `domains/docs_distiller/`
   (`routers/` + `ingestion/`[+`tiers/`] + `planner/` + `synth/` + `tasks.py` +
   `files/sources.yaml`). Fix imports: internal `services.docs_distiller.* →
   domains.docs_distiller.*` (or relative); `services.llm.chain → domains.llm.chain`;
   `services.llm.otel_setup → core.otel_setup`; update `celery_app.py`'s `conf.include` →
   `["domains.docs_distiller.tasks"]`. Verify resolver→ingestion→planner→synth locally
   (`uvicorn app:app`). Commit.
4. **FastAPI finish:** wire `celery_app.py` autodiscover; migrate scattered `os.environ` →
   `core/config.py`; add `tests/` (+ port `scripts/test_*.py`).
5. **FastHTML (re-architect as a server-side BFF — see §9):** build `main.py` (`fast_app()`
   once + register features); add `api_client.py` (server-side httpx + internal credential);
   feature packages return HTML fragments via HTMX; one scoped SSE-relay route; `register(rt)`
   → `APIRouter(prefix=).to_app(app)`. Do **not** recreate `proxy.py`'s wildcard `/api`
   passthrough or port the client-`fetch` wizard wholesale.
6. **Cutover:** point `skaffold.yaml` build context + `Dockerfile.*` back at the new `apps/`
   (launch commands `uvicorn app:app` / `celery -A celery_app` are **unchanged**); `skaffold dev`
   redeploy; verify. Then `apps_/` → delete or fold into `zdeprecated/`.
7. **Deferred:** port `youtube` (when back in scope); split `llm/chain.py` +
   `docs_distiller.js` (behavior-risky).

> Build/skaffold deploy is **red between the rename (0) and cutover (6)** — expected,
> since `apps_/` is reference-only. Verify intermediate steps by running the package
> locally (`uvicorn app:app` from `apps/fastapi`), not via skaffold.

---

## 8. Decisions — RESOLVED (2026-05-20)

1. **Layout / package root:** **flat** under `apps/<svc>/` (no wrapper package). FastAPI business
   domains grouped under a **`domains/`** parent (`domains/{docs_distiller,llm,youtube}`); infra
   (`core/ shared/ api/`) + `app.py`/`celery_app.py` at the root; FastHTML under `features/`.
   Nesting under `domains/` removes the bare-`llm` PyPI-collision risk — so no `nexus`/`src`
   wrapper, and launch stays `uvicorn app:app` / `celery -A celery_app`.
2. **Layout container:** rename `apps/` → `apps_/` + build a fresh `apps/` (port into a
   clean tree), *not* in-place mutation.
3. **Port vs rewrite:** **port** (`git mv` + fix imports); re-author a file only by
   explicit choice. Preserves git history + the documented hard-won fixes.
4. **Branch:** none — work on `master`, commit at each verified checkpoint (solo repo).
5. **Versioning:** **Option A** — each domain owns its router; `api/v1/router.py` mounts
   the three domain routers with prefix + tags.
6. **`pydantic-settings`:** adopt during the FastAPI-finish step (§7 step 5).
7. **FastHTML routing:** migrate `register(rt)` → official `APIRouter(prefix=).to_app(app)`.
8. **FastHTML↔FastAPI = server-side BFF (security):** browser ↔ FastHTML only; FastHTML ↔
   FastAPI server-side with an internal credential; FastAPI stays ClusterIP. Drop the wildcard
   `/api` proxy + wildcard CORS. Full pattern + control checklist in §9.

---

## 9. FastHTML ↔ FastAPI communication + security — DECIDED

**Decision: FastHTML is a server-side Backend-for-Frontend (BFF).** The browser talks only to
FastHTML; FastHTML calls FastAPI server-side; FastAPI is never browser-exposed. This is at once
the idiomatic FastHTML (server-rendered hypermedia) pattern and the OWASP-recommended secure-auth
pattern — they converge.

### Data flow
```
browser ──HTMX / htmx-ext-sse──▶ FastHTML routes ──HTML fragments──▶ browser
                                      │
                                      │  server-side httpx (api_client) +
                                      │  internal credential (browser never sees it)
                                      ▼
                                  FastAPI — ClusterIP, private, never browser-reachable
```

### Building blocks (FastHTML)
- `api_client.py` — ONE server-side httpx client to FastAPI (in-cluster URL); attaches the
  internal credential (service token from a K8s Secret, or mTLS).
- Feature routes return **HTML fragments** (ft components); HTMX swaps them in.
- **One scoped SSE-relay route** — connects to FastAPI's SSE and forwards to the browser via
  `htmx-ext-sse` (the deprecated app already shipped `htmx-ext-sse`). Replaces `proxy.py`'s SSE
  handling, but authenticated + named — not a wildcard.
- **No wildcard `/api/{path:path}` passthrough.** Any client→backend need = a narrow, named,
  allow-listed route (deny-by-default).

### Anti-patterns in the current code NOT carried into the reference
1. `proxy.py`'s `/api/{path:path}` wildcard reverse proxy — exposes *every* FastAPI route (incl.
   `/debug/*`) to the public browser, unauthenticated (the `<Proxy "*">` anti-pattern).
2. FastAPI `app.py`'s `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` — wildcard
   origin + credentials is invalid per spec and a hole. With a BFF the browser never calls FastAPI
   cross-origin → lock CORS down / remove it.

### Security control checklist (bake in from the start)
- [ ] Session = HttpOnly + Secure + SameSite=Lax cookie from FastHTML; **no tokens in
      `localStorage`** (one XSS leaks every token — OWASP).
- [ ] CSRF token on state-changing routes (HTMX sends it via `hx-headers` / meta tag).
- [ ] FastAPI stays **ClusterIP** — no NodePort/ingress; never browser-reachable.
- [ ] FastHTML→FastAPI carries an internal **service token (K8s Secret) or mTLS**; FastAPI verifies it.
- [ ] **NetworkPolicy:** only the FastHTML pod may reach FastAPI.
- [ ] Ingress fronts **FastHTML only**; terminates TLS + rate-limits.
- [ ] Deny-by-default route allow-list; no wildcard passthrough.

### Honest costs
- The 30-min SSE stream still needs a server-side relay route (one named, authed endpoint).
- Genuinely client-side libs stay client-side (Cytoscape DAG, highlight.js, marked) — they consume
  fragment/SSE data, never the API directly.
- Migration impact: **FastAPI domains are still a straight port** (logic unchanged); the **FastHTML
  frontend is re-architected** server-side-first — do *not* recreate `proxy.py` or port the
  5,795-line client-`fetch` wizard wholesale. Cheap now (`apps/fasthtml/` is near-empty).

### Sources
[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) ·
[BFF pattern (Auth0)](https://auth0.com/blog/the-backend-for-frontend-pattern-bff/) ·
[Stop leaking API keys — BFF (GitGuardian)](https://blog.gitguardian.com/stop-leaking-api-keys-the-backend-for-frontend-bff-pattern-explained/) ·
[Reverse-proxy security paradox](https://blog.devsecopsguides.com/p/secure-by-design-the-reverse-proxy) ·
[API allow-list / deny-by-default (Tyk)](https://tyk.io/docs/api-management/security-best-practices)
8. **Deferred (post-reorg):** MonsterUI adoption; splitting `chain.py` / `docs_distiller.js`.