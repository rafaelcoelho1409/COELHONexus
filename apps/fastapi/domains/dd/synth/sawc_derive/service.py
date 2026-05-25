"""sawc_derive service — pure-function helpers (no I/O, no LLM calls).

Pieces:
  - `is_thin_block(body)`        — signature/length heuristic
  - `build_analogical_prompt`    — Yasunaga 2023 (arXiv:2310.01714)
  - `parse_code_block`           — strip fences, language-aware
  - `score_derived_candidate`    — AST-validity + LOC + import richness
  - `rank_mpsc_samples`          — N candidates → winning index (or None)
  - `python_ast_valid(body)`     — boolean compile gate

All decisions are deterministic given the inputs. The node module
threads I/O (bandit rotator, MinIO persistence, Redis cancel flags).
"""
from __future__ import annotations

import ast
import re

from .constants import (
    _DERIVED_MAX_CHARS,
    _DERIVED_MAX_LINES,
    _DERIVED_MIN_CHARS,
    _DERIVED_MIN_LINES,
    _SIGNATURE_ONLY_RE,
    _THIN_MAX_CHARS,
    _THIN_MAX_NEWLINES,
)


# =============================================================================
# Thin-block detection
# =============================================================================
def is_thin_block(body: str) -> bool:
    """True when a vault code body is too thin to teach effectively.

    "Thin" = signature-only OR very short. The two-gate AND lets a
    short example like a 4-line snippet through, while catching:
        list_skills(client: Client) -> list[SkillSummary]
    and similar single-line API references.

    The heuristic is conservative on purpose — we'd rather miss a
    derive opportunity than over-fire and re-generate already-good
    code blocks.
    """
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    n_newlines = stripped.count("\n")
    if len(stripped) > _THIN_MAX_CHARS:
        return False
    if n_newlines > _THIN_MAX_NEWLINES:
        return False
    # Single non-empty line that looks like a signature → thin.
    if n_newlines == 0 and _SIGNATURE_ONLY_RE.match(stripped):
        return True
    # 1-2 newlines but content fits the signature shape line-wise — also thin.
    if n_newlines <= _THIN_MAX_NEWLINES:
        non_empty_lines = [
            ln for ln in stripped.splitlines() if ln.strip()
        ]
        if len(non_empty_lines) <= 2 and all(
            _SIGNATURE_ONLY_RE.match(ln.strip()) for ln in non_empty_lines
        ):
            return True
    # Otherwise, fall through — short but isn't a pure signature.
    return False


# =============================================================================
# Prompt builder — Analogical Prompting (Yasunaga 2023 arXiv:2310.01714)
# =============================================================================
def build_reexplain_prompt(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
    lang: str = "python",
) -> str:
    """Ship D (2026-05-25): after MPSC promotes a derived code block,
    the original explanation (written for the thin signature) is stale —
    it describes APIs/params from the signature, not the expanded
    example. This prompt regenerates the explanation conditioned on
    the new code body.

    Per the deep research (Citation-Grounded Code Comprehension arXiv
    2512.12117): prose grounded to the resolved code beats prose grounded
    to an imagined topic. The re-explain call mirrors the Ship A "hash
    first, prose second" ordering — the code is already chosen; we just
    rewrite the prose to match.
    """
    return (
        f"You are regenerating ONE documentation explanation in a "
        f"{framework} learning resource. The code block below has been "
        f"newly AI-generated to expand a thin signature; the old "
        f"explanation no longer describes it. Write a fresh explanation "
        f"that grounds to THIS specific code.\n\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n\n"
        f"OLD EXPLANATION (stale — describes a different example):\n"
        f"{old_explanation.strip()}\n\n"
        f"NEW CODE BLOCK:\n"
        f"```{lang}\n{derived_code.strip()}\n```\n\n"
        f"== TASK ==\n"
        f"Write a NEW explanation (8-80 words, 1-3 sentences) that:\n"
        f"  1. Describes WHAT this specific code block demonstrates.\n"
        f"  2. References at least ONE identifier visible in the code "
        f"(function name, decorator, type, parameter, or imported "
        f"symbol).\n"
        f"  3. Reads as prose that goes IMMEDIATELY BEFORE the code in a "
        f"cookbook chapter.\n"
        f"  4. NO code fences, NO inline `code-ref` tags, NO meta-framing "
        f"('In this example...'). Just the explanation.\n\n"
        f"OUTPUT: strict JSON, exactly: "
        f'{{"explanation": "your rewritten 8-80 word explanation here"}}\n'
        f"NO prose commentary outside JSON."
    )


def build_analogical_prompt(
    *,
    framework: str,
    chapter_title: str,
    section_heading: str,
    subheading: str,
    explanation: str,
    original_body: str,
    original_lang: str = "python",
) -> str:
    """Analogical Prompting prompt — ask the LLM to first reason about
    a relevant, expanded example by analogy, then emit it as a fenced
    code block.

    Per Yasunaga et al. 2023 ("Large Language Models as Analogical
    Reasoners"), letting the model first describe a closely-related
    canonical example improves derived-code quality vs. one-shot
    generation. We don't need the reasoning text in the output —
    we strip everything outside the final fenced block server-side.

    Output contract: exactly one fenced code block in the response.
    Anything outside the fence is discarded.
    """
    return (
        f"You are expanding a thin documentation reference into a "
        f"COMPLETE RUNNABLE EXAMPLE for a {framework} learning resource.\n\n"
        f"CHAPTER: {chapter_title}\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n"
        f"PROSE LEAD-IN (already written, do NOT repeat): "
        f"{explanation}\n\n"
        f"== ORIGINAL DOC REFERENCE (too thin to teach) ==\n"
        f"```{original_lang}\n"
        f"{original_body.strip()}\n"
        f"```\n\n"
        f"== TASK ==\n"
        f"Think about ONE common production use-case that exercises this "
        f"API. By analogy to that use-case, write a self-contained, "
        f"runnable {original_lang} example demonstrating realistic usage. "
        f"Show real imports, real arguments, real return-value handling.\n\n"
        f"== HARD RULES ==\n"
        f"1. Output EXACTLY ONE fenced ```{original_lang} ... ``` block. "
        f"NO prose before, after, or between fences.\n"
        f"2. The code MUST parse as valid {original_lang} (AST validates "
        f"it server-side; ungated samples are discarded).\n"
        f"3. Length: 4-50 non-blank lines. Tight, focused, teachable.\n"
        f"4. INCLUDE imports for any types/decorators used.\n"
        f"5. Use REAL function/method names from {framework} — do NOT "
        f"invent APIs. If unsure, mirror the surface from the original "
        f"reference above; expand parameter names + types realistically.\n"
        f"6. NO placeholders like '...', 'YOUR_KEY_HERE', '# TODO'. "
        f"Concrete, usable values everywhere.\n"
        f"7. NO test scaffolding (no `assert`, no `unittest`, no "
        f"`pytest.mark`). Production-style code only.\n"
        f"8. NO inline comments explaining what the code does line-by-"
        f"line — the prose lead-in already framed it.\n\n"
        f"Respond with the fenced code block ONLY."
    )


# =============================================================================
# Code-block extraction
# =============================================================================
_FENCE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)\n```",
    re.DOTALL,
)


def parse_code_block(raw: str) -> str:
    """Extract the first fenced code block from an LLM response.

    Returns the inner body (no fences). Empty string if no fenced
    block is present — the caller treats that as a failed sample.
    """
    if not raw:
        return ""
    m = _FENCE_RE.search(raw)
    if not m:
        # Last-resort fallback: if the whole response is plausibly
        # bare code (no fences at all), return it. AST parse downstream
        # is the real gate.
        stripped = raw.strip()
        if "```" not in stripped and stripped:
            return stripped
        return ""
    return m.group(1).rstrip("\n")


# =============================================================================
# AST validity gate (Python-only for now; sawc_write subtopics
# overwhelmingly target python — extend per-lang if framework demands)
# =============================================================================
def python_ast_valid(body: str) -> bool:
    """True iff `body` parses as valid Python (incl. async). Catches
    hallucinated names, malformed signatures, broken imports."""
    if not body or not body.strip():
        return False
    try:
        ast.parse(body)
        return True
    except SyntaxError:
        return False
    except Exception:
        # Any other parser-internal failure → treat as invalid.
        return False


# =============================================================================
# Per-sample scoring
# =============================================================================
def score_derived_candidate(body: str) -> float:
    """Deterministic structural score for one derived candidate.

    Higher = better. Used by `rank_mpsc_samples` to break ties among
    AST-valid samples.

    Components (~0-10 scale):
      + AST valid:        4.0
      + In LOC band:      2.0
      + Has imports:      1.5  (real lib usage signal)
      + Multi-line:       1.0  (not a one-shot expression)
      − Excess length:    up to -2.0 (penalize blobs >40 lines)
      − Placeholder leak: -3.0 (`...`, `YOUR_*_HERE`, `# TODO`, etc.)
    """
    if not body or not body.strip():
        return -10.0
    score = 0.0
    if python_ast_valid(body):
        score += 4.0
    lines = [ln for ln in body.splitlines() if ln.strip()]
    n_lines = len(lines)
    if _DERIVED_MIN_LINES <= n_lines <= _DERIVED_MAX_LINES:
        score += 2.0
    n_imports = sum(
        1 for ln in lines
        if re.match(r"^\s*(?:from\s+\w+|import\s+\w+)", ln)
    )
    if n_imports >= 1:
        score += 1.5
    if n_lines >= 3:
        score += 1.0
    if n_lines > 40:
        score -= min(2.0, (n_lines - 40) * 0.1)
    # Placeholder leaks — clear hallmarks of unfinished code.
    placeholders = (
        "YOUR_KEY_HERE", "YOUR_API_KEY", "# TODO", "# FIXME",
        "pass  # implement", "raise NotImplementedError",
    )
    body_lower = body
    for p in placeholders:
        if p in body_lower:
            score -= 3.0
            break
    if re.search(r"^\s*\.{3}\s*$", body, re.MULTILINE):
        score -= 3.0
    return round(score, 3)


# =============================================================================
# MPSC ranker — Multi-Path Self-Consistency (arXiv 2503.04611)
# =============================================================================
def rank_mpsc_samples(samples: list[str]) -> tuple[int | None, list[float]]:
    """Pick the best AST-valid sample by structural score.

    Returns (chosen_idx, scores). chosen_idx is None when no sample is
    both AST-valid AND in the LOC band — caller then records the attempt
    as `rejected_ast` (no AST pass) or `rejected_len` (AST passed but
    nothing in band).
    """
    if not samples:
        return None, []
    scores = [score_derived_candidate(s) for s in samples]
    # Require AST validity + length-band; pick highest score among those.
    valid_idxs = [
        i for i, s in enumerate(samples)
        if python_ast_valid(s)
    ]
    if not valid_idxs:
        return None, scores
    in_band: list[int] = []
    for i in valid_idxs:
        body = samples[i]
        n_lines = sum(1 for ln in body.splitlines() if ln.strip())
        n_chars = len(body)
        if (_DERIVED_MIN_LINES <= n_lines <= _DERIVED_MAX_LINES
                and _DERIVED_MIN_CHARS <= n_chars <= _DERIVED_MAX_CHARS):
            in_band.append(i)
    if not in_band:
        return None, scores
    chosen = max(in_band, key=lambda i: scores[i])
    return chosen, scores
