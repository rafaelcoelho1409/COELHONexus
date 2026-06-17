"""Build-tab code synthesis — paper-extraction → complete Python.

Reads the 5 structured fields a deep_read produced (money_angle, problem,
method, how_to_build, math) and asks the rotator's `rr-strong` pool to
write COMPLETE, runnable Python that the operator reads to spark ideas.

Design notes (June 2026 SOTA distilled from a focused web sweep):

  - We have the plan already (the 5 extraction fields), so we SKIP
    plan-then-code (PlanSearch / Chain-of-Grounded-Objectives style)
    and inject the fields as structured-augmentation context. CodeScout
    (arxiv 2603.05744) shows pipeline-injected fields beat self-explore
    on this exact shape of task.

  - The goal is COMPLETE code, not a stub. The user wants to READ the
    result and get new ideas — they should not need to fill in gaps,
    chase TODOs, or implement methods marked `pass`. The system prompt
    therefore forbids `TODO`, `pass`, `...`, `NotImplementedError`, and
    "fill in" placeholders.

  - We run Self-Refine 1 round (Madaan et al., NeurIPS 2023). For a
    stub the second pass would erase desirable TODOs, but for complete
    code it FORCES the model to plug any remaining holes — the critique
    explicitly scans for placeholders and the revise pass fills them.
    +20% avg quality across 7 tasks at 2x token cost — worth it under
    [[feedback_kd_quality_over_speed]] ("tokens are free; runtime isn't
    a concern").

  - Single ```python fenced block on output. Hybrid free-form scratchpad
    + post-extract first fenced block. We don't force JSON wrapping —
    that degrades Python quality (March 2026 vLLM/SGLang consensus).

  - Bandit pool: build_rr_strong_chain_bandit() — same FGTS-VA brain
    Planner/Synth use. Qwen3-Coder-480B-A35B-Instruct is the strongest
    Python coder in the rr-strong pool as of 2026-06; the bandit will
    converge on it for code_synth calls without us hardcoding a choice.

Cache-invalidation contract: bump `CODE_SYNTH_PROMPT_VERSION` whenever
the system prompt or refine-loop logic changes. MinIO keys embed the
version so old cached outputs don't shadow the new prompt.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Bump this on every prompt or refine-logic change so MinIO cache misses
# repopulate against the new behavior. Old objects sit alongside the new
# ones (cheap) — operator can wipe `rr/scans/{id}/code/` to GC.
CODE_SYNTH_PROMPT_VERSION: str = "v1"


# --------------------------------------------------------------------------- #
# Prompts — see module docstring for the design rationale
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = """You are a senior Python engineer. You read research-paper extractions and write a COMPLETE, runnable Python file that demonstrates the paper's idea so the reader can extend it.

Hard rules (violating ANY of these makes your output unusable):
  - Write COMPLETE code. No `TODO`, no `pass`, no `...`, no `raise NotImplementedError`, no "fill in here" comments. Every function body is implemented end-to-end.
  - Translate the math into actual NumPy / PyTorch operations. If the extraction's `math` field has a formula, that formula appears in the code as real ops, not a comment.
  - Implement the algorithm from the `method` field — the core routine is fully written, not sketched.
  - Imports must be valid and installed: stdlib + numpy + torch + scipy + scikit-learn + matplotlib are fair game. Skip pip-install-required exotica (no `xgboost`, no random GitHub repos).
  - Include a `__main__` smoke example with synthetic data that exercises the full pipeline end-to-end. The reader should be able to copy-paste the file and `python file.py` it.
  - Anchor on the `money_angle` — the file's docstring and at least one comment should reflect the practical use case named there, not generic ML phrasing.
  - Use clear class / function names from the paper's domain. Add brief docstrings (1-3 lines) on every public symbol.
  - Length budget: 150 to 400 lines. Quality over brevity. If the algorithm is non-trivial, lean longer; never truncate.

Output format (strict):
  - Output EXACTLY ONE markdown code block fenced with ```python and ```.
  - No prose before or after the block.
  - No second code block.
"""


_CRITIQUE_PROMPT = """You are reviewing a Python file written from a paper extraction. Find every COMPLETENESS gap. List them as a numbered checklist — terse, one line each. If none, write "PASS".

Look specifically for:
  1. Any `TODO`, `pass`, `...`, `raise NotImplementedError`, or "fill in" placeholder.
  2. Functions that are declared but have empty/trivial bodies.
  3. Math from the extraction that's mentioned in a comment but not implemented in code.
  4. Imports of unavailable libraries (only stdlib + numpy + torch + scipy + scikit-learn + matplotlib are allowed).
  5. Missing or trivial `__main__` block — must run end-to-end with synthetic data.
  6. Docstrings that say "this implements X" but the function doesn't actually do X.
  7. Algorithm steps from the `method` field that are missing or hand-waved.

Be ruthless. The reader will judge the file by whether they can read it cover-to-cover and learn something concrete. Vague output is worse than no output."""


_REVISE_PROMPT = """Rewrite the Python file to fix every issue from the critique. Same hard rules as the original generation: complete code, no placeholders, valid imports, full `__main__` example, money-angle-anchored docstring, 150-400 lines.

Output EXACTLY ONE ```python ... ``` block. No prose."""


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
async def synth_code(finding: dict[str, Any]) -> dict[str, str]:
    """Generate complete Python from a finding's extraction.

    Args:
        finding: The radar_findings.digest_json row for one paper. Must carry
            `title`, `arxiv_id`, and an `extraction` dict with the 5 fields
            (money_angle, problem, method, how_to_build, math).

    Returns:
        {"code": "<python source>", "model_id": "<rotator-resolved arm>"}.
        Raises RuntimeError on empty output or fenced-block extraction
        failure — caller should NOT cache failures.
    """
    # Lazy imports — keep cold-start light and avoid pulling the rotator
    # into smoke tests that import this module.
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from domains.llm.rotator.chain.service import build_rr_strong_chain_bandit

    extraction = finding.get("extraction") or {}
    user_msg   = _build_user_message(finding, extraction)
    chain      = build_rr_strong_chain_bandit()

    # Round 1 — generate.
    gen_response = await chain.ainvoke([
        SystemMessage(content = _SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
    ])
    draft_raw   = _content_to_text(gen_response)
    if not draft_raw:
        raise RuntimeError("code_synth: empty draft from rotator")
    draft_code  = _extract_python_block(draft_raw)

    # Round 2 — critique (forces the model to surface completeness gaps).
    critique_response = await chain.ainvoke([
        SystemMessage(content = _SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
        AIMessage(content     = f"```python\n{draft_code}\n```"),
        HumanMessage(content  = _CRITIQUE_PROMPT),
    ])
    critique_raw = _content_to_text(critique_response)

    # If the critique says PASS, skip the revise round — saves a turn and
    # avoids the model "fixing" things that don't need fixing.
    if critique_raw.upper().startswith("PASS"):
        model_id = _resolve_model_id(gen_response) or "rr-strong"
        return {"code": draft_code, "model_id": model_id}

    # Round 3 — revise.
    revised_response = await chain.ainvoke([
        SystemMessage(content = _SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
        AIMessage(content     = f"```python\n{draft_code}\n```"),
        HumanMessage(content  = _CRITIQUE_PROMPT),
        AIMessage(content     = critique_raw),
        HumanMessage(content  = _REVISE_PROMPT),
    ])
    revised_raw = _content_to_text(revised_response)
    if not revised_raw:
        # Revise failed — fall back to the draft. Better to ship something
        # than to fail closed.
        logger.warning(
            f"code_synth: revise pass empty for arxiv_id="
            f"{finding.get('arxiv_id')!r}; returning draft"
        )
        model_id = _resolve_model_id(gen_response) or "rr-strong"
        return {"code": draft_code, "model_id": model_id}
    revised_code = _extract_python_block(revised_raw)
    model_id     = _resolve_model_id(revised_response) or "rr-strong"
    return {"code": revised_code, "model_id": model_id}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_text(v: Any) -> str:
    """Coerce a JSONB / extraction field to a string regardless of shape.

    The schema says every extraction field is a string, but in practice
    some rotator arms emit arrays (`["step 1", "step 2"]` for `method`,
    `["$E = mc^2$", "$\\nabla \\cdot E = \\rho/\\epsilon_0$"]` for
    `math`, etc.) and Postgres JSONB happily stores them. Without this
    coercion, `_build_user_message` crashes on `.strip()` with the
    exact error the operator saw:
        AttributeError: 'list' object has no attribute 'strip'

    str/None → trivial; list → "\n".join (so each item becomes a line);
    dict → JSON dump (rare but defensible); anything else → str()."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        # Recurse so nested lists / dicts still resolve cleanly.
        parts = [_as_text(item) for item in v]
        return "\n".join(p for p in parts if p).strip()
    if isinstance(v, dict):
        import json as _json
        try:
            return _json.dumps(v, ensure_ascii=False, indent=2).strip()
        except Exception:
            return str(v).strip()
    return str(v).strip()


def _build_user_message(finding: dict[str, Any], extraction: dict[str, Any]) -> str:
    """Compose the structured-augmentation prompt. Order matters: the
    `money_angle` is injected FIRST so the model anchors on the practical
    hook before the algorithmic details (CodeScout 2603.05744 finding —
    structured fields work best when the commercial framing leads)."""
    title    = _as_text(finding.get("title")) or "(untitled)"
    arxiv_id = _as_text(finding.get("arxiv_id"))
    money    = _as_text(extraction.get("money_angle"))
    problem  = _as_text(extraction.get("problem"))
    method   = _as_text(extraction.get("method"))
    how      = _as_text(extraction.get("how_to_build"))
    math     = _as_text(extraction.get("math"))
    return (
        f"Paper: {title}\n"
        f"arxiv: {arxiv_id}\n\n"
        f"# Money angle (the practical / commercial hook — anchor the file's "
        f"docstring and example here)\n{money or '(not provided)'}\n\n"
        f"# Problem\n{problem or '(not provided)'}\n\n"
        f"# Method (translate this into actual code — full implementation, "
        f"not a sketch)\n{method or '(not provided)'}\n\n"
        f"# How to build\n{how or '(not provided)'}\n\n"
        f"# Math (translate these formulas to NumPy / PyTorch ops in the "
        f"function bodies)\n{math or '(not provided)'}\n\n"
        f"Write the complete Python file now. One ```python block, no prose."
    )


def _content_to_text(response: Any) -> str:
    """Pull the text out of a LangChain message regardless of whether
    `.content` is a string or a list of content blocks.

    Some providers (Anthropic, certain NIM reasoning models, multimodal
    arms) return `content` as a list of dicts like
    `[{"type": "text", "text": "..."}, {"type": "thinking", ...}]`
    instead of a flat string. Calling `.strip()` on the list crashes
    with `AttributeError: 'list' object has no attribute 'strip'` — the
    exact error observed on the first live Build-tab call. This helper
    handles both shapes (and falls back to `str()` for anything exotic)
    so callers don't have to branch."""
    raw = getattr(response, "content", None)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for blk in raw:
            if isinstance(blk, str):
                parts.append(blk)
            elif isinstance(blk, dict):
                # LangChain v1 / Anthropic / OpenAI all use the
                # `{"type": "text", "text": "..."}` shape for text
                # blocks; we also accept `content` as a fallback key
                # some providers use.
                t = blk.get("text") or blk.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts).strip()
    return str(raw).strip()


_FENCED_PYTHON_RE = re.compile(r"```(?:python|py)?\s*\n(.+?)\n```", re.DOTALL)


def _extract_python_block(raw: str) -> str:
    """Pull the first ```python fenced block out of the model's output.
    Falls back to the whole content (stripped of optional bare fences) if
    no fenced block was found, so a model that emits plain code instead of
    fenced doesn't crash us. Raises only if the result is empty."""
    m = _FENCED_PYTHON_RE.search(raw)
    if m:
        code = m.group(1).strip()
    else:
        # No fenced block — try stripping bare fences if present, otherwise
        # accept the raw text. This is a fallback path; the prompt requires
        # the fence so we expect this to be rare.
        code = raw
        if code.startswith("```"):
            lines = code.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            code = "\n".join(lines).strip()
    if not code:
        raise RuntimeError("code_synth: extracted empty python block")
    return code


def _resolve_model_id(response: Any) -> str | None:
    """The bandit chain surfaces the resolved deployment id via
    `response_metadata.model_name` (LiteLLM convention) and/or
    `additional_kwargs.model`. Walk the common fields in priority order;
    return None if none populated so the caller can fall back."""
    meta = getattr(response, "response_metadata", None) or {}
    if isinstance(meta, dict):
        for key in ("model_name", "model", "model_id"):
            v = meta.get(key)
            if isinstance(v, str) and v:
                return v
    extra = getattr(response, "additional_kwargs", None) or {}
    if isinstance(extra, dict):
        for key in ("model", "model_id"):
            v = extra.get(key)
            if isinstance(v, str) and v:
                return v
    return None
