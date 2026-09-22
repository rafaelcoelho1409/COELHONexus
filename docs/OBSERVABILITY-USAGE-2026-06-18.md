# Observability — Hands-on Usage Guide

**Date:** 2026-06-18
**Audience:** future-me adding observability to a new domain.
**Updated 2026-09-22:** added the Current State Audit, a live-research SOTA
update, and a portable standards checklist for future projects. Corrected two
stale claims (§6b judge import path, §10 "shipped" claim) and replaced the
dead link to the deleted SOTA doc at the bottom.

This guide answers: *"I have a new domain `foo`; how do I add spans / metrics / scores / sessions / prompts so it renders in LangFuse + Tempo + Mimir like the existing ones?"*

Every section below is **copy-pasteable** from a real shipped pattern in the codebase.

---

## Current State Audit — 2026-09-22

Full repo inventory (`apps/fastapi`, `apps/fastmcp`, `apps/fasthtml`, plus a
peek at COELHOLLMRotator and COELHOCloud) against this guide's claims.

**Confirmed good, no action needed:**
- LangFuse SDK is fully installed and far more built out than the (now
  corrected) `project_observability_sota_2026_06_18` memory suggested — 12+
  files under `infra/langfuse/` covering sessions, scores, prompts (cache +
  fallback), `annotation.py` (`flag_for_review`), the LangChain callback
  builder, dataset uploader/runner, and 4 LLM-judges (faithfulness, novelty,
  citation_accuracy, ragas_relevance).
- Celery instrumentation timing (`worker_process_init` signal, post-fork) and
  the deliberate exclusion of Redis auto-instrumentation ("task-queue chatter
  produces thousands of zero-value spans") both still match current
  OTel-Python-Contrib guidance — no change needed.
- FastMCP relies on the app setting the global `TracerProvider`, no separate
  SDK needed — matches FastMCP's own current telemetry docs.

**P0 — the system-wide dark spot.** `COELHOLLMRotator`'s
`domains/llm/rotator/observability/service.py:1` is literally
`"""No-op observability — OTel/LangFuse removed for demo. All spans are
no-ops."""`. Every `genai_completion_span` / `genai_embedding_span` /
`genai_bandit_*` call site in `chain/service.py` (~15 of them) now yields a
`_NoOpSpan` stub. Nexus's own outbound call (`domains/settings/chat/service.py`)
has zero span code either — it relies entirely on `HTTPXClientInstrumentor`'s
generic `POST` span, which the Langfuse gate's allow-list doesn't match (no
`gen_ai.*` attrs, no `dd./rr./ycs.` prefix). **Net effect: no LLM call
anywhere in the system currently produces a `gen_ai.*` span or a Langfuse
"generation" observation** — despite the Rotator having a fully-designed
`gen_ai.*` + `bandit.*` attribute contract sitting dormant in
`observability/keys.py`, and the entire evals/scores/prompts machinery on the
Nexus side built to consume exactly that data. Treat this as a decision
point, not a bug to blindly revert — confirm why it was stubbed (repo split?
version conflict? perf?) before re-wiring.

**P1 — stale doc claims**, both fixed inline below: §6b's judge sample
pointed at `domains.llm.rotator.chain` (retired 2026-09-21 per
`docs/CODE-CONVENTIONS.md`); the real path is
`domains.settings.chat.service.chat_judge_async`. §10 claimed
`build_langchain_callback` "shipped in `domains/rr/task.py`" — it has no call
sites anywhere outside its own definition.

**P1 — FastHTML is a trace dead-end.** Zero OTel/Langfuse dependency, no
`infra/otel`, no trace-context propagation on its calls into the FastAPI
backend. Every FastHTML→FastAPI hop starts a brand-new trace; a user click
can't be followed through to the backend spans it triggered.

**P2 — a parallel, un-unified telemetry system.**
`domains/ycs/runtime/llm_counter/` and `domains/rr/runtime/llm_counter/` are
Redis-backed LangChain-callback counters feeding the FastHTML "usage drawer"
— legitimate (sync UI read vs. trace-store query) but they duplicate exactly
the token/cost data the (currently dark) `gen_ai.usage.*` spans were meant to
carry. Once P0 is fixed, decide whether these should dual-write an OTel
metric too, or stay separate by design.

**P2 — not a bug.** `infra/otel/` in `apps/fastapi` and `apps/fastmcp`
duplicate the LangFuse allow-list logic — deliberate per the fastmcp port
comment, since the two are independently deployed apps. **Update
2026-09-22:** `apps/fastapi/infra/otel/` was reorganized to the
`domain.py`/`service.py`/`params.py`/`entities.py` shape (§2 below) — the
old `_LangFuseSpanGate` class is now free functions in `domain.py`
(`should_keep_span`), so the two apps' copies are no longer byte-identical,
just logically equivalent. `apps/fastmcp/infra/otel/` was intentionally left
untouched (out of scope for this pass) — mirror the same file split there
when it's next touched, or accept the drift; either is fine, just don't
"fix" it into a cross-app shared import (`docs/CODE-CONVENTIONS.md` §8's
dotted-path rule is for references *inside* one app, not a mandate to share
code across independently-deployed apps).

---

## SOTA Update — 2026-09-22 (live research, see bottom of this section for sources)

1. **Langfuse SDK is pinned `langfuse>=3,<4`; v4 has been current since March
   2026** — OTel-native `get_client()` / `@observe` /
   `start_as_current_observation()`, with the old `trace()/span()/generation()`
   calls deprecated. Self-hosted Langfuse has more runway than Cloud (legacy
   ingestion sunsets on Cloud 2026-11-16; self-hosted v3 gets patches until
   2027-01), so this isn't urgent — but plan the v4 bump as its own deliberate
   wave, not a side-effect of the next unrelated `infra/langfuse/` change.
2. **The hand-rolled `_LangFuseSpanGate` + `langfuse.observation.*` /
   `langfuse.trace.*` attribute-setting is doing, by hand, roughly what
   Langfuse's own `langfuse.otel.LangfuseSpanProcessor` now does out of the
   box** (auto-recognizes OpenInference/native `gen_ai.*` spans, owns the
   attribute mapping). Worth a spike to see if `LangfuseSpanProcessor` can
   replace the hand-rolled gate — less code to keep in sync with Langfuse's
   evolving ingestion schema. Known SDK limitation: it can't currently send to
   an external OTLP endpoint *without* Langfuse also configured as a
   destination — a non-issue here since dual-export to Alloy is wanted
   anyway; it just means this SDK component can only replace the Langfuse-side
   processor, not the Alloy one.
3. **`gen_ai.*` semantic conventions are still "Development" stability**,
   moved to their own dedicated repo in OTel-Python v1.42.0 (2026-06-12), with
   no versioned schema URL yet. Treat the Rotator's `observability/keys.py`
   attribute names as pinned-to-a-snapshot, not a stable API — schedule a
   deliberate re-sync whenever OTel ships a stable `gen_ai` release, rather
   than letting it silently drift.
4. **LangGraph instrumentation**: the hand-rolled `@traced` decorator + RR's
   `PhaseEventsMiddleware` is a legitimate, actively-recommended pattern
   (native manual spans) — not something to feel behind on.
   `openinference-instrumentation-langchain` (Arize, actively maintained,
   releases through 2026-09-18) hooks LangChain's callback system for less
   boilerplate, but has a known root-graph-span fidelity gap — if adopted,
   use it to *supplement* node-level `@traced` spans, not replace them.
5. **Evals are fully unrestricted OSS** since Langfuse open-sourced all eval
   features (2025-06) — no license gate blocking anything in
   `infra/langfuse/evals/`. The free-tier-only judge path (`chat_judge_async`
   → rotator → NIM) is already the correct SOTA pattern; keep running judges
   as dataset batch evals, not synchronously per production trace, to keep
   cost/latency off the hot path.

**Sources:** [Langfuse SDK overview](https://langfuse.com/docs/observability/sdk/overview) ·
[Python v2→v3 upgrade](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v2-to-v3) ·
[Migrate v3→v4](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4) ·
[Add Langfuse to an existing OTel setup](https://langfuse.com/faq/all/existing-otel-setup) ·
[semantic-conventions-genai repo](https://github.com/open-telemetry/semantic-conventions-genai) ·
[FastMCP telemetry docs](https://gofastmcp.com/servers/telemetry) ·
[OTel Python Contrib — Celery](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/celery/celery.html) ·
[OpenInference LangGraph root-span gap (issue #3339)](https://github.com/Arize-ai/openinference/issues/3339).
Caveats: the exact current Langfuse *server* version wasn't independently
confirmed (inferred from SDK-compatibility requirements); no official
Langfuse-published Collector-fan-out reference architecture exists beyond
community discussions — workable, not authoritative.

---

## TL;DR — The folder shape

**Status (2026-09-22): this section is now a trigger table, not a fixed
file list** — mirrors `docs/CODE-CONVENTIONS.md` §2's philosophy exactly:
create a file only when its trigger holds, never for uniformity alone. A
36-line domain and a 400-line domain do NOT get the same folder shape, and
that's correct, not an inconsistency to "fix" by padding the small one.

**Location.** Observability code lives at
`domains/<feature>/runtime/observability/`, a sibling of that feature's
other `runtime/` concerns (`dispatch/`, `progress/`, `cancel/`,
`checkpoint/`). **Exception:** a feature with no `runtime/` folder at all
yet (nothing to group observability code alongside) may keep a single flat
`domains/<feature>/observability.py` instead. The moment that feature grows
a `runtime/` folder for any other reason, the flat file should move under
it like everywhere else, rather than staying an orphan next to a `runtime/`
folder that already exists — **this exception is now theoretical, not
demonstrated**: `dd/ingestion` was the one domain using it (see the audit
row below) and was moved under `runtime/` on 2026-09-22, at the user's
explicit request for uniformity across `dd/`, even though the trigger
table alone wouldn't have forced the move on its own (36 lines, one
caller). Kept as documentation of the pattern for whichever future domain
starts small enough to need it again.

**File triggers, once inside `runtime/observability/`:**

| File | Trigger (create iff) | What goes in it |
|---|---|---|
| `__init__.py` | REQUIRED CORE | re-export whichever files below are present |
| `metrics.py` | REQUIRED CORE the moment this feature records ≥1 metric | `record_*` functions calling `infra.otel.service.get_instrument(key)` |
| `service.py` | ≥2 call sites need the SAME span-wrapping shape (e.g. a `@traced` decorator applied across several LangGraph node files) | The decorator/wrapper — the "one span per node" idiom |
| `spans.py` | the feature wraps ≥1 external I/O call (a `db.*` or `gen_ai.*` span) that is its OWN dedicated context manager, distinct from the generic node decorator above | One context-manager span helper per backend/operation (`qdrant_search_span`, `chat_completion_span`, ...) |
| `keys.py` | ≥3 attribute/span-name string constants are shared across `spans.py`, especially when they mirror an external semantic convention (`gen_ai.*`, `aws.s3.*`) | Attribute-name constants, span-name constants, operation-value constants |
| `domain.py` | the span-gating/attribute logic contains ≥1 pure decision worth unit-testing without a real span object | Pure predicate/attribute-shaping functions, no I/O |

Notably absent from this table: a per-domain `scores.py`. Scoring calls
`infra.langfuse.scores.record_score(...)` directly from wherever a score is
computed (see §5) — no domain ever needed its own wrapper for it, so one
was never created. If a feature eventually does, the same trigger logic
above applies to it too.

Plus the two cross-cutting locations touched regardless of which files
above you add:

- `apps/fastapi/infra/otel/entities.py` — append a `MetricSpec` for each new instrument.
- `apps/fastapi/infra/otel/keys.py` — add `foo_id` to `ALLOWED_BAGGAGE_KEYS` if it needs propagating onto every child span (moved here from `params.py` in the 2026-09-22 `infra/otel/` reorg — see the file index below).

**Audit against these triggers (2026-09-22)** — every existing domain
already matches, with one real exception:

| Domain | Files present | Matches triggers? |
|---|---|---|
| `dd/planner`, `dd/synth` | `metrics.py`, `service.py` | ✅ — `service.py` earned its keep (≥8 node files import it); no `spans.py`/`keys.py`/`domain.py` because neither trigger holds yet |
| `ycs` | `metrics.py`, `service.py`, `spans.py`, `domain.py` | ✅ — the richest, because YCS genuinely has all four needs (node spans, `db.*`/`gen_ai.rerank` I/O spans, and pure attribute helpers) |
| `settings` | `metrics.py`, `spans.py`, `keys.py` | ✅ — no `service.py` (chat/embeddings are 2 leaf functions, not a multi-node graph, so no shared node-decorator need); `keys.py` earned its keep mirroring the `gen_ai.*` semconv |
| `dd/ingestion` | `runtime/{dispatch,observability,progress}/`, `observability/` has only `metrics.py` | ✅ — moved under `runtime/` 2026-09-22 to match planner/synth structurally (`dispatch/`+`progress/`+`observability/` relocated together, not just observability alone — moving observability by itself would have left a `runtime/` folder holding exactly one thing, a worse inconsistency than the flat file it replaced). No `service.py`/`spans.py`/`keys.py`/`domain.py` — ingestion's one span is still inline in `runtime/dispatch/service.py:run()`, no fan-out of consumers exists to justify extracting it |
| `rr` | `runtime/observability/{metrics,service}.py` | ✅ (2026-09-22 consolidation) — `runtime/metrics.py` moved to `runtime/observability/metrics.py`; the `@traced_tool` decorator moved from `agent/tools/observability.py` (an orphan next to an already-existing `runtime/` folder — the placement mistake, not a legitimate exception) to `runtime/observability/service.py`, mirroring planner/synth's `@traced`. The 4 tool-module callers now reach it via `from domains.rr.runtime.observability.service import traced_tool` (a plain import, not a `domains.*` attribute chase — required per §8 Exception 2 since `@traced_tool` runs at module-exec time). Phase-transition spans deliberately stay in `agent/middleware/service.py` — tightly coupled to `PhaseEventsMiddleware`'s own state, a legitimate exception, not fragmentation. |

---

## 1. Add a node-level OTel span (LangGraph or generic)

**Pattern:** `@traced("name")` decorator, mirrors what DD synth + YCS already use.

```python
# domains/foo/runtime/observability/spans.py
from __future__ import annotations
import functools
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace
import infra.otel


def traced(name: str) -> Callable:
    """Decorate `async def node(state, ...)` to wrap it in a top-level span."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = infra.otel.service.get_tracer()
            with tracer.start_as_current_span(
                f"foo/{name}",
                attributes = {"foo.node": name, "foo.thread_id": state.get("thread_id", "")},
            ) as span:
                try:
                    result = await fn(state, *args, **kwargs)
                    span.set_attribute("foo.ok", True)
                    return result
                except Exception as e:
                    span.set_attribute("foo.ok", False)
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator
```

Use it:
```python
from domains.foo.runtime.observability import traced

@traced("plan")
async def plan_node(state: dict) -> dict:
    ...
```

---

## 2. Add a `db.*` or `gen_ai.*` span around an I/O call

**Pattern:** context-manager helper, one per backend. Mirrors `qdrant_search_span` / `es_search_span` / `reranker_span` in `domains/ycs/runtime/observability/spans.py`.

```python
# domains/foo/runtime/observability/spans.py (continued)
import contextlib
from typing import Iterator

@contextlib.contextmanager
def foo_backend_span(
    *, operation: str, item_count: int,
) -> Iterator[object | None]:
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None; return
    with tracer.start_as_current_span(
        f"db.foo.{operation}",
        kind        = _otel_trace.SpanKind.CLIENT,
        attributes  = {
            "db.system":         "foo",
            "db.operation":      operation,
            "db.foo.item_count": item_count,
        },
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise
```

Use it:
```python
from domains.foo.runtime.observability import foo_backend_span

async def call_foo_backend(items):
    with foo_backend_span(operation = "bulk_write", item_count = len(items)):
        await foo_client.write_many(items)
```

This renders as a `db.foo.bulk_write` span in Tempo, with attributes `db.system=foo`, `db.foo.item_count=N`.

---

## 3. Add a metric

### 3a. Define it once in the central registry

```python
# apps/fastapi/infra/otel/entities.py
INSTRUMENTS: tuple[MetricSpec, ...] = (
    ...,  # existing
    MetricSpec(
        key         = "foo_writes",
        name        = "foo.writes_total",
        description = "Foo backend writes — labels: tenant, status",
        kind        = "counter",
    ),
)
```

### 3b. Emit it from the domain `metrics.py`

```python
# domains/foo/runtime/observability/metrics.py
import infra.otel


def record_foo_write(*, tenant: str, status: str) -> None:
    """Increment when a Foo write completes."""
    try:
        if (inst := infra.otel.service.get_instrument("foo_writes")) is not None:
            inst.add(1, attributes = {"tenant": tenant, "status": status})
    except Exception:
        pass
```

PromQL:
```promql
sum by (tenant, status) (rate(foo_writes_total[5m]))
```

---

## 4. Group a workflow under a LangFuse session

**Pattern:** `with session(...)` around the entry point. Every span inside the block — including LiteLLM auto-emitted ones — gets `session_id` / `user_id` baggage, which `BaggageSpanProcessor` mirrors onto every child span. LangFuse's OTLP ingester groups traces by `session_id` automatically.

```python
from infra.langfuse.sessions import session as _lf_session

async def run_foo_pipeline(workflow_id: str, tenant: str):
    with _lf_session(
        "foo",                       # baggage `pipeline` = "foo"
        session_id = workflow_id,    # baggage `session_id`
        user_id    = tenant,         # baggage `user_id`
    ):
        return await _foo_pipeline_inner(workflow_id, tenant)
```

The same pattern is shipped in:
- `domains/rr/task.py` — RR digest cycle
- `api/v1/ycs/agents/router.py:rag_search` — YCS Ask
- `domains/dd/synth/runtime/dispatch/service.py:run_study_async` — DD study

---

## 5. Attach quality scores to traces

**Pattern:** `record_score(name, value)` — fires off the LangFuse SDK without blocking.

```python
# Inside a span context — score is bound to the active trace.
from infra.langfuse.scores import record_score

record_score("foo.quality.precision", 0.92, comment = "tenant=acme")
```

Existing pattern: `domains/dd/synth/runtime/observability/metrics.py:record_grader_dim_score` dual-writes — OTel histogram **and** LangFuse score. Use that shape for any "quality metric" you want visible both in aggregate (Mimir) and per-trace (LangFuse UI).

---

## 6. Migrate a prompt to LangFuse prompt management

### 6a. At the call site

Local builder stays as the source of truth; LangFuse is the additive override.

```python
def build_foo_prompt(*, tenant: str, items: list[dict]) -> str:
    try:
        from infra.langfuse.prompts import get_prompt as _lf_get
        rendered = _lf_get(
            "foo.bar",
            label     = "production",
            variables = {"tenant": tenant, "n_items": len(items)},
        )
        if rendered:
            return rendered
    except Exception:
        pass
    return f"You are foo. Tenant={tenant}. Items={len(items)}..."  # local fallback
```

### 6b. Publish the template (one-shot)

Add a script under `scripts/observability/publish_foo_prompt.py` mirroring `publish_chapter_propose_prompt.py`:

```python
from infra.langfuse import get_client

PROMPT = """You are foo. Tenant={{tenant}}. Items={{n_items}}..."""

client = get_client()
client.create_prompt(name="foo.bar", prompt=PROMPT, labels=["production"], type="text")
```

Run it:
```bash
kubectl exec -i -n coelhonexus-dev <pod> -c coelhonexus-fastapi -- \
    bash -c 'PYTHONPATH=/app python /tmp/publish_foo_prompt.py'
```

---

## 7. Add a gold dataset + LLM-judge eval

### 7a. Gold corpus

```
observability/fixtures/foo/<dataset_name>/
  inputs.json     # [{ "input": {...}, "expected_output": {...}, "metadata": {...} }]
  rubric.md      # human-readable rubric (the judge prompt embeds the criteria inline)
```

### 7b. Judge

```python
# apps/fastapi/infra/langfuse/evals/judges/foo_quality.py
async def foo_quality(input_: dict, expected: dict, actual: dict) -> float:
    from domains.settings.chat.service import chat_judge_async  # domains/llm/ retired 2026-09-21
    prompt = f"Score 1-5. Expected={expected}; actual={actual}"
    raw = await chat_judge_async(prompt, max_tokens=8, temperature=0.0)
    import re
    m = re.search(r"[1-5]", raw)
    return float(m.group()) if m else 0.0
```

### 7c. Upload + run

```bash
PYTHONPATH=/app python -m infra.langfuse.datasets.uploader \
    observability/fixtures/foo/<dataset> foo.<dataset>.v1 "Foo gold corpus"

# Then in a script using run_dataset_eval(...)
```

Existing shipped reference: `scripts/observability/run_faithfulness_eval.py`.

---

## 8. Add a Grafana dashboard

Drop `observability/grafana-dashboards/foo-something.json` next to the existing ones. Use `Mimir` as the Prometheus datasource UID. For span-derived metrics (latency, error rate), the `traces_spanmetrics_*` series populate automatically (Tempo→Mimir pipeline).

Reference shipped: `rotator-red.json`, `dd-pipeline.json`, `rr-digests.json`, `ycs-retrieval.json`.

---

## 9. Loki ↔ Tempo correlation — jump from a log to its trace

Already wired: `LoggingInstrumentor` in `apps/fastapi/infra/otel/service.py:_instrument_libraries` injects `trace_id` + `span_id` into every Python log record. Both fields flow through stdout/stderr → Alloy → Loki, and the same `trace_id` is on the Tempo span.

In Grafana:
1. **Log → Trace:** open a Loki query for `{namespace="coelhonexus-dev"}`. Click a log line — the right pane shows `trace_id: <hex>`. Click the `Tempo` icon next to it to jump straight to that span tree.
2. **Trace → Log:** open a Tempo trace. The right pane has a *"Logs for this span"* section that auto-queries Loki for matching `trace_id`. Useful when a span is opaque ("LLM call took 12s") and you want the per-step logs underneath.

No additional config needed — both wiring directions are populated by what we ship today. The only gotcha: if the Loki datasource doesn't have the `derivedFields` regex set to capture `trace_id`, the "Tempo" link icon won't appear. Set it once in Grafana:

```
trace_id=(\w+)   →   Tempo datasource → ${__value.raw}
```

---

## 10. Wire a LangChain callback (DeepAgents / LangGraph)

```python
from infra.langfuse.callbacks import build_langchain_callback

cb = build_langchain_callback(
    session_id = workflow_id,
    user_id    = tenant,
    tags       = ["foo", "pipeline"],
)
callbacks = [c for c in (existing_cb, cb) if c is not None]
await agent.ainvoke({"messages": [...]}, config = {"callbacks": callbacks})
```

**Not yet wired anywhere** (corrected 2026-09-22 — previously claimed shipped
in `domains/rr/task.py`; `build_langchain_callback` currently has no call
sites outside its own definition). RR is the natural first caller once its
DeepAgents orchestrator lands. Returns `None` when LangFuse is unavailable —
the `None`-filter line keeps the existing path intact regardless of caller.

---

## What you should NOT do

- **Don't** import the LangFuse SDK at module load. Use `infra.langfuse.service.get_client()`; it's lazy + fail-soft.
- **Don't** put domain concepts in `infra/otel/` or `infra/langfuse/`. Those are vendor folders; domain enrichment goes under `domains/<feature>/runtime/observability/`.
- **Don't** create a new TracerProvider. `infra/otel/service.py:init_otel()` owns it; calling it again is idempotent.
- **Don't** emit span events with unbounded data (prompts, full responses) without the env-controlled `RECORD_CONTENT` gate (TODO — captured in §9 of the SOTA doc).
- **Don't** raise from observability code. Every helper in `infra/langfuse/` returns `None` or silently drops on failure. The pipeline must never break because LangFuse is down.

---

## Portable Standards Checklist — for this project and the next one

A project-agnostic version of what COELHONexus already gets right (plus the
one gap it has, #8), to carry into any new Python + LLM project:

1. **Two vendor folders, never mixed with domain code.** `infra/otel/` =
   transport only (TracerProvider, exporters, resource attrs, library
   auto-instrumentation, metric registry). `infra/langfuse/` = SDK features
   only (sessions, scores, prompts, evals, callbacks). Domain-specific
   enrichment — what to name a span, which attributes matter for *this*
   workflow — lives under `domains/<feature>/runtime/observability/`, never
   inside the vendor folders.
2. **One `@traced(name)` decorator per app, applied at the node/handler
   boundary** — not scattered inline `start_as_current_span` calls. Keeps
   every span's shape consistent and makes "does this workflow have tracing?"
   a one-line grep.
3. **A single allow-list gate decides what reaches the expensive/rate-limited
   backend** (Langfuse here) while everything reaches the cheap unlimited one
   (Tempo/Alloy here) unconditionally. Gate on semantics (`gen_ai.*` present,
   known name prefixes), not on a per-call-site flag threaded through
   business code.
4. **Session/user/tag context rides OTel Baggage, not function parameters.**
   One context manager at the workflow entry point (`with session(...)`), and
   a `BaggageSpanProcessor` mirrors it onto every descendant span
   automatically — including spans emitted by libraries you don't control
   (LiteLLM, LangChain).
5. **Every observability call is fail-soft by construction.** Wrap in
   `try/except: pass` (or return `None`) at the *helper* level, not at every
   call site — a dead Langfuse/Alloy endpoint must never be able to fail a
   production request.
6. **Metrics live in one central registry** (`entities.py: INSTRUMENTS`
   here), not declared ad hoc per module — makes "what do we measure" a
   single file to read, and prevents duplicate-instrument-name bugs.
7. **Pin semantic-convention attribute names to a dated snapshot** when the
   spec is pre-1.0/"Development" (true of `gen_ai.*` today) — put the source
   commit/date in a comment next to the constant, and schedule a re-sync
   rather than silently drifting.
8. **Decide LLM-call span ownership before building the consumer side.** If a
   gateway/rotator sits between your app and the provider, the *gateway* must
   own the `gen_ai.*` span — it has the model/tokens/latency/provider-
   selection data your app doesn't. Building the evals/scores/dashboards
   layer before that span exists (this project's current P0) leaves the
   whole stack with nothing real to show.
9. **Reach for the official SDK's own OTel bridge before hand-rolling
   attribute mapping** (Langfuse's `LangfuseSpanProcessor` is one), and
   re-check this at each major SDK bump — a hand-rolled gate that was
   necessary at integration time can become redundant maintenance once the
   vendor ships the same thing natively.

---

## File index — observability code by responsibility

| File | What it owns |
|---|---|
| `infra/otel/service.py` | SDK init, exporter builders (Alloy + LangFuse OTLP + Mimir), `BaggageSpanProcessor` + `bag_context()`, `get_instrument(key)` factory, library auto-instrumentation |
| `infra/otel/domain.py` | Pure predicates — LangFuse span-gate (`should_keep_span`) + baggage-key allow check (`is_allowed_baggage_key`) |
| `infra/otel/entities.py` | Central `INSTRUMENTS` list (`MetricSpec` registry) + `DedupeRateLimitFilter` (stateful log-dedup entity) |
| `infra/otel/keys.py` | Task-path / route-name tables + `ALLOWED_BAGGAGE_KEYS` — consumed by `domain.py`, not `service.py` (moved out of `params.py` 2026-09-22) |
| `infra/otel/params.py` | Tunables consumed only by `service.py` itself (service identity, BSP/OTLP timeouts, `FASTAPI_EXCLUDED_URLS`, `OTEL_NOISY_LOGGERS`) |
| `infra/langfuse/service.py` | Lazy SDK singleton |
| `infra/langfuse/sessions.py` | `session(...)` context manager |
| `infra/langfuse/scores.py` | `record_score(...)` |
| `infra/langfuse/prompts.py` | `get_prompt(...)` with cache + fallback |
| `infra/langfuse/callbacks.py` | `build_langchain_callback(...)` |
| `infra/langfuse/datasets/` | uploader + runner |
| `infra/langfuse/evals/judges/` | one file per LLM-judge |
| `domains/<feature>/runtime/observability/` | per-domain enrichment — which files exist is trigger-based, see the TL;DR table at the top |
| `scripts/observability/` | one-shot publish scripts for LangFuse-managed prompts + eval runners |
| `observability/grafana-dashboards/` | importable JSONs |
| `observability/fixtures/` | gold corpora |

---

## Want to extend further?

The original SOTA doc this pointed to (`docs/OBSERVABILITY-LANGFUSE-OTEL-SOTA-2026-06-18.md`)
was deleted 2026-07-03 during a docs cleanup — its content is superseded by
the **Current State Audit** and **SOTA Update** sections above. Remaining
open items from that doc not yet covered anywhere: prompt experiments,
exemplars, tail sampling, SLO recording rules. Annotation queues are now
partially shipped (`infra/langfuse/annotation.py:flag_for_review`) but not
wired into any UI review flow yet.
