"""Pure functions for the RR agent's code_synth tool — no I/O, no event loop."""
from __future__ import annotations

import re
from typing import Any


def as_text(v: Any) -> str:
    """Coerce a JSONB / extraction field to a string regardless of shape.

    The schema says every extraction field is a string, but in practice
    some rotator arms emit arrays (`["step 1", "step 2"]` for `method`,
    `["$E = mc^2$", "$\\nabla \\cdot E = \\rho/\\epsilon_0$"]` for
    `math`, etc.) and Postgres JSONB happily stores them. Without this
    coercion, `build_user_message` crashes on `.strip()` with the
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
        parts = [as_text(item) for item in v]
        return "\n".join(p for p in parts if p).strip()
    if isinstance(v, dict):
        import json
        try:
            return json.dumps(v, ensure_ascii=False, indent=2).strip()
        except Exception:
            return str(v).strip()
    return str(v).strip()


def build_user_message(finding: dict[str, Any], extraction: dict[str, Any]) -> str:
    """Compose the structured-augmentation prompt. Order matters: the
    `money_angle` is injected FIRST so the model anchors on the practical
    hook before the algorithmic details (CodeScout 2603.05744 finding —
    structured fields work best when the commercial framing leads)."""
    title    = as_text(finding.get("title")) or "(untitled)"
    arxiv_id = as_text(finding.get("arxiv_id"))
    money    = as_text(extraction.get("money_angle"))
    problem  = as_text(extraction.get("problem"))
    method   = as_text(extraction.get("method"))
    how      = as_text(extraction.get("how_to_build"))
    math     = as_text(extraction.get("math"))
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


def content_to_text(response: Any) -> str:
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


def extract_python_block(raw: str) -> str:
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


def resolve_model_id(response: Any) -> str | None:
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
