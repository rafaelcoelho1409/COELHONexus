"""Substep 3 — off_topic: two-stage filter (cheap cosine prefilter +
LLM-as-judge on the middle band).

Design (2026-05-17 night — supersedes the GMM/Otsu percentile cut):
the embedding margin is a CHEAP PREFILTER, not the verdict. Per DCLM
2026 ablations + RAGAS small-corpus guidance, the right shape for a
100-2000 doc corpus is: cleave the obvious cases with similarity
geometry, then ask the big LLM to judge the uncertain middle band per
page. No percentile-based amputation — every doc gets the right amount
of scrutiny.

  1. Load pre-computed unit-norm vectors (NIM dd-embed, via embed_corpus).
  2. Embed positive + negative anchors (input_type="query"); compute
     margin = cos(page, pos) - cos(page, neg).
  3. CLEAVE:
       margin >= _MARGIN_KEEP_FLOOR  → KEEP (clearly on-topic; no LLM)
       margin <= _MARGIN_DROP_CEIL   → DROP (clearly meta-content; no LLM)
       otherwise                     → LLM-AS-JUDGE per page
  4. For middle-band docs, ask the dd-all rotator (big-model group) a
     binary KEEP/DROP question with a strict rubric. Bounded concurrency
     via asyncio.Semaphore so we don't blow past 40 RPM cumulatively.
  5. Combine cheap-cleave verdicts + LLM verdicts → final relevant_files.

Stats payload reports each path's verdict counts + per-file decisions so
the operator can spot-check the LLM's judgments and tune the cleave
thresholds.
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from routers.v1.docs_distiller.resolver import _index_by_slug
from services.docs_distiller.ingestion.storage_minio import get_storage
from services.llm.chain import (
    DD_EMBED_MODEL_NAME,
    chat_judge_bandit_async,
    embed_via_router_async,
)

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from .embed_corpus import load_embeddings


logger = logging.getLogger(__name__)


# LLM judge config
_JUDGE_BODY_CHARS = 4000     # chars sent to the LLM per page
_JUDGE_MAX_TOKENS = 8        # plenty for "KEEP" or "DROP" plus whitespace
# Concurrency: 5 parallel in-flight calls — the ParetoBandit + LiteLLM
# cascade handles transient failures; the inner helper already routes
# each call through the best-ranked deployment with per-attempt retries
# down the bandit's top-K list, so the outer concurrency stays modest.
_JUDGE_CONCURRENCY = 5
# Per-call retry budget — outer wrapper retries the WHOLE bandit cascade
# this many times if it raises (covers transient infra failures like Redis
# blips). Each bandit cascade itself tries top-K=5 deployments internally.
_JUDGE_MAX_ATTEMPTS = 2
_JUDGE_BACKOFF_BASE = 1.5

# Negative-anchor template. Stable, framework-independent — describes
# the kind of "looks like docs but isn't" content that bypasses URL
# filters (CoC, sponsor lists, conference talk archives, issue
# templates, changelog dumps, generated index pages).
_NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)


def _build_positive_descriptor(entry: dict) -> str:
    """Anchor prompt for the framework. Uses the catalog name + category."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        return (
            f"Documentation for {name}, a {category} library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to "
            f"articles, conceptual explanations."
        )
    return (
        f"Documentation for {name}. Teaching content: tutorials, "
        f"guides, API reference, how-to articles, conceptual explanations."
    )


def _build_judge_prompt(framework_name: str, framework_category: str, body: str) -> str:
    """Single-shot KEEP/DROP rubric, designed to be unambiguous so the
    model returns a clean one-word verdict at temperature=0."""
    cat_clause = f", a {framework_category} library/framework" if framework_category else ""
    truncated = (body or "")[:_JUDGE_BODY_CHARS].strip() or "(empty page)"
    return (
        f"You are filtering pages from the official documentation site of "
        f"{framework_name}{cat_clause}.\n\n"
        f"Decide if this page is:\n"
        f"  KEEP → teaching content (tutorials, guides, API reference, "
        f"how-to articles, conceptual explanations of how to use the library)\n"
        f"  DROP → repository meta-content (code of conduct, contributing "
        f"guidelines, sponsor lists, conference talks or event pages, "
        f"blog posts, changelog dumps, release notes, governance policies, "
        f"license text, generated index pages with no real content)\n\n"
        f"Respond with EXACTLY ONE WORD: KEEP or DROP.\n\n"
        f"--- Page content (truncated) ---\n"
        f"{truncated}\n"
        f"--- End page content ---\n\n"
        f"Answer (KEEP or DROP):"
    )


def _parse_verdict(text: str) -> bool | None:
    """Parse the LLM's one-word verdict. Returns True for KEEP, False for
    DROP, None if the response is unparseable (caller decides fallback)."""
    if not text:
        return None
    head = text.strip().upper().split()[0].strip(".,;:!\"'`)")
    if head == "KEEP":
        return True
    if head == "DROP":
        return False
    return None


async def _judge_one(
    sem: asyncio.Semaphore,
    framework_name: str,
    framework_category: str,
    body: str,
    on_complete=None,
) -> tuple[bool, str, str | None, dict]:
    """Run ONE bandit-routed LLM-judge call with cascade fallback.

    Returns (keep, raw_response, error, meta). On final-attempt parse
    failure or all-cascade exception, defaults to KEEP (err on the side
    of preserving content per the user's quality-over-speed rule).

    `meta` carries bandit telemetry: which deployment answered, latency,
    reward — surfaced into stats for operator visibility.

    `on_complete`, when provided, is an async callback invoked once per
    judgment with kwargs (keep: bool, error: str|None). Used by off_topic
    to emit live counter progress."""
    prompt = _build_judge_prompt(framework_name, framework_category, body)
    last_error: str | None = None
    last_response: str = ""
    last_meta: dict = {}
    for attempt in range(_JUDGE_MAX_ATTEMPTS):
        try:
            async with sem:
                response, meta = await chat_judge_bandit_async(
                    prompt,
                    max_tokens=_JUDGE_MAX_TOKENS,
                    temperature=0.0,
                    expected_pattern=r"^(KEEP|DROP)$",
                )
            last_response = response
            last_meta = meta
            verdict = _parse_verdict(response)
            if verdict is not None:
                if on_complete is not None:
                    try:
                        await on_complete(keep=verdict, error=None)
                    except Exception:
                        pass
                return verdict, response, None, meta
            last_error = "unparseable_verdict"
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
        if attempt < _JUDGE_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_JUDGE_BACKOFF_BASE ** (attempt + 1))
    if on_complete is not None:
        try:
            await on_complete(keep=True, error=last_error)
        except Exception:
            pass
    return True, last_response, last_error, last_meta


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    raw_files = state.get("raw_files") or []
    embeddings_ref = state.get("embeddings_ref") or ""
    if not slug or not raw_files:
        return {
            "relevant_files": list(raw_files),
            "off_topic_stats": {
                "kept": len(raw_files), "dropped": 0,
                "skipped": "no input",
            },
        }
    if not embeddings_ref:
        raise RuntimeError(
            "off_topic: missing embeddings_ref in state — embed_corpus must "
            "run first"
        )

    entry = _index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    framework_category = entry.get("category") or ""
    positive_descriptor = _build_positive_descriptor(entry)
    negative_descriptor = _NEGATIVE_DESCRIPTOR

    t0 = time.monotonic()
    minio = get_storage()
    await emit_progress(
        thread_id, "off_topic", "start",
        files=len(raw_files), embeddings_ref=embeddings_ref,
    )

    # ── Embed both anchors as queries (single rotator call). ────────────
    anchor_vecs = await embed_via_router_async(
        [positive_descriptor, negative_descriptor], input_type="query",
    )
    await emit_progress(
        thread_id, "off_topic", "anchors_embedded",
        positive=positive_descriptor[:200],
        negative=negative_descriptor[:200],
    )
    pos_anchor = np.asarray(anchor_vecs[0], dtype=np.float32)
    neg_anchor = np.asarray(anchor_vecs[1], dtype=np.float32)
    pos_anchor /= max(float(np.linalg.norm(pos_anchor)), 1e-9)
    neg_anchor /= max(float(np.linalg.norm(neg_anchor)), 1e-9)

    # ── Load pre-computed unit-norm corpus matrix. ─────────────────────
    blob = await minio.read_bytes(embeddings_ref)
    stored_keys, page_vecs = load_embeddings(blob)

    key_to_idx = {k: i for i, k in enumerate(stored_keys)}
    missing = [k for k in raw_files if k not in key_to_idx]
    if missing:
        raise RuntimeError(
            f"off_topic: {len(missing)} files in raw_files have no matching "
            f"vector in {embeddings_ref!r} — re-run embed_corpus "
            f"(first missing: {missing[0]!r})"
        )
    ordered_idx = np.array([key_to_idx[k] for k in raw_files], dtype=np.int64)
    page_mat = page_vecs[ordered_idx]

    cos_pos = page_mat @ pos_anchor
    cos_neg = page_mat @ neg_anchor
    margins = (cos_pos - cos_neg).astype(np.float64)

    # 2026-05-17 night: pure LLM-as-Judge — no cosine cleave. Every doc
    # gets the bandit-routed big-LLM verdict. Margins stay in the stats
    # payload as TELEMETRY only (operator can correlate margin to LLM
    # verdict to spot calibration drift between cheap + expensive signals).
    n = len(raw_files)
    keep_mask = np.zeros(n, dtype=bool)

    # ── LLM-as-judge on EVERY doc, bandit-routed. ──────────────────────
    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    bodies = await minio.read_many(raw_files)
    sem = asyncio.Semaphore(_JUDGE_CONCURRENCY)

    # Shared counter so we can emit live "judged N/M" progress as
    # each future resolves (futures complete in arbitrary order with
    # asyncio.gather, so accumulate across the gathered set).
    judged_done = {"n": 0, "keep": 0, "drop": 0, "err": 0}
    _EMIT_EVERY = max(1, n // 40)   # ~40 progress events / run

    async def _on_judge_complete(keep: bool, error: str | None) -> None:
        judged_done["n"] += 1
        if error:
            judged_done["err"] += 1
        elif keep:
            judged_done["keep"] += 1
        else:
            judged_done["drop"] += 1
        if judged_done["n"] % _EMIT_EVERY == 0 or judged_done["n"] == n:
            await emit_progress(
                thread_id, "off_topic", "llm_progress",
                judged=judged_done["n"], total=n,
                llm_keep=judged_done["keep"],
                llm_drop=judged_done["drop"],
                llm_err=judged_done["err"],
            )

    tasks = [
        _judge_one(
            sem, framework_name, framework_category, body,
            on_complete=_on_judge_complete,
        )
        for body in bodies
    ]
    verdicts = await asyncio.gather(*tasks)

    # Bandit deployment usage tally — which models actually answered.
    deployment_usage: dict[str, int] = {}
    for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
        keep_mask[doc_idx] = keep
        dep = (meta or {}).get("deployment") or "?"
        deployment_usage[dep] = deployment_usage.get(dep, 0) + 1
        judge_decisions.append({
            "key":        raw_files[doc_idx],
            "margin":     float(margins[doc_idx]),
            "verdict":    "KEEP" if keep else "DROP",
            "raw":        raw_resp[:60],   # cap for state payload size
            "error":      err,
            "deployment": dep,
            "latency_s":  (meta or {}).get("latency_s"),
            "reward":     (meta or {}).get("reward"),
            "attempts":   (meta or {}).get("attempts"),
        })
        if err:
            judge_errors.append(err)

    # ── Compute outputs + observability ────────────────────────────────
    relevant: list[str] = []
    per_file: list[tuple[str, float, str, bool]] = []
    cos_kept: list[float] = []
    for i, key in enumerate(raw_files):
        keep = bool(keep_mask[i])
        leaf = key.rsplit("/", 1)[-1]
        # decision_source is always "llm" in the pure-LLM-judge design;
        # kept in the tuple for forward-compat with future cleave revivals.
        per_file.append((leaf, round(float(margins[i]), 4), "llm", keep))
        if keep:
            relevant.append(key)
            cos_kept.append(float(cos_pos[i]))

    domain_coherence = (
        sum(cos_kept) / len(cos_kept) if cos_kept else 0.0
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    llm_kept = sum(1 for d in judge_decisions if d["verdict"] == "KEEP")
    llm_dropped = sum(1 for d in judge_decisions if d["verdict"] == "DROP")

    # Bandit telemetry — top deployments by usage + reward avg per deployment
    rewards_by_dep: dict[str, list[float]] = {}
    for d in judge_decisions:
        r = d.get("reward")
        if r is None:
            continue
        rewards_by_dep.setdefault(d.get("deployment") or "?", []).append(float(r))
    deployment_summary = [
        {
            "deployment": dep,
            "calls":      deployment_usage.get(dep, 0),
            "reward_avg": (sum(rewards) / len(rewards)) if rewards else 0.0,
        }
        for dep, rewards in sorted(
            rewards_by_dep.items(),
            key=lambda kv: -deployment_usage.get(kv[0], 0),
        )
    ]

    stats = {
        "kept":              len(relevant),
        "dropped":           n - len(relevant),
        "total":             n,
        "llm_judged":        len(judge_decisions),
        "llm_kept":          llm_kept,
        "llm_dropped":       llm_dropped,
        "llm_errors":        len(judge_errors),
        "domain_coherence":  round(domain_coherence, 4),
        "per_file_margins":  per_file,
        "judge_decisions":   judge_decisions,
        "deployment_usage":  deployment_summary,
        "elapsed_ms":        elapsed_ms,
        "anchor_positive":   positive_descriptor,
        "anchor_negative":   negative_descriptor,
        "embeddings_ref":    embeddings_ref,
        "embed_model":       DD_EMBED_MODEL_NAME,
        "judge_concurrency": _JUDGE_CONCURRENCY,
        "judge_router":      "pareto-bandit/dd-grader",
    }

    try:
        from opentelemetry import trace as _otel_trace
        span = _otel_trace.get_current_span()
        span.set_attribute("off_topic.kept", stats["kept"])
        span.set_attribute("off_topic.dropped", stats["dropped"])
        span.set_attribute("off_topic.cleave_keep", cleave_keep)
        span.set_attribute("off_topic.cleave_drop", cleave_drop)
        span.set_attribute("off_topic.llm_judged", len(judge_decisions))
        span.set_attribute("off_topic.llm_errors", len(judge_errors))
        span.set_attribute("off_topic.domain_coherence", stats["domain_coherence"])
        span.set_attribute("off_topic.elapsed_ms", elapsed_ms)
    except Exception:
        pass

    # Build an error-type breakdown so the operator can see WHAT's failing.
    error_breakdown: dict[str, int] = {}
    for err in judge_errors:
        # err is "ExceptionType: message" — bucket by the prefix.
        kind = err.split(":", 1)[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1
    stats["llm_error_breakdown"] = error_breakdown

    top_dep_summary = ", ".join(
        f"{d['deployment'].split('/')[-1]}:{d['calls']}"
        for d in deployment_summary[:3]
    ) or "—"
    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{n} "
        f"(dropped {stats['dropped']}); "
        f"llm judged={len(judge_decisions)} (keep={llm_kept} drop={llm_dropped}, "
        f"errors={len(judge_errors)} = {dict(sorted(error_breakdown.items()))}); "
        f"top deployments [{top_dep_summary}]; "
        f"coherence={stats['domain_coherence']:.3f}; elapsed={elapsed_ms}ms"
    )
    await emit_progress(
        thread_id, "off_topic", "done",
        kept=len(relevant), dropped=n - len(relevant), total=n,
        llm_judged=len(judge_decisions), llm_keep=llm_kept,
        llm_drop=llm_dropped, llm_err=len(judge_errors),
        llm_error_breakdown=error_breakdown,
        coherence=stats["domain_coherence"],
        wall_ms=elapsed_ms,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
