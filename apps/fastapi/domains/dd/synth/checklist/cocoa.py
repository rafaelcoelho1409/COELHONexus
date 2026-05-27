"""CoCoA two-stage alignment check (Ship C, 2026-05-25).

Based on Code Comprehension then Auditing (arXiv 2410.03131): single-shot
LLM-judges that simultaneously parse code AND grade prose perform worst.
A two-stage pipeline — *explainer* abstracts code into NL behavioral spec
first, *judge* then compares prose-against-spec — beats single-shot by
+68% F1 and +20% accuracy on code-NL alignment benchmarks.

INTEGRATION

This module augments (NOT replaces) the bundled LLM-judge in
`checklist_eval`. The bundled judge already produces verdicts for the 5
semantic criteria including `prose_code_first_not_meta_framing` (c11) and
`code_refs_introduced_in_prose` (c12). CoCoA runs after the bundled judge
and, when it finds explanation↔code drift, OVERRIDES c11+c12 with FAIL
verdicts carrying specific subtopic feedback so mgsr_replan can drive
surgical rerolls.

Conservative-bias aggregation: CoCoA NEVER upgrades the bundled judge —
only downgrades. If CoCoA passes but bundled fails, bundled's FAIL stands.

CONSTRAINTS

Free-tier only — same as faithfulness.py. All LLM calls route through the
FGTS-VA bandit (chat_judge_bandit_async) with two new dd_process keys so
the bandit learns separate quality posteriors for each role:
  - "dd-cocoa-explainer" — cheap arm preferred (per-block abstraction)
  - "dd-cocoa-judge"     — heavyweight arm preferred (alignment verdict)

Two LLM calls per chapter total (one batched call per stage), matching the
bundled judge's cost envelope.

Per `feedback_kd_quality_over_speed`: tokens are free, quality is the
binding constraint. Token budgets are intentionally generous; truncation
only kicks in for pathologically long chapters.
"""
from __future__ import annotations

import json
import logging
import re

from domains.llm.rotator.chain import chat_judge_bandit_async

from ...ingestion.storage import get_storage


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables
# =============================================================================
COCOA_PROMPT_VERSION = "v1-cocoa-2026-05-25"

# Per-hash abstraction cache (Bundle 5, 2026-05-25). Stage 1 abstractions
# depend ONLY on the code body — and the body is content-addressed by
# its vault hash. So the same hash always produces the same spec under
# a fixed prompt_version. Cache MinIO blob path includes prompt_version
# so a prompt revision automatically invalidates without manual flush.
_COCOA_CACHE_PREFIX = f"synth-cache/cocoa-abstractions/{COCOA_PROMPT_VERSION}"

_DD_PROCESS_EXPLAINER = "dd-cocoa-explainer"
_DD_PROCESS_JUDGE     = "dd-cocoa-judge"

# A chapter's total subtopic count caps the batched call sizes. Anything
# above ~80 subtopics gets sliced into chunks so the prompts stay <30k tokens.
_MAX_SUBTOPICS_PER_BATCH = 60

# Generous token budgets — chapters can carry 50+ code blocks and we'd
# rather pay than truncate.
_EXPLAINER_MAX_TOKENS = 8000
_JUDGE_MAX_TOKENS     = 4000
_EXPLAINER_TEMPERATURE = 0.0
_JUDGE_TEMPERATURE     = 0.0

# Pass threshold for CoCoA — fraction of aligned subtopics required for
# c11/c12 to remain at the bundled-judge's verdict. Below this, CoCoA
# overrides with FAIL.
_ALIGN_PASS_FRACTION = 0.85

# Per-code-body excerpt cap when rendering code blocks into the explainer
# prompt. Most code blocks are well under 600 chars; the cap is defense.
_CODE_EXCERPT_CHARS = 1200


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


# =============================================================================
# Stage 1 — Explainer: abstract every code block into a behavioral NL spec
# =============================================================================
_EXPLAINER_PROMPT = """You are the Code Explainer (CoCoA stage 1).

For each code snippet below, write ONE concise behavioral abstraction (1
sentence, 12-30 words) describing WHAT the code does and which key
identifiers (function names, decorators, types, parameters) appear in it.
This abstraction is NOT user-facing prose — it's a structured spec the
judge will compare against the documentation explanation.

OUTPUT — strict JSON, exactly this shape:
{{
  "abstractions": [
    {{"id": "<the integer id from the input>",
      "spec": "<1-sentence behavioral abstraction naming key identifiers>"}},
    ...
  ]
}}

Cover EVERY input id. No prose outside JSON.

== CODE BLOCKS ==
{blocks_block}
== END CODE BLOCKS =="""


def _render_blocks_for_explainer(blocks: list[dict]) -> str:
    parts: list[str] = []
    for b in blocks:
        bid = b["id"]
        lang = b.get("lang") or ""
        body = (b.get("body") or "")[:_CODE_EXCERPT_CHARS]
        parts.append(
            f"[id={bid}, lang={lang}]\n```{lang}\n{body}\n```"
        )
    return "\n\n".join(parts)


async def _read_cached_abstraction(minio, h: str) -> str | None:
    """Return the cached spec for hash `h`, or None on miss / error.
    Per-hash blobs are tiny JSON ({spec: "..."}); read latency is
    dominated by MinIO RTT (~5-15 ms). Even an "all miss" pass over a
    chapter of ~50 blocks costs <1s total — much cheaper than a single
    LLM call."""
    key = f"{_COCOA_CACHE_PREFIX}/{h}.json"
    try:
        if not await minio.exists(key):
            return None
        raw = await minio.read_text(key)
        obj = json.loads(raw)
        spec = (obj.get("spec") or "").strip()
        return spec or None
    except Exception:
        return None


async def _write_cached_abstraction(minio, h: str, spec: str) -> None:
    """Best-effort cache write. Failures are silent — the abstraction
    still works for this run, we just miss the cache for future ones."""
    if not h or not spec:
        return
    key = f"{_COCOA_CACHE_PREFIX}/{h}.json"
    try:
        await minio.write(
            key,
            json.dumps({"spec": spec}),
            content_type="application/json",
        )
    except Exception as e:
        logger.debug(
            f"[cocoa] cache write failed for {h[:8]}…: "
            f"{type(e).__name__}: {e}"
        )


async def _explain_blocks(blocks: list[dict]) -> dict[str, str]:
    """Run the explainer on a batch of code blocks. Returns {id_str: spec}.

    Bundle 5 (2026-05-25) — per-hash MinIO cache:
      - Verbatim blocks (default `code_source`) get cache lookup by
        their vault hash. Same hash + same prompt_version = guaranteed
        same spec.
      - Derived blocks (sawc_derive promoted) have AI-generated bodies
        that vary across runs even for the same originating hash, so
        they always go to the LLM (no caching).
      - Misses get one batched LLM call covering ALL miss ids; new specs
        get written back to cache for future runs.

    Fail-soft: empty dict on any error — caller treats unspecced blocks
    as "alignment unknown" (passes through without overriding the bundled
    judge).
    """
    if not blocks:
        return {}

    minio = get_storage()

    # Partition into cache hits vs misses. Blocks without `hash` or
    # flagged `code_source='derived'` go straight to the LLM (no cache).
    cached: dict[str, str] = {}
    misses: list[dict] = []
    for b in blocks:
        h = (b.get("hash") or "").strip()
        is_derived = (b.get("code_source") or "verbatim") == "derived"
        if h and not is_derived:
            spec = await _read_cached_abstraction(minio, h)
            if spec:
                cached[b["id"]] = spec
                continue
        misses.append(b)

    if cached:
        logger.info(
            f"[cocoa] explainer cache: {len(cached)}/{len(blocks)} "
            f"hits ({len(misses)} miss); LLM call will cover misses"
        )

    # If everything was cached, skip the LLM call entirely.
    if not misses:
        return cached

    prompt = _EXPLAINER_PROMPT.format(
        blocks_block=_render_blocks_for_explainer(misses),
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens=_EXPLAINER_MAX_TOKENS,
            temperature=_EXPLAINER_TEMPERATURE,
            dd_process=_DD_PROCESS_EXPLAINER,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(
            f"[cocoa] explainer call failed: {type(e).__name__}: {e}"
        )
        return cached    # ship whatever we had cached; bundled judge stands
    parsed = _parse_json(response or "")
    if not parsed:
        return cached
    fresh: dict[str, str] = {}
    for row in (parsed.get("abstractions") or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        spec = str(row.get("spec") or "").strip()
        if rid and spec:
            fresh[rid] = spec

    # Write back the fresh ones to cache (only for verbatim blocks with
    # a hash — derived blocks are unsafe to cache by hash since their
    # bodies vary).
    miss_by_id = {b["id"]: b for b in misses}
    for rid, spec in fresh.items():
        b = miss_by_id.get(rid)
        if not b:
            continue
        h = (b.get("hash") or "").strip()
        is_derived = (b.get("code_source") or "verbatim") == "derived"
        if h and not is_derived:
            await _write_cached_abstraction(minio, h, spec)

    # Merge cache hits + fresh; caller doesn't need to know which is which.
    return {**cached, **fresh}


# =============================================================================
# Stage 2 — Judge: align prose explanation against the explainer's spec
# =============================================================================
_JUDGE_PROMPT = """You are the Alignment Judge (CoCoA stage 2).

For each row below, decide if the documentation EXPLANATION faithfully
describes the BEHAVIORAL SPEC of its associated code block. Both fields
were produced from the same source code; the spec is a reliable
abstraction of what that code does.

Pass criteria:
  - The explanation names ≥1 identifier from the spec (function name,
    decorator, type, parameter) AND
  - The explanation's main claim is consistent with the spec — same
    function, same purpose, no APIs invented that aren't in the spec.

Fail criteria (any one is sufficient):
  - The explanation describes a DIFFERENT API than the spec covers.
  - The explanation mentions identifiers that aren't in the spec.
  - The explanation is generic filler with no code-anchored detail.

OUTPUT — strict JSON, exactly this shape:
{{
  "verdicts": [
    {{"id": "<the integer id>",
      "aligned": true | false,
      "reason": "<short string; required when aligned=false, optional otherwise>"}},
    ...
  ]
}}

Cover EVERY input id. No prose outside JSON. Be strict — when in doubt,
prefer FAIL with a specific reason naming the drift.

== PAIRS ==
{pairs_block}
== END PAIRS =="""


def _render_pairs_for_judge(pairs: list[dict]) -> str:
    parts: list[str] = []
    for p in pairs:
        pid = p["id"]
        spec = (p.get("spec") or "").strip()
        sub  = (p.get("subheading") or "").strip()
        expl = (p.get("explanation") or "").strip()
        parts.append(
            f"[id={pid}]\n"
            f"  SUBHEADING:  {sub}\n"
            f"  EXPLANATION: {expl}\n"
            f"  SPEC:        {spec}"
        )
    return "\n\n".join(parts)


async def _judge_pairs(pairs: list[dict]) -> dict[str, dict]:
    """Returns {id_str: {"aligned": bool, "reason": str}}. Failures fall
    through to {} (alignment unknown for missing ids → treated as PASS)."""
    if not pairs:
        return {}
    prompt = _JUDGE_PROMPT.format(
        pairs_block=_render_pairs_for_judge(pairs),
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens=_JUDGE_MAX_TOKENS,
            temperature=_JUDGE_TEMPERATURE,
            dd_process=_DD_PROCESS_JUDGE,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(
            f"[cocoa] judge call failed: {type(e).__name__}: {e}"
        )
        return {}
    parsed = _parse_json(response or "")
    if not parsed:
        return {}
    out: dict[str, dict] = {}
    for row in (parsed.get("verdicts") or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        out[rid] = {
            "aligned": bool(row.get("aligned")),
            "reason": str(row.get("reason") or "").strip(),
        }
    return out


# =============================================================================
# Public entrypoint
# =============================================================================
async def cocoa_alignment_check(
    *,
    sawc_payload: dict,
    vault: dict[str, str],
) -> dict:
    """Run CoCoA two-stage alignment over every (subtopic, code) pair in
    the chapter.

    Args:
      sawc_payload: parsed sawc-latest.json content.
      vault: {hash: fence_text} merged vault — same lookup the renderer
        uses. Subtopics whose hash isn't in the vault are skipped (the
        round-trip audit catches those as missing).

    Returns:
      {
        "passed":              bool,    # ≥85% aligned
        "method":              "cocoa_v1",
        "n_pairs":             int,
        "n_aligned":           int,
        "n_misaligned":        int,
        "alignment_rate":      float,   # 0-1
        "misaligned":          [{"subheading": str, "reason": str, ...}, ...],
        "feedback":            str,     # 1-sentence summary for mgsr_replan
      }

    Fail-soft: any infrastructure failure returns passed=True with
    method='cocoa_skipped' so the bundled judge stays authoritative.
    """
    sections = sawc_payload.get("sections") or []

    # Build (subtopic, code body) pairs. Derived subtopics use their
    # derived_code directly; verbatim subtopics resolve via vault. We index
    # by a stable integer id so the JSON round-trip is robust.
    pairs: list[dict] = []   # input rows the LLM stages consume
    for s in sections:
        sub_list = s.get("subtopics") or []
        for st in sub_list:
            if not isinstance(st, dict):
                continue
            subheading = (st.get("subheading") or "").strip()
            explanation = (st.get("explanation") or "").strip()
            h = (st.get("code_ref_hash") or "").strip()
            code_source = st.get("code_source") or "verbatim"
            derived = (st.get("derived_code") or "").strip()
            if code_source == "derived" and derived:
                body = derived
                lang = "python"
            else:
                body = vault.get(h, "") or ""
                # vault entries include fences — strip them for the
                # explainer prompt (cleaner abstraction).
                body = _strip_fences(body)
                lang = _detect_lang(vault.get(h, ""))
            if not (subheading and explanation and body):
                continue
            pairs.append({
                "id":          str(len(pairs)),
                "hash":        h,            # for stage-1 per-hash cache
                "code_source": code_source,  # derived blocks skip cache
                "subheading":  subheading,
                "explanation": explanation,
                "lang":        lang,
                "body":        body,
                "section_id":  s.get("section_id", "?"),
            })

    n_pairs = len(pairs)
    if n_pairs == 0:
        return {
            "passed":         True,
            "method":         "cocoa_skipped",
            "n_pairs":        0,
            "n_aligned":      0,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "no subtopics with both code body + prose",
        }

    # Slice by _MAX_SUBTOPICS_PER_BATCH so prompts don't balloon.
    batches: list[list[dict]] = [
        pairs[i:i + _MAX_SUBTOPICS_PER_BATCH]
        for i in range(0, n_pairs, _MAX_SUBTOPICS_PER_BATCH)
    ]

    # Stage 1: explainer over all blocks (per-hash MinIO cache absorbs
    # repeat work across iters/chapters/frameworks).
    specs: dict[str, str] = {}
    for batch in batches:
        blocks = [
            {
                "id":          p["id"],
                "hash":        p.get("hash") or "",
                "code_source": p.get("code_source") or "verbatim",
                "lang":        p["lang"],
                "body":        p["body"],
            }
            for p in batch
        ]
        partial = await _explain_blocks(blocks)
        specs.update(partial)

    if not specs:
        return {
            "passed":         True,    # fail-soft — don't override bundled
            "method":         "cocoa_skipped",
            "n_pairs":        n_pairs,
            "n_aligned":      n_pairs,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "cocoa explainer failed; bundled judge stands",
        }

    # Stage 2: judge across all pairs that received an abstraction.
    judge_input: list[dict] = []
    for p in pairs:
        spec = specs.get(p["id"])
        if not spec:
            continue
        judge_input.append({
            "id":          p["id"],
            "spec":        spec,
            "subheading":  p["subheading"],
            "explanation": p["explanation"],
        })

    verdicts: dict[str, dict] = {}
    for i in range(0, len(judge_input), _MAX_SUBTOPICS_PER_BATCH):
        batch = judge_input[i:i + _MAX_SUBTOPICS_PER_BATCH]
        partial = await _judge_pairs(batch)
        verdicts.update(partial)

    if not verdicts:
        return {
            "passed":         True,
            "method":         "cocoa_skipped",
            "n_pairs":        n_pairs,
            "n_aligned":      n_pairs,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "cocoa judge failed; bundled judge stands",
        }

    n_aligned = 0
    misaligned: list[dict] = []
    by_id = {p["id"]: p for p in pairs}
    for pid, v in verdicts.items():
        p = by_id.get(pid)
        if p is None:
            continue
        if v.get("aligned", True):
            n_aligned += 1
        else:
            misaligned.append({
                "section_id": p.get("section_id"),
                "subheading": p["subheading"],
                "reason": v.get("reason") or "explanation does not ground to the cited code",
            })

    n_judged = n_aligned + len(misaligned)
    rate = (n_aligned / n_judged) if n_judged else 1.0
    passed = rate >= _ALIGN_PASS_FRACTION

    feedback = ""
    if not passed:
        sample = [
            f"{m['subheading']!r} ({m['reason'][:80]})"
            for m in misaligned[:3]
        ]
        feedback = (
            f"CoCoA: {n_aligned}/{n_judged} subtopics aligned "
            f"({rate:.0%}; floor {_ALIGN_PASS_FRACTION:.0%}). "
            f"Sample drift: {sample}. mgsr_replan should re-roll those "
            f"sections with stronger code-grounded prose."
        )

    return {
        "passed":         passed,
        "method":         "cocoa_v1",
        "n_pairs":        n_pairs,
        "n_aligned":      n_aligned,
        "n_misaligned":   len(misaligned),
        "alignment_rate": rate,
        "misaligned":     misaligned[:50],   # cap for blob size
        "feedback":       feedback,
    }


# =============================================================================
# Tiny helpers
# =============================================================================
def _strip_fences(s: str) -> str:
    """Strip leading/trailing ```lang ... ``` fence markers from a vault
    body so the explainer sees clean code."""
    s = (s or "").strip()
    if not s.startswith("```"):
        return s
    parts = s.split("\n")
    if not parts:
        return s
    # Drop the first fence line and the trailing fence line if present.
    body = parts[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body)


def _detect_lang(s: str) -> str:
    """Pull the lang from a ```python\n...``` fence; default 'python'."""
    if not s:
        return "python"
    head = s.lstrip().split("\n", 1)[0]
    if head.startswith("```"):
        return head[3:].strip() or "python"
    return "python"
