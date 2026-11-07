"""Pipeline observability — the `@traced` (planner) and `@traced_synth` node decorators (one span per node, exported to Alloy + LangFuse) and the metric recorders."""
from __future__ import annotations
import domains, infra

import contextlib
import logging


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def workflow_span(
    span_name: str,
    *,
    workflow: str,
    attributes: dict | None = None,
    kind: str = "workflow_root",
    node: str | None = None,
    stage: str | None = None,
    session_name: str | None = None,
    session_fields: dict | None = None,
    input_data: dict | None = None,
    trace_metadata: dict | None = None,
    observation_metadata: dict | None = None,
):
    """One run as a Langfuse trace root (`kind="workflow_root"`) or one node under a run (`kind="workflow_node"`): an optional Langfuse session around an
    OpenTelemetry span that carries the keep/kind/workflow attributes, with the run's input and metadata set on it. Yields `set_output(data)`; the run
    calls it with its outcome before leaving the block."""
    keys = infra.langfuse.keys
    langfuse = infra.langfuse.service
    attrs = {keys.KEEP: True, keys.KIND: kind, keys.OBS_META_WORKFLOW: workflow}
    if kind == "workflow_root":
        attrs[keys.TRACE_NAME] = span_name
    if node:
        attrs[keys.OBS_META_NODE_NAME] = node
    if stage:
        attrs[keys.OBS_META_STAGE] = stage
    attrs.update(attributes or {})
    with contextlib.ExitStack() as stack:
        if session_name:
            stack.enter_context(langfuse.session(session_name, **(session_fields or {})))
        stack.enter_context(infra.opentelemetry.service.get_tracer().start_as_current_span(span_name, attributes = attrs))
        if input_data is not None:
            langfuse.set_current_span_langfuse_io(input_data = input_data)
        if trace_metadata is not None:
            langfuse.set_current_span_langfuse_trace_metadata(trace_metadata)
        if observation_metadata is not None:
            langfuse.set_current_span_langfuse_observation_metadata(observation_metadata)
        yield lambda output_data: langfuse.set_current_span_langfuse_io(output_data = output_data)


def run_config(thread_id: str) -> dict:
    """The LangGraph config of a run: its thread, and the Langfuse callback when Langfuse is on."""
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": [c for c in (infra.langfuse.service.build_langchain_callback(),) if c is not None],
    }


def _llm_context(state: dict, node_name: str):
    """Attribute the LLM calls inside this node to (planner, thread, node)."""
    return domains.settings.llm_counter.service.llm_context(
        ns = "dd", run_id = state.get("thread_id") or "", node_id = node_name, stage = "planner",
    )


traced = infra.opentelemetry.service.node_tracer(
    prefix      = "planner",
    workflow    = "dd_planner",
    stage       = "planner",
    span_prefix = "dd.planner.node",
    state_attrs = lambda state: {
        "thread_id":      state.get("thread_id") or "",
        "framework_slug": state.get("framework_slug") or "",
    },
    context     = _llm_context,
)


def span_attrs(name: str, attrs: dict) -> None:
    """Put a node's headline numbers on the span it runs in (what the trace view shows next to the node)."""
    infra.opentelemetry.service.attach_span_attrs(name, attrs)


def record_planner_run(
    *,
    framework: str,
    mode: str,
    outcome: str,
    duration_s: float | None = None,
    chapter_count: int | None = None,
) -> None:
    attrs = {
        "framework": framework,
        "mode":      mode or "unknown",
        "outcome":   outcome or "unknown",
    }
    try:
        if (inst := infra.opentelemetry.service.get_instrument("planner_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument("planner_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if chapter_count is not None and (
            inst := infra.opentelemetry.service.get_instrument("planner_chapter_count")
        ) is not None:
            inst.record(max(chapter_count, 0), attributes = attrs)
    except Exception:
        pass


async def record_planner_terminal(graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, duration_s: float | None) -> None:
    """Record how a planner run ended, from the thread's final state (best-effort: logged, never raised)."""
    try:
        values = dict((await graph.aget_state(config)).values or {})
        plan_write_stats = values.get("plan_write_stats") or {}
        select_stats = values.get("select_stats") or {}
        chapter_count = plan_write_stats.get("n_chapters") or select_stats.get("n_chapters_out")
        record_planner_run(
            framework = str(values.get("framework_slug") or fallback_slug or "unknown"),
            mode = str(values.get("planner_mode") or fallback_mode or "unknown"),
            outcome = status,
            duration_s = duration_s,
            chapter_count = int(chapter_count) if chapter_count is not None else None,
        )
    except Exception as error:
        logger.warning(f"[planner] terminal metric emit failed: {type(error).__name__}: {error}")


async def planner_langfuse_output(graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, error: str | None) -> dict:
    """The summary a planner run hands to its Langfuse trace: derived from the thread's final state, or the bare outcome when it cannot be read."""
    output = {"status": status, "framework_slug": fallback_slug or "unknown", "mode": fallback_mode or "unknown"}
    if error:
        output["error"] = error
    try:
        values = dict((await graph.aget_state(config)).values or {})
        output = domains.dd.pipeline.runtime.dispatch.domain.derive_langfuse_output(
            values, fallback_slug = fallback_slug, fallback_mode = fallback_mode, status = status, error = error,
        )
    except Exception as exc:
        logger.warning(f"[planner] langfuse output summary failed: {type(exc).__name__}: {exc}")
    return output


# ---- synth ---------------------------------------------------------------------------------------------------------------------------------

def _synth_llm_context(state: dict, node_name: str):
    """Attribute the LLM calls inside this node to (synth, thread, node)."""
    return domains.settings.llm_counter.service.llm_context(
        ns = "dd", run_id = state.get("thread_id") or "", node_id = node_name, stage = "synth",
    )


traced_synth = infra.opentelemetry.service.node_tracer(
    prefix      = "synth",
    workflow    = "dd_synth",
    stage       = "synth",
    span_prefix = "dd.synth.node",
    state_attrs = lambda state: {
        "thread_id":      state.get("thread_id") or "",
        "framework_slug": state.get("framework_slug") or "",
        "chapter_id":     state.get("chapter_id") or "",
    },
    context     = _synth_llm_context,
)


def record_chapter_outcome(
    *,
    outcome: str,
    framework: str,
    chapter_number: int,
    pinned_model: str | None = None,
    duration_s: float | None = None,
    iterations: int | None = None,
) -> None:
    """Outcome + duration + iters for one chapter's synth lifecycle."""
    attrs = {"framework": framework, "chapter_number": str(chapter_number), "outcome": outcome}
    if pinned_model:
        attrs["pinned_model"] = pinned_model
    try:
        if (inst := infra.opentelemetry.service.get_instrument("chapter_outcome")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (inst := infra.opentelemetry.service.get_instrument("chapter_synth_duration")) is not None:
            inst.record(duration_s, attributes = attrs)
        if iterations is not None and (inst := infra.opentelemetry.service.get_instrument("refiner_iters")) is not None:
            inst.record(iterations, attributes = attrs)
    except Exception:
        pass


async def record_chapter_terminal(graph, config: dict, status: str) -> None:
    """Chapter outcome and audit metrics of a finished chapter (best-effort: logged, never raised)."""
    if status != "done":
        return
    try:
        state = dict((await graph.aget_state(config)).values or {})
        slug = str(state.get("framework_slug") or "")
        chapter_id = str(state.get("chapter_id") or "")
        chapter_num = domains.dd.pipeline.runtime.dispatch.domain.chapter_number_from_id(chapter_id)
        chapter_stats = state.get("chapter_stats") or {}
        if not slug or not chapter_id or chapter_num <= 0 or not chapter_stats:
            return
        wall_ms = int(chapter_stats.get("wall_ms", 0) or 0)
        refine_iter = int(state.get("refine_iter", 0) or 0)
        halt_reason = str((state.get("mgsr_stats") or {}).get("halt_reason") or "")
        audit_passed = bool(chapter_stats.get("audit_passed", False))
        record_chapter_outcome(
            outcome = "accept" if halt_reason == "chapter_passed" else "debt_below",
            framework = slug, chapter_number = chapter_num, duration_s = max(wall_ms / 1000.0, 0.0), iterations = max(refine_iter, 0),
        )
        n_code_refs = int(chapter_stats.get("n_code_refs", 0) or 0)
        n_missing = int(chapter_stats.get("n_missing", 0) or 0)
        missing_ratio = float(n_missing) / float(n_code_refs) if n_code_refs > 0 else (1.0 if not audit_passed else 0.0)
        record_audit_missing(
            framework = slug, chapter_number = chapter_num, iteration = max(refine_iter, 0), missing_ratio = max(0.0, min(missing_ratio, 1.0)),
        )
    except Exception as error:
        logger.warning(f"[synth] terminal metric emit failed: {type(error).__name__}: {error}")


def record_bucket_split_overflow(*, framework: str, sections_dropped: int) -> None:
    """Increment when the outline hits the section cap and is trimmed."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("bucket_split_overflow")) is not None:
            inst.add(1, attributes = {"framework": framework, "sections_dropped": str(sections_dropped)})
    except Exception:
        pass


def record_grader_dim_score(*, framework: str, dim: str, score: float) -> None:
    """One checklist criterion's score (0-1). Dual-write: OTel histogram → Mimir for aggregates; LangFuse score → per-trace inspection in the UI."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("grader_dim_score")) is not None:
            inst.record(score, attributes = {"framework": framework, "dim": dim})
    except Exception:
        pass
    try:
        infra.langfuse.service.record_score(f"grader.{dim}", score, comment = f"framework={framework}")
    except Exception:
        pass


def record_audit_missing(*, framework: str, chapter_number: int, iteration: int, missing_ratio: float) -> None:
    """Audit's missing-hash ratio for one iteration."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("audit_missing_ratio")) is not None:
            inst.record(
                missing_ratio,
                attributes = {"framework": framework, "chapter_number": str(chapter_number), "iteration": str(iteration)},
            )
    except Exception:
        pass


def record_study_completion(*, framework: str, duration_s: float, n_accepted: int, n_total: int, outcome: str) -> None:
    """End-to-end study completion."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("study_completion_duration")) is not None:
            inst.record(
                duration_s,
                attributes = {"framework": framework, "outcome": outcome, "accepted_count": str(n_accepted), "total_chapters": str(n_total)},
            )
    except Exception:
        pass


def record_classical_patch(*, dim: str, framework: str) -> None:
    """Increment when the book-harmonize pass applies a patch."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("classical_patch_applied")) is not None:
            inst.add(1, attributes = {"dim": dim, "framework": framework})
    except Exception:
        pass


def record_score(name: str, value: float, *, comment: str = "") -> None:
    """A named score on the current Langfuse trace (best-effort)."""
    try:
        infra.langfuse.service.record_score(name, value, comment = comment)
    except Exception:
        pass
