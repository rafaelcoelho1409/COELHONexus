# Observability — Hands-on Usage Guide

**Date:** 2026-06-18
**Audience:** future-me adding observability to a new domain.

This guide answers: *"I have a new domain `foo`; how do I add spans / metrics / scores / sessions / prompts so it renders in LangFuse + Tempo + Mimir like the existing ones?"*

Every section below is **copy-pasteable** from a real shipped pattern in the codebase.

---

## TL;DR — The folder shape

For a new domain `foo`:

```
apps/fastapi/domains/foo/runtime/observability/
  __init__.py    # re-export the public surface
  spans.py       # @traced decorator + context-manager span helpers
  metrics.py     # record_* recorders calling get_instrument(key)
  scores.py      # record_score wrapper(s) for the LangFuse SDK
```

Plus, the two cross-cutting locations:

- `apps/fastapi/infra/otel/metrics_registry.py` — append a `MetricSpec` for each new instrument.
- `apps/fastapi/infra/otel/baggage.py` — add `foo_id` to `ALLOWED_BAGGAGE_KEYS` if needed.

---

## 1. Add a node-level OTel span (LangGraph or generic)

**Pattern:** `@traced("name")` decorator, mirrors what DD synth + YCS already use.

```python
# domains/foo/runtime/observability/spans.py
from __future__ import annotations
import functools
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace
from infra.otel import get_tracer


def traced(name: str) -> Callable:
    """Decorate `async def node(state, ...)` to wrap it in a top-level span."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
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
    tracer = get_tracer()
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
# apps/fastapi/infra/otel/metrics_registry.py
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
from infra.otel.metrics import get_instrument


def record_foo_write(*, tenant: str, status: str) -> None:
    """Increment when a Foo write completes."""
    try:
        if (inst := get_instrument("foo_writes")) is not None:
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
    from domains.llm.rotator.chain import chat_judge_async
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

Shipped in `domains/rr/task.py`. Returns `None` when LangFuse is unavailable — the `None`-filter line keeps the existing path intact.

---

## What you should NOT do

- **Don't** import the LangFuse SDK at module load. Use `infra.langfuse.client.get_client()`; it's lazy + fail-soft.
- **Don't** put domain concepts in `infra/otel/` or `infra/langfuse/`. Those are vendor folders; domain enrichment goes under `domains/<feature>/runtime/observability/`.
- **Don't** create a new TracerProvider. `infra/otel/service.py:init_otel()` owns it; calling it again is idempotent.
- **Don't** emit span events with unbounded data (prompts, full responses) without the env-controlled `RECORD_CONTENT` gate (TODO — captured in §9 of the SOTA doc).
- **Don't** raise from observability code. Every helper in `infra/langfuse/` returns `None` or silently drops on failure. The pipeline must never break because LangFuse is down.

---

## File index — observability code by responsibility

| File | What it owns |
|---|---|
| `infra/otel/service.py` | SDK init, library auto-instrumentation, LiteLLM callback wiring |
| `infra/otel/exporters.py` | Alloy + LangFuse OTLP + Mimir exporter builders |
| `infra/otel/baggage.py` | `BaggageSpanProcessor` + `bag_context()` |
| `infra/otel/metrics_registry.py` | Central `INSTRUMENTS` list |
| `infra/otel/metrics.py` | `get_instrument(key)` factory |
| `infra/langfuse/client.py` | Lazy SDK singleton |
| `infra/langfuse/sessions.py` | `session(...)` context manager |
| `infra/langfuse/scores.py` | `record_score(...)` |
| `infra/langfuse/prompts.py` | `get_prompt(...)` with cache + fallback |
| `infra/langfuse/callbacks.py` | `build_langchain_callback(...)` |
| `infra/langfuse/datasets/` | uploader + runner |
| `infra/langfuse/evals/judges/` | one file per LLM-judge |
| `domains/<feature>/runtime/observability/` | per-domain enrichment (spans + metrics + scores) |
| `scripts/observability/` | one-shot publish scripts for LangFuse-managed prompts + eval runners |
| `observability/grafana-dashboards/` | importable JSONs |
| `observability/fixtures/` | gold corpora |

---

## Want to extend further?

See `docs/OBSERVABILITY-LANGFUSE-OTEL-SOTA-2026-06-18.md` §3-4 for the **full** LangFuse + OTel feature menu — annotation queues, prompt experiments, exemplars, tail sampling, SLO recording rules.
