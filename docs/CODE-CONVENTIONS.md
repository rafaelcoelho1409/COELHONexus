# Code organization conventions

**Status:** LIVING document. Authored 2026-06-02 from a synthesis of FastAPI
best-practices, Cosmic Python, Pydantic docs, and an audit of the current
`apps/server/domains/` tree. This is the rulebook for future modules and
the migration target for existing ones.
**Updated 2026-09-18:** added §8, the cross-module reference convention
(dotted `domains.*` path, no wrapper/namespace classes).
**Updated 2026-09-21:** `domains/llm/` (gateway + provider registry) retired;
external chat/embeddings live in `domains/settings/` (§9 tree).
**Updated 2026-11-03:** recorded a pending internal-import observation for
`ai/`, `domains/`, `entrypoints/`, and `infra/` (no code migration yet).
**Updated 2026-11-16:** clarified ownership of deterministic logic in
`ai/` node packages versus the bounded-context `domains/` package.

**Why this exists:** the project is a knowledge-demonstration codebase.
Every file name, every module split, every choice between "dataclass vs
loose constants" is a teaching surface. The conventions below are picked
so that a contributor reading the tree top-down can build a correct mental
model without opening every file.

---

## TL;DR

- **Don't use `constants.py` and `types.py`** as catch-all names. Split by
  responsibility: `prompts.py`, `params.py`, `keys.py`, `patterns.py`,
  `schemas.py`, `entities.py`, `errors.py`, `state.py`.
- **Use `@dataclass(frozen=True, slots=True)` to group ≥3 tunables that
  always change together**. Single scalars stay as module-level constants.
- **Split `service.py` into `domain.py` (pure logic) + `service.py`
  (I/O orchestration)** — the Cosmic Python "Functional Core, Imperative
  Shell" pattern. Makes the pure logic trivially testable and reads top-
  to-bottom in `service.py`.
- **Choose `ai/` versus `domains/` by ownership, not determinism.** A pure
  AI-step rule may live in that step's `domain.py`; bounded-context rules
  belong in `domains/<context>/`.
- **`node.py` stays a thin LangGraph shell** — 20–40 lines wrapping a
  single call into `service.py`.
- **Pydantic for boundary validation only** (LLM responses, HTTP bodies).
  Dataclasses for everything internal.
- **Cross-module references use a dotted `domains.*` path, unaliased**
  (`domains.bd.ingestion.params.MAX_ARTIFACT_BYTES`), not a bare
  `from .module import name` and not a wrapper/namespace class. See §8.

---

## 1. Diagnosis — what the current `constants.py` / `types.py` names hide

Examples of what catch-all names can obscure:

| File | What's actually inside | Why the name is wrong |
|---|---|---|
| `ingestion/storage/constants.py` | Key-builder **functions** (`framework_prefix()`, `manifest_key()`, `page_key()`) + a TTL scalar | These are functions, not constants. The file is really a "key shapes you must agree on" file. |
| `workflow/constants.py` | Version marker, tunables, regular expressions, and decision notes | Mixes versioning, parameters, patterns, and historical context. |
| `workflow/types.py` | Boundary schemas and internal value objects | Mixes validation at system boundaries with internal domain shapes. |
| `workflow/service.py` | Pure transformations and I/O orchestration | Pure functions are harder to test when buried alongside asynchronous I/O. |

The pattern problem: **lowest-common-denominator names hide what each
file is for, which forces every new contributor to read the file before
they can navigate to it.** For a reference project, this is exactly the
opposite of what we want.

---

## 2. New naming convention (split by responsibility)

**Status:** ENFORCED 2026-09-21 — the split below is a review rule, not a
suggestion. Every leaf module ships the **required core**
(`__init__.py` with module-only re-exports + `__all__`, `params.py`,
`service.py`) plus **exactly the conditional files whose trigger
holds** — `domain.py` included. A pure-client factory with no branches
(`infra/qdrant/service.py: get_qdrant` passes params straight through)
correctly has no `domain.py`: the trigger is "the module contains pure
logic", not "every module must have one". A file with no trigger MUST
NOT exist — an empty `errors.py` "for uniformity" is ceremony: it
costs every reader a hop and rots the moment someone defines an
exception in `service.py` because the empty file "looked unused".
Uniformity is enforced by the triggers, not by placeholder files.

### Replacement for `constants.py`

| New name | Trigger (create the file iff) | What goes in it |
|---|---|---|
| `keys.py` | the module builds ≥1 key/path/routing name consumed outside `service.py` | Key builders + path helpers (`framework_prefix(slug)`), queue names, task-path tables |
| `params.py` | REQUIRED CORE — every module has tunables or env reads | Loose numeric tunables (thresholds, concurrency, timeouts) that don't fit a dataclass group |
| `prompts.py` | the module sends ≥1 LLM prompt | LLM prompt strings + their version markers |
| `patterns.py` | the module holds ≥1 `re.compile(...)` at module scope | Pre-compiled regexes |
| `versions.py` | the module versions a schema or prompt for cache invalidation | Schema/prompt version strings (cache-invalidation knobs) |
| `config.py` | ≥3 tunables describe one concept and change together (§3) | Frozen-dataclass GROUPS of related tunables |

Reserve plain `constants.py` ONLY for a module that legitimately has 1–2
unrelated scalars and nothing else (rare). If you find yourself adding
comments to *group* scalars inside a `constants.py`, that's the signal to
split into one of the above files OR a dataclass.

### Replacement for `types.py`

| New name | Trigger (create the file iff) | What goes in it |
|---|---|---|
| `schemas.py` | the module defines ≥1 boundary shape | Shapes at system boundaries — Pydantic `BaseModel` classes for validated boundaries (LLM I/O, HTTP bodies) AND external-service schema definitions (ES index mappings, Qdrant payload shapes). If it describes what a datastore or wire format accepts, it lives here, not in `service.py` |
| `entities.py` | the module manipulates ≥1 value object, or ≥2 scalars form a frozen group | Plain value objects — the "things" the domain manipulates (`ManifestEntry`, `Section`, `MemoryEntry`). `@dataclass` by default; a `TypedDict` instead when the value is kept as a plain dict inside LangGraph state or a JSON artifact (the items of a state's lists: page references, per-page results) |
| `state.py` | the module owns LangGraph state | Only the schemas passed to `StateGraph` (state, and input/output/private schemas if the graph has them), as `TypedDict`s. Their item shapes go in `entities.py` |
| `errors.py` | the module defines ≥1 exception class | Exception classes (`IngestCancelled`, etc.) |
| inline (no file) | one-off `TypeAlias` / `Literal` | Put them in the module that uses them, no separate file needed |

Why not `models.py`? It collides semantically with SQLAlchemy/ORM
"models" — `schemas.py` (validation) + `entities.py` (domain objects)
is unambiguous and matches the FastAPI ecosystem convention.

---

## 3. Dataclass groupings — when and how

### When YES

Group ≥3 scalars into a frozen dataclass when:
1. They describe **one concept** (one feature, one subsystem, one knob),
2. They **change together** (tuning one usually means re-tuning others),
3. You'd write **a comment grouping them** if they were loose constants.

### When NO

- A lone version string → module-level constant
- A single threshold → module-level constant
- Unrelated scalars that just happen to live in the same module → keep loose

### How — the canonical shape

```python
# workflow/config.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """Limits for a compact summary and its key terms."""
    summary_chars_min: int = 100
    summary_chars_max: int = 500
    term_chars_max:    int = 48
    terms_max:         int = 10


@dataclass(frozen=True, slots=True)
class StructureConfig:
    """Output shape limits for a structured guide."""
    sections_min:          int = 2
    sections_max:          int = 8
    explanation_words_min: int = 12
    explanation_words_max: int = 80


# Module exports — single immutable instances. Importers see
# `MEMORY.summary_chars_max` instead of `_MEMORY_SUMMARY_CHARS_MAX`.
MEMORY    = MemoryConfig()
STRUCTURE = StructureConfig()
```

**Why `frozen=True, slots=True`:**
- `frozen=True` — immutable (prevents accidental mutation of "config")
- `slots=True` — no `__dict__`, smaller memory footprint, faster attribute access
- Both together turn the dataclass into a true value object

**Why ONE module-level instance** (e.g. `MEMORY = MemoryConfig()`):
- Call sites read `MEMORY.terms_max` (clear, grouped) rather than
  `MemoryConfig().terms_max` (allocates per call) or `MemoryConfig.terms_max`
  (class-attr access; works only if all fields have defaults, fragile)

### What goes INSIDE the dataclass vs OUTSIDE

This rule applies to **CONFIG / value-object dataclasses** (the
`MemoryConfig`, `StructureConfig` style above) — NOT to all dataclasses.
Domain entities (DDD aggregates) often legitimately carry behavior; see
the decision rule below.

For CONFIG dataclasses:
- Inside: **values only**. Strings, ints, frozensets, tuples.
- Outside: **all behavior**. Pure functions go in `domain.py`; I/O goes
  in `service.py`. They take the dataclass as a parameter if they need
  its values.

```python
# GOOD — function takes config as a param
def is_valid_subtopic_count(n: int, cfg: StructureConfig) -> bool:
    return cfg.subtopics_min <= n <= cfg.subtopics_max

# BAD — method on a CONFIG dataclass (mixes data + behavior)
@dataclass(frozen=True)
class StructureConfig:
    subtopics_min: int = 2
    subtopics_max: int = 6

    def validate(self, n: int) -> bool:   # don't do this
        return self.subtopics_min <= n <= self.subtopics_max
```

The Cosmic Python rationale (for config / value objects): behavior on
values makes those values harder to test (every call carries instance
state) and harder to reason about (you can't predict the output from
arguments alone — you need to know which instance was used).

### Methods that ARE always fine on a dataclass

Regardless of whether the dataclass is a config or a domain entity, the
following are idiomatic Python and the dataclass decorator doesn't
restrict any of them:

| Pattern | What it does | When to use it |
|---|---|---|
| `__post_init__(self)` | Runs AFTER auto-generated `__init__` | Validation, normalization (e.g. `self.title = self.title.strip()`) |
| `@property` | Derived value computed from fields | When a value is implied by other fields (avoid storing redundancy) |
| `@classmethod from_X(cls, ...)` | Alternative constructor / factory | `Chapter.from_dict(d)` is more discoverable than a free function |
| `@staticmethod` | Utility namespaced under the class | Pure helpers that operate on the type's domain but don't need `self` |
| `to_dict(self)` / serializers | Conversion to/from external shapes | Same discoverability argument as `from_X` factories |

### The decision rule for "should this be a method?"

**If `self` is used for more than read-only access to fields, the method
belongs on the class. If `self` only appears as
`self.field_a, self.field_b, ...`, it's a free function that takes the
dataclass as a param.**

Re-phrased two ways:

- **Config / value-object dataclasses** → values only; behavior outside
  in `domain.py` / `service.py`. (The rule above.)
- **Domain entities with invariants** → behavior on the class is fine
  and often clearer. Classic example: `Order.add_item(item)` enforces
  the invariant that line totals stay consistent — the rule belongs
  with the data it protects.

### Worked example — the five method shapes on a domain entity

```python
from dataclasses import dataclass, field


@dataclass
class Chapter:
    title: str
    sources: list[str] = field(default_factory=list)

    # 1. __post_init__ — normalization right after construction.
    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("Chapter title cannot be empty")

    # 2. Instance method — modifies state under an invariant
    #    ("a source key appears at most once per chapter").
    def add_source(self, key: str) -> None:
        if key not in self.sources:
            self.sources.append(key)

    # 3. @property — derived value, no redundant storage.
    @property
    def n_sources(self) -> int:
        return len(self.sources)

    # 4. @classmethod — alternative constructor.
    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        return cls(title=d["title"], sources=list(d.get("sources") or []))

    # 5. @staticmethod — utility namespaced under the type.
    @staticmethod
    def slug(title: str) -> str:
        return title.lower().replace(" ", "-")
```

All five are correct here because `Chapter` is a domain entity with
invariants. The same five shapes on `MemoryConfig` (a pure value
object) would be the anti-pattern described above.

---

## 4. Service-layer split — Functional Core, Imperative Shell

This is the deeper lever for readability + testability. Today most
`service.py` files mix pure logic and I/O. The Cosmic Python pattern is
to **separate them into different modules**:

```
workflow/step/
├── __init__.py
├── prompts.py        # LLM templates + version strings
├── params.py         # loose numeric tunables
├── config.py         # frozen-dataclass groups for related tunables
├── patterns.py       # pre-compiled regexes
├── schemas.py        # Pydantic schemas at external boundaries
├── entities.py       # internal value objects
├── domain.py         # PURE functions — no I/O, no LLM, no async
├── service.py        # ORCHESTRATION — async I/O, calls domain.*
├── node.py           # LangGraph node wrapper — ~30 lines, calls service.run(state)
├── state.py          # TypedDict for the LangGraph state field
└── errors.py         # exception classes
```

### What goes in `domain.py` (the "Functional Core")

Pure functions that:
- Take inputs, return outputs.
- No `async`, no I/O, no network calls, no logging, no clocks.
- No mutable globals.
- Deterministic — same inputs → same outputs.

Examples:
- `_normalize_identifier(value: str) -> str` — deterministic text normalization.
- `_overlap_score(items_a, items_b) -> float` — Jaccard-style overlap.
- `_compute_manifest_hash(...)` — deterministic SHA hash.
- `_validate_record_against_inputs(record, source)` — validation and repair-list logic.

These are the "Functional Core". They're trivial to unit-test
(no mocks, no fixtures, no event loop). They're the modules to write
property-based tests against.

### Which `domain.py` owns the pure logic?

`domain.py` and the top-level `domains/` package answer different questions.
`domain.py` describes the kind of code: deterministic functional logic.
`domains/<context>/` identifies the bounded context that owns a capability.
Purity alone does not decide where a function belongs; place it beside the
contract and policy that give the function its meaning.

| Code responsibility | Owner |
|---|---|
| Stable feature rules and data transformations that make sense outside one AI graph | `domains/<context>/<capability>/domain.py` |
| Business I/O and capability workflows | `domains/<context>/<capability>/service.py` |
| Pure logic specific to an AI step's prompt, agent schema, or model-output policy | `ai/<context>/nodes/<flow>/<step>/domain.py` |
| Model calls and graph-step orchestration, including progress, cancellation, and fan-out | `ai/<context>/nodes/<flow>/<step>/service.py` |
| LangGraph state transitions and conditional-edge routing | `ai/<context>/graphs/` (with small adapters in `node.py`) |
| External client lifecycle and provider-specific mechanics | `infra/<service>/` |

Examples: BD's processed-book heading and page-mapping rules belong in the
BD domain layer when they remain independent of agent contracts. Validation
of an agent response belongs beside the AI step that owns that response
schema. YCS video projection and transcript partition rules belong to
`domains/ycs/ingestion/`; which graph edge to take after indexing is workflow
control and belongs with the YCS ingestion graph.

AI code may call bounded-context domain services. Domain business-capability
modules must not depend on LangGraph `State`/`Runtime` types or schemas owned
by an AI agent. The documented composition roots under
`domains/<context>/<tab>/ai/` are the exception: they bind AI graphs and
infrastructure. If a pure rule becomes meaningful outside its AI step, move
it to the owning bounded context and have the AI step call it. If a
graph-specific algorithm is deterministic but changes with that step's agent
contract, keep it in the AI node package. Keep `node.py` as the LangGraph
adapter; put orchestration that outgrows a thin wrapper in the node's
`service.py`.

#### Applying the rule to Book Distiller agentic AI

Use YCS ingestion as an organizational precedent for separating AI-step
behavior from bounded-context capabilities. Do not interpret it as a rule
that all deterministic code must move to `domains/`; placement follows the
contract and policy that own the code.

- **BD:** Keep ask, distill, audit, and repair policies beside their AI nodes
  when they depend on an agent schema or output contract. Keep persistent
  ingestion state, selection revisions, and processed-book capabilities in
  the BD domain layer when they express book-ingestion behavior independently
  of a particular agent. The ingestion AI domain packages group these
  capabilities by responsibility: `content_selection/`, `repair_planning/`,
  `publication/`, and `processed_book/`. Graph `node.py` wrappers call those
  dotted domain APIs for domain-owned operations; LLM calls, agent-contract
  validation, progress, and graph runtime wiring stay in the AI node layer.
  The processed-book heading and page-mapping rules are domain logic when they
  remain independent of agent contracts.
- **BD inventory boundary follow-up:**
  `domains/bd/ingestion/ai/inventory/domain.py` imports and returns
  `ai.bd.agents.schemas` types, and its helpers are shared by the audit and
  repair nodes. This crosses the domain-to-AI dependency boundary. The
  recommended cleanup is to move the AI-contract-coupled inventory package
  (`domain.py`, `params.py`, and `patterns.py`) under a shared AI ingestion
  package such as `ai/bd/nodes/ingestion/inventory/`. This is a focused
  follow-up, not a reason to relocate all deterministic BD logic.

This records the target ownership standard. It does not imply that the BD
inventory package or other existing modules have already been moved.

### What goes in `service.py` (the "Imperative Shell")

Functions that:
- Orchestrate I/O — `async def`, `await`, MinIO reads/writes, LLM calls.
- Call multiple `domain.*` pure functions to compute things.
- Emit progress events, retry on transient errors, log warnings.
- The "glue" — short, top-to-bottom-readable, every step delegates to a
  named pure function or a named I/O call.

The benchmark: someone should be able to read `service.py` top-to-bottom
and understand the algorithm. They shouldn't have to dive into a 200-line
pure-logic block to follow the orchestration.

### What goes in `node.py`

The LangGraph wrapper. Should be ~20–40 lines. Signature is fixed by
LangGraph; the body is one call into `service.py`'s entry point + a
state-patch dict on the way out.

```python
from .service import run_step
from ..state import WorkflowState
from ..observability.spans import traced


@traced("step_run")
async def run(state: WorkflowState) -> dict:
    return await run_step(state)
```

The teaching value: `node.py` becomes a 5-second read. The architecture
(what the node does, how it does it, what state it patches) is in
`service.py` + `domain.py`, where it belongs.

---

## 5. Example module layout

Use one focused workflow step as the **pattern-establishing module**. Apply
only the files whose triggers hold, then use the resulting structure as a
reference for other modules.

### Target tree

```
workflow/step/
├── __init__.py             # narrow public API, when needed
├── prompts.py              # LLM templates + versions
├── config.py               # related tunable groups
├── params.py               # standalone tunables
├── patterns.py             # compiled regular expressions
├── versions.py             # schema and prompt versions
├── schemas.py              # boundary validation shapes
├── entities.py             # internal value objects
├── domain.py               # pure deterministic logic
├── service.py              # I/O orchestration and persistence
├── node.py                 # thin LangGraph wrapper
├── state.py                # only when the step owns state
└── errors.py               # step-specific exceptions
```

### What gets touched in import-callers

Callers import from the specific module that owns each symbol. Avoid broad
re-export barrels that hide dependency direction.

---

## 6. Migration order (lowest blast radius first)

Phase this. Don't do it all at once.

| Phase | What changes | Scope | Risk |
|---|---|---|---|
| 1 | Split `constants.py` → `prompts.py` + `params.py` + `keys.py` + `patterns.py` + `versions.py` (per module, only those that apply) | mechanical re-org, no behavior change | LOW |
| 2 | Split `types.py` → `schemas.py` + `entities.py` + `errors.py` | mechanical, no behavior change | LOW |
| 3 | Group related tunables into `@dataclass(frozen=True, slots=True)` in `config.py` | touches import sites; one `MEMORY.x` instead of `_MEMORY_X` | LOW-MEDIUM |
| 4 | Extract pure functions from `service.py` → `domain.py` | actual architectural change; testable core appears | MEDIUM |
| 5 | Shrink `node.py` to a thin LangGraph shell | trivial after phase 4 | LOW |

Each phase is **one PR per module**. A sweep across `domains/<context>/` and
`domains/settings/` is roughly 3–5 days of focused work for the whole tree.

**Recommended pilot:** apply all 5 phases to one focused step first. It
becomes the reference template for other modules.

---

## 7. Anti-patterns (what NOT to do)

- **Don't put behavior inside a dataclass.** Values only. Behavior lives
  in `domain.py` (pure) or `service.py` (I/O) and takes the dataclass as
  a parameter.
- **Don't use Pydantic `BaseModel` for internal config.** Pydantic is
  for boundary validation (LLM responses, HTTP bodies). For "these
  scalars belong together", a frozen dataclass is faster, simpler, and
  doesn't drag a heavyweight validator into hot paths.
- **Don't use `pydantic-settings` unless the value comes from env or a
  file.** Frozen dataclass is the right tool for compile-time constants;
  `pydantic-settings` is the right tool for `MINIO_ENDPOINT`-style
  runtime config (i.e. things that already live as `os.environ` reads).
- **Don't create a `models.py`.** It collides semantically with
  SQLAlchemy "models". Use `schemas.py` (validation) +
  `entities.py` (domain objects).
- **Don't keep loose decision-log comments in `constants.py`.** Move
  rationale to git commit messages or short docstrings on the dataclass.
  A code file is for code; an architecture file is for architecture.
- **Don't carry orphan helpers in `service.py`.** If a function is
  pure, move it to `domain.py`. If it's I/O, name it explicitly and
  document the side effects in the first docstring line. "Service" is
  the orchestrator, not the dumping ground.
- **Don't preserve dead modules in a `deprecated/` folder.** Git history
  is the deprecation archive; record only design rationale that remains
  useful to active contributors.
- **Don't wrap pure functions in a class to get import-path clarity**
  (`IngestionArtifactsDomain.func(...)`, or a nested `Ingestion.Artifacts.
  Domain.func(...)` tree). Every function needs a manually
  maintained `@staticmethod` mirror inside the class — permanent drift
  risk against the real module, for a problem §8's dotted `domains.*`
  path already solves for free.

---

## 8. Cross-module references and Python imports

Use Python packages and modules as namespaces. Do not create wrapper
classes whose only purpose is to add a qualified name to module functions.
Keep imports explicit at module boundaries and avoid eager package exports
that load optional infrastructure or heavyweight dependencies during app
startup.

For example, a domain-owned operation should remain in the domain package
that owns it, and callers should make that ownership clear in their imports:

```python
from domains.bd.ingestion import service as ingestion_service

await ingestion_service.run(...)
```

For code evaluated while a module is importing (decorators, module-level
registries, and default values), use a direct import of the needed symbol
when an attribute chain could cross a package that is still initializing.
Keep package initialization light, and add re-exports only when they do not
create import cycles or force unrelated runtime dependencies to load.

Heavy or environment-dependent modules should stay out of eager
`__init__.py` chains. Import them at the execution
boundary that actually needs them. This lets API startup, scripts, and
unrelated workers import the package without production secrets or optional
libraries.

---

## 9. Current project structure

```text
apps/server/
├── entrypoints/             # REST and MCP interfaces
│   ├── api/v1/{bd,ycs,settings}/
│   └── mcp/tools/
├── domains/                 # business capabilities and I/O orchestration
│   ├── bd/
│   ├── ycs/
│   └── settings/
├── ai/                      # agents, graphs, nodes, prompts, skills, and tools
│   ├── bd/
│   ├── ycs/
│   └── settings/
├── infra/                   # database, queue, storage, and observability adapters
├── main.py                  # FastAPI and FastMCP bootstrap
└── celery_app.py            # Celery bootstrap

apps/web/src/
├── app/                     # Next.js route composition
├── features/{bd,ycs,settings}/
└── shared/                  # UI, API client, state, and styles
```

Each bounded context owns its product behavior. Shared infrastructure
stays in `infra/`; AI graph construction and
execution stay in `ai/`; domain services bind those
graphs to storage, queues, credentials, and persistence.

---

## 10. The `infra/` layer — one module per COELHO Cloud service

`apps/server/infra/<service>/` is the **only** place that imports a service's client library, reads
its env vars, builds its connection string or hardcodes its names. Everything else reaches it
dotted — `infra.<service>.service.X` — through `import infra`. Domains keep owning *what* they
store or ask (SQL, key shapes, queries, prompts); infra owns the connection, the client lifecycle,
the re-exported types and the service's own attribute/key names.

| Module | Owns |
|---|---|
| `celery` | app + broker URL, queue routing, worker init |
| `elasticsearch` | async client (`new_es` / `get_es`), index mappings and names |
| `langfuse` | SDK client, sessions, scores, span-attribute setters, exporter, prompts, evals |
| `minio` | `MinIOStorage` adapter, raw S3 clients, `MINIO_*` / `AWS_*` env |
| `neo4j` | async driver, `Neo4jGraph`, connection tunables |
| `opentelemetry` | SDK bootstrap, exporters, instruments, generic span helpers (`client_span`, `db_span`, `node_tracer`, `attach_span_attrs`) |
| `playwright` (+ `crawl4ai/`) | CDP endpoint resolution, driver start, the Crawl4AI library bound to the browsers |
| `postgres` | `connect()`, URL builder, shared LangGraph checkpointer and checkpoint replay |
| `qdrant` | async client, request models, sparse-embedding class |
| `redis` | client lifecycle (`client()` / `client_sync()`), URL, event stream, cancel flags, locks (`try_lock`, `release_if_owner`), prefix scans (`iter_held`, `first_held`, `delete_matching`) |

Rules, enforced offline by `scripts/server/test_infra_boundary.py` (one rule per service, plus
`client-spans`, which bans hand-built `db.*` / `aws.s3.*` span attributes outside `infra/`;
`env-params`, which bans env reads in `infra/` outside `params.py`; `entrypoint-redis`, which keeps
Redis clients and key layouts out of routers; and `llm-counter`, which keeps usage accounting in
`domains/settings/llm_counter/`):

- `infra` never imports `domains` (two known exceptions: the celery worker-init credential warm-up
  and the langfuse judges).
- Env reads live in `params.py` (read once at import, so a pod's env must be set before the process
  starts); pure helpers (URL builders, config objects, regex-based string transforms) in
  `domain.py`; I/O in `service.py`. The file-taxonomy triggers of §2 apply unchanged — no
  placeholder files.
- A client is opened for the length of a block — `async with infra.redis.service.client() as r:` —
  never with a hand-written `try` / `finally: aclose()`. The exceptions are clients that outlive one
  function on purpose (the app-state client, an ingestion run's client shared with its watcher and
  store, the `Progress` object's own).
- Routers (`entrypoints/`) call domain services; they do not open Redis clients or know key layouts.
  Whatever a router needs from Redis — a single-flight gate, a live-run registry,
  cancel propagation, or cleanup — is a function in the workflow's `runtime/dispatch/service.py`
  (`reserve_start` / `release_start`, `read_active`, `cancel_run`, `wipe_redis`, …); the router keeps
  only HTTP concerns and the Celery enqueue. Focused endpoint checks can pin
  responses and Redis state against a throwaway Redis instance.
- Every service module exposes a `span(operation, **attrs)` helper where the service is called
  over the wire, built on `infra.opentelemetry.service.client_span`; domain-specific span wrappers
  (e.g. YCS's `qdrant_search_span`) stay in the domain's `observability/service.py` and only add
  their own attribute vocabulary.
- A domain module that wraps an infra helper to bind its own keys or TTLs
  stays: it holds domain knowledge that infrastructure must not own.
- Not an infra module: libraries that are not services (LangChain, pydantic), the private LLM
  rotator and the embedding endpoint (`domains/settings/chat`, `domains/settings/embeddings`).

---

## Sources

The recommendations above synthesize:

- [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) — canonical per-domain layout (router/schemas/models/service/constants/exceptions). This is the convention we're loosely following today; the §2 split is a refinement, not a replacement.
- [Cosmic Python — Service Layer chapter](https://www.cosmicpython.com/book/chapter_04_service_layer.html) — the `domain.py` vs `service.py` split (Functional Core, Imperative Shell). The whole book (Percival & Gregory, O'Reilly) is the reference for pythonic application architecture.
- [Pydantic — Dataclasses](https://docs.pydantic.dev/latest/concepts/dataclasses/) — when Pydantic dataclasses beat stdlib (validation only).
- [Pydantic — Settings Management](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — `pydantic-settings` is for env/file-sourced runtime config, not compile-time constants.
- [Implementing Domain-Driven Design with FastAPI](https://medium.com/delivus/implementing-domain-driven-design-with-fastapi-6aed788779af) — the `entities.py` vs `schemas.py` distinction (DDD value-objects vs validation schemas).
- [PEP 8 — Style Guide](https://peps.python.org/pep-0008/) — module naming + constants conventions.
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) — module organization + naming.
