# Code organization conventions

**Status:** LIVING document. Authored 2026-06-02 from a synthesis of FastAPI
best-practices, Cosmic Python, Pydantic docs, and an audit of the current
`apps/fastapi/domains/` tree. This is the rulebook for future modules and
the migration target for existing ones.
**Updated 2026-09-18:** added §8, the cross-module reference convention
(dotted `domains.*` path, no wrapper/namespace classes).

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
- **`node.py` stays a thin LangGraph shell** — 20–40 lines wrapping a
  single call into `service.py`.
- **Pydantic for boundary validation only** (LLM responses, HTTP bodies).
  Dataclasses for everything internal.
- **Cross-module references use a dotted `domains.*` path, unaliased**
  (`domains.dd.ingestion.artifacts.params.MAX_ARTIFACT_BYTES`), not a bare
  `from .module import name` and not a wrapper/namespace class. See §8.

---

## 1. Diagnosis — what the current `constants.py` / `types.py` names hide

Audit of the actual content (audited 2026-06-02):

| File | What's actually inside | Why the name is wrong |
|---|---|---|
| `ingestion/storage/constants.py` | Key-builder **functions** (`framework_prefix()`, `manifest_key()`, `page_key()`) + a TTL scalar | These are functions, not constants. The file is really a "key shapes you must agree on" file. |
| `synth/sawc/constants.py` | Schema version string, 12 numeric tunables (subtopics min/max, explanation words min/max, memory chars), pre-compiled regex, decision-log comments | Mixes 4 separable concerns: version markers, parameter groups, patterns, history. |
| `synth/sawc/types.py` | Pydantic `BaseModel` schemas (`Subtopic`, `Section`, `Citation`, `_LLMSectionDraft`) + plain `@dataclass` value objects (`MemoryEntry`, `SAWCStats`) | Mixes "validation at the LLM boundary" with "internal domain shapes". |
| `synth/sawc/service.py` | Pure logic (AST identifier extraction, hash computation, validator) + I/O orchestration (LLM dispatch, MinIO writes, retry loops) | The pure functions are buried alongside `async` I/O — they could be tested without any mocking but aren't because they're not isolated. |

The pattern problem: **lowest-common-denominator names hide what each
file is for, which forces every new contributor to read the file before
they can navigate to it.** For a reference project, this is exactly the
opposite of what we want.

---

## 2. New naming convention (split by responsibility)

### Replacement for `constants.py`

Pick whichever fit the module's actual content. A module can have several
of these, or none.

| New name | What goes in it | Modules where it fits |
|---|---|---|
| `keys.py` | Storage key builders + path helpers (functions like `framework_prefix(slug)`) | `ingestion/storage/`, anywhere a `*_key()` function exists today |
| `params.py` | Loose numeric tunables (thresholds, concurrency, timeouts) that don't fit a dataclass group | most synth/planner nodes |
| `prompts.py` | LLM prompt strings + their version markers | every node that calls an LLM |
| `patterns.py` | Pre-compiled regexes (anything that survives `re.compile(...)` at module scope) | `corpus_normalize/`, `post/`, `ingestion/filters/` |
| `versions.py` | Schema/prompt version strings (cache-invalidation knobs) | sawc, outline, digest |
| `config.py` | Frozen-dataclass GROUPS of related tunables (see §3) | rotator, ingestion tiers |

Reserve plain `constants.py` ONLY for a module that legitimately has 1–2
unrelated scalars and nothing else (rare). If you find yourself adding
comments to *group* scalars inside a `constants.py`, that's the signal to
split into one of the above files OR a dataclass.

### Replacement for `types.py`

| New name | What goes in it |
|---|---|
| `schemas.py` | Pydantic `BaseModel` classes — LLM input/output validation, HTTP body validation |
| `entities.py` | Plain `@dataclass` value objects — the "things" the domain manipulates (`ManifestEntry`, `Section`, `MemoryEntry`) |
| `state.py` | LangGraph `TypedDict`s (planner uses this name already; standardize) |
| `errors.py` | Exception classes (`IngestCancelled`, etc.) |
| inline (no file) | One-off `TypeAlias` / `Literal` types — put them in the module that uses them, no separate file needed |

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
# synth/sawc/config.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """Limits for per-section MemoryEntry derivation. Tuned for
    cross-section context without bloating the LLM input budget."""
    summary_chars_min: int = 200
    summary_chars_max: int = 600
    term_chars_max:    int = 40
    terms_max:         int = 12


@dataclass(frozen=True, slots=True)
class StructureConfig:
    """SAWC v2 cookbook output shape limits."""
    subtopics_min:          int = 2
    subtopics_max:          int = 6
    explanation_words_min:  int = 12
    explanation_words_max:  int = 60


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
synth/sawc/
├── __init__.py
├── prompts.py        # LLM templates + version strings
├── params.py         # loose numeric tunables
├── config.py         # frozen-dataclass GROUPS (MemoryConfig, StructureConfig)
├── patterns.py       # pre-compiled regexes
├── schemas.py        # Pydantic schemas: _LLMSectionDraft, Subtopic, Section, Citation
├── entities.py       # @dataclass value objects: MemoryEntry, SAWCStats
├── domain.py         # PURE functions — no I/O, no LLM, no async
├── service.py        # ORCHESTRATION — async, dispatches LLM, calls domain.*, writes MinIO
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

From the current sawc service.py, examples:
- `_ast_identifiers(code: str) -> set[str]` — extracts identifiers from Python AST.
- `_overlap_score(idents_a, idents_b) -> float` — Jaccard-style overlap.
- `_compute_manifest_hash(...)` — deterministic SHA hash.
- `_validate_section_against_inputs(section, digest)` — boolean / repair-list logic.

These are the "Functional Core". They're trivial to unit-test
(no mocks, no fixtures, no event loop). They're the modules to write
property-based tests against.

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
# synth/sawc/node.py
from .service import sawc_write_run
from ..state import SynthState
from ..observability.spans import traced


@traced("sawc_write")
async def sawc_write(state: SynthState) -> dict:
    return await sawc_write_run(state)
```

The teaching value: `node.py` becomes a 5-second read. The architecture
(what the node does, how it does it, what state it patches) is in
`service.py` + `domain.py`, where it belongs.

---

## 5. Pilot target — what `synth/sawc/` should look like

Use sawc as the **pattern-establishing module**. It has the most surface
area (~1,500 LOC across service + types + constants today), so it
exercises all six naming categories and forces the domain/service split
decision. After it lands, every other module copies the shape.

### Target tree

```
synth/sawc/
├── __init__.py             # re-exports the public API (sawc node + key schemas)
├── prompts.py              # WRITER_PROMPT, CRITIC_PROMPT, REPAIR_PROMPT (+ versions)
├── config.py               # MemoryConfig, StructureConfig, RetryConfig
├── params.py               # _CONCURRENCY, _PICK_TIMEOUT_S — loose scalars not in a group
├── patterns.py             # _IDENT_STOPWORDS, _CODE_FENCE_RE
├── versions.py             # SAWC_SCHEMA_VERSION, prompt-version strings
├── schemas.py              # Pydantic: _LLMSectionDraft, Subtopic, Section, Citation
├── entities.py             # @dataclass: MemoryEntry, SAWCStats, SectionDraft
├── domain.py               # PURE: _ast_identifiers, _overlap_score, _structural_score,
│                           #       _compute_manifest_hash, validate_section
├── service.py              # ORCHESTRATION: sawc_write_run, run_stage, fire_drafts,
│                           #       pick_best, persist_chapter_draft
├── node.py                 # ~30 lines: @traced wrapper over service.sawc_write_run
├── state.py                # (only if sawc-specific state — most lives in ../state.py)
└── errors.py               # SAWCDraftFailed, SAWCAllDraftsFailed
```

### What gets touched in import-callers

Most call sites today import from `sawc.constants` and `sawc.types`.
After the split they import from the specific file. **The migration is
mechanical** — search-and-replace import lines per module.

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

Each phase is **one PR per module**. The sweep across `domains/dd/` +
`domains/llm/` is roughly 3–5 days of focused work for the whole tree.

**Recommended pilot:** apply all 5 phases to `synth/sawc/` first. It
becomes the reference template. Other modules then migrate by copying
the shape.

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
  IS the deprecation archive — see `docs/archive/PLANNER-CLASSICAL-REFERENCE.md`
  for the pattern (condensed design doc, code recovered via `git log`).
- **Don't wrap pure functions in a class to get import-path clarity**
  (`DDIngestionArtifactsDomain.func(...)`, or a nested `DD.Ingestion.
  Artifacts.Domain.func(...)` tree). Every function needs a manually
  maintained `@staticmethod` mirror inside the class — permanent drift
  risk against the real module, for a problem §8's dotted `domains.*`
  path already solves for free.

---

## 8. Cross-module reference convention — dotted path, no wrapper classes

**Status:** DECIDED 2026-09-18, revised same day. **Scope:** every reference
outside the function/class doing the referencing — including same-directory
sibling files (`domain.py` calling into `keys.py`), not just cross-package
ones. The original scope note here carved out sibling imports as unchanged
from §2; that carve-out was inherited from the pre-existing convention, not
independently justified — a bare `_coerce_usage(...)` three lines into
`service.py` costs the same "which file is this from?" lookup as a bare
cross-domain import did. There's no principled reason "3 lines away in the
same folder" is different from "3 lines away in another domain" for this
goal, so the rule is uniform: no bare `from .module import name`, anywhere,
full stop.

### The rule

Reference everything outside your own leaf directory — functions,
module-level constants, and classes alike — through one fully dotted path
rooted at the top-level `domains` package, imported unaliased:

```python
import domains

domains.dd.ingestion.artifacts.params.MAX_ARTIFACT_BYTES          # constant
domains.dd.ingestion.artifacts.domain.compute_manifest_hash(...)  # function
domains.dd.planner.entities.Chapter(title="...", sources=[...])   # class
```

Never `from domains.dd.ingestion.artifacts import domain` and never
`from domains.dd.planner.entities import Chapter`. A bare imported name
loses its path the moment it's read three lines below the import block.
The dotted chain carries its own provenance everywhere it appears — import
line, call site, grep result, stack trace — with no need to scroll up.

**Same-directory siblings follow the same rule, just without the
`domains.*` prefix** (no cross-package boundary to cross, so no need to
route through the global root) — import the sibling *module*, not names
out of it:

```python
# domains/dd/runtime/service.py
from . import domain, keys, params

domain.extract_usage(response)
keys.counters_key(thread_id)
params.COUNTER_TTL_S
```

Never `from .domain import extract_usage` — same reasoning as the
cross-package case, just one folder over instead of several.

### Why this over the alternatives considered

| Alternative | Rejected because |
|---|---|
| Bare `from .module import name` (pre-2026-09-18 style) | Call site (`compute_manifest_hash(...)`) tells you nothing without checking the import block |
| Wrapper class per module (`DDIngestionArtifactsDomain.func(...)`) | Every function needs a manually maintained `@staticmethod` mirror inside the class — permanent drift risk vs. the real module. Google Python Style Guide: "Never use `staticmethod`... write a module-level function instead." |
| Nested class tree (`DD.Ingestion.Artifacts.Domain.func(...)`) | Same staticmethod-wrapping cost at every tree level, plus short segment names (`Domain`, `Artifacts`) are ambiguous away from the import line unless always spelled from the root — which then requires the full wrapper tree just to reproduce what plain packages already give for free |
| Mega flat name (`DomainsDDIngestionArtifactsDomain.func(...)`) | Same wrapping cost; a 30+ character unbroken PascalCase run has no visual parse boundary — harder to read at depth than the dotted form it replaces |

This converges on the same pattern large, well-known libraries already
use for exactly this reason — `tf.keras.layers.Conv3D(...)`,
`tf.nn.relu(...)`, `np.linalg.norm(...)`. Packages and modules are
Python's native namespace mechanism; no wrapper class is needed to get a
dotted, self-describing call site — that's what `import` + attribute
access already does.

### Mechanics — `__init__.py` re-exports required at every level

Python does not expose a subpackage as an attribute of its parent just
because the parent was imported. Every intermediate `__init__.py` in the
chain needs one `from . import <child>` line per direct child, added once
when that child module is created and never touched again as the child's
internals change:

```python
# domains/__init__.py
from . import dd, llm, rr, ycs

# domains/dd/__init__.py
from . import ingestion, planner, synth, resolver, runtime

# domains/dd/ingestion/__init__.py
from . import artifacts, dispatch, filters, post, progress, storage, tiers

# domains/dd/ingestion/artifacts/__init__.py
from . import domain, entities, keys, params, patterns, service
```

Without these, `import domains` leaves `domains.dd` (and everything below
it) undefined — `AttributeError` at the first dotted access, not at
import time.

### No alias

`import domains` — never `import domains as X`.
1. `domains` is referenced from `api/` as much as from inside `domains/`
   itself, so there's no single "internal-only" context to shorten it for.
2. Any short alias risks colliding with an unrelated local variable
   somewhere in a codebase this size. `cn` was considered and rejected —
   it's a common ad-hoc name for a database/network connection object,
   which this project creates constantly (Postgres, Redis, MinIO, Neo4j,
   Qdrant, ES). Not aliasing has zero such risk by construction, and
   matches how the project already imports most real dependencies
   (`fastapi`, `celery`, `redis`, `minio`, `neo4j`, `qdrant_client` — all
   unaliased). Only a handful of extremely high-repetition data-science
   libraries (numpy, pandas, tensorflow) earned a community-standard
   short alias; `domains` doesn't need that treatment to save a few
   characters.

### Local shorthand — allowed, but must keep the identifying prefix

If one file calls the same leaf module many times, a local alias is fine
as long as it keeps enough of the path to stay self-describing without
the import line:

```python
from domains.dd.ingestion.artifacts import domain as ingestion_artifacts_domain
ingestion_artifacts_domain.compute_manifest_hash(...)
```

Never shorten to something generic (`domain`, `d`, `art`) — that
reintroduces the exact ambiguity this convention exists to remove.

### Exception 1 — heavy/env-dependent modules stay out of the eager `__init__.py` chain

A module whose import triggers a *hard* external dependency (reads a
required env var at module level, opens a real connection at import time)
must **not** be added to its parent's `__init__.py` re-export list, even
though it's a legitimate part of that package. Adding it would mean a bare
`import domains` anywhere in the app — including a standalone script, a
test, or a REPL — suddenly requires production secrets to succeed.

Known instances: `dd/planner/task.py` and `dd/synth/task.py` do
`from infra.celery import app`, which transitively requires
`REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD`/`ENVIRONMENT` (never re-exported
from `planner/__init__.py`/`synth/__init__.py` — callers reach it with a
direct `from domains.dd.planner.task import X`, same shape as any normal
Python import, and the same class of exception as the LLM rotator's
reverse-edge import above).

`dd/synth`'s `params.py` originally did `STUDY_SEM = int(os.environ["KD_STUDY_SEM"])`
at module scope — a hard eager read that would have forced this same exception
onto `params`, `graph`, and most of `runtime` (all pull constants from
`params.py`, and Python re-executes a whole module on any import of it, not
just the names actually used). Fixed at the root instead of propagating the
exception: turned it into `study_sem() -> int: return
int(os.environ["KD_STUDY_SEM"])` — a function, read lazily inside whichever
function body needs it, same as any other deferred `domains.*` reference.
Zero behavior change (still one `os.environ` read per call site, just later),
and it let `dd/synth` join the eager `dd/__init__.py` chain outright instead
of carrying its own exception. Prefer this fix — de-lazy the read — over a
wider `__init__.py` exclusion whenever the env-dependent value doesn't
actually need to be read at import time.

### Exception 2 — module-level code needs a plain import, not the dotted chase

`import domains` + `domains.dd.x.y.z` only works for references **deferred
inside a function body** — resolved long after the app has fully booted.
A reference evaluated at *module or class definition time* (a decorator, a
module-level dict/list/tuple literal, a class body attribute, a default
argument value) breaks this, because Python does not set `domains.dd` as
an attribute of the `domains` module until `dd/__init__.py` finishes
running *completely* — and a file that gets loaded transitively as part of
that very `__init__.py` chain (e.g. every node's `node.py`, pulled in via
`nodes/__init__.py` pulled in via `planner/__init__.py`) executes its
module-level code *during* that unfinished bootstrap, not after.

Concretely, this failed with `AttributeError: module 'domains' has no
attribute 'dd'`:

```python
# BROKEN — decorator runs at import time, mid-bootstrap
import domains

@domains.dd.planner.runtime.observability.traced("corpus_load")
async def corpus_load(state) -> dict: ...
```

Fixed by using a plain import for the decorator specifically (the type
annotation on `state` stays dotted-style and is unaffected — `from
__future__ import annotations`, present everywhere in this codebase,
stores annotations as unevaluated strings, so they never actually chase
the attribute chain at runtime):

```python
import domains
from domains.dd.planner.runtime.observability.service import traced

@traced("corpus_load")
async def corpus_load(state: domains.dd.planner.state.PlannerState) -> dict: ...
```

Same reasoning applies to `graph.py`'s `NODE_REGISTRY` dict — its values
are real function objects needed at module-exec time. The fix there is one
step cleaner: import the sibling `nodes` package itself, then chase
attributes off *that* name instead of off `domains`:

```python
from . import nodes

NODE_REGISTRY = {
    "corpus_load": nodes.corpus_load.node.corpus_load,
    ...
}
```

This is safe for a subtler reason than "it's a plain import" — `from .
import nodes` is a normal relative import statement, resolved through
Python's import machinery, not an attribute-chase on an already-existing
binding. It only returns once `nodes/__init__.py` (and everything *it*
re-exports) has fully finished running, so `nodes.corpus_load.node...` is
guaranteed valid the instant the import statement completes — unlike
`domains.dd...`, which depends on a DIFFERENT, still-unfinished frame
higher up the call stack (`dd/__init__.py`) to ever set that attribute.
Prefer this shape over 8 separate `from .nodes.x.node import y` lines
whenever the target is a sibling *package* one level down, not the global
`domains` root.

**Rule of thumb:** a dotted chain is only unsafe at module-exec time when
it's rooted at the global `domains` name specifically. Rooted at a name
your own `from . import x` (or `from .. import x`) just bound, it's always
safe — that import already blocked until `x` was fully ready.

### What still uses real classes

Nothing here changes §2–§5: `entities.py` domain objects, stateful
clients, anything with real invariants or state stays a real class,
instantiated normally — just referenced through the same dotted path as
everything else (`domains.dd.planner.entities.Chapter(...)`), never
through a bare import.

---

## 9. The whole-project structure (target)

```
apps/fastapi/
├── api/v1/                  # routes (FastAPI routers) — thin, dispatch to domains
│   └── dd/{planner,synth,ingestion,pipeline,runs}.py
├── domains/                 # business logic — the bounded contexts
│   ├── dd/                  # Docs Distiller bounded context
│   │   ├── ingestion/
│   │   │   ├── storage/     # ← module = directory with the 5–10 files above
│   │   │   ├── tiers/
│   │   │   ├── progress/
│   │   │   ├── filters/
│   │   │   ├── post/
│   │   │   └── ...
│   │   ├── planner/         # LangGraph planner — one dir per node
│   │   │   ├── corpus_load/
│   │   │   ├── embed_corpus/
│   │   │   ├── off_topic/
│   │   │   ├── doc_distill/
│   │   │   ├── chapter_propose/
│   │   │   ├── chapter_assign/
│   │   │   ├── chapter_select/
│   │   │   ├── order_chapters/
│   │   │   ├── plan_write/
│   │   │   ├── observability/
│   │   │   ├── graph.py     # node wiring — single file
│   │   │   ├── state.py     # PlannerState TypedDict
│   │   │   ├── dispatch.py  # async runners
│   │   │   ├── task.py      # Celery wrappers
│   │   │   └── ...
│   │   └── synth/           # LangGraph synth — same shape as planner
│   └── llm/                 # LLM rotator bounded context
│       ├── rotator/{bandit,chain,benchmarks,discovery,otel_metrics}/
│       └── credentials/
├── app.py                   # FastAPI bootstrap
├── celery_app.py            # Celery bootstrap
├── pyproject.toml
└── shared/                  # cross-cutting (sources.yaml, etc.)
```

Each leaf module follows the **5–10 file convention** from §5.

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
