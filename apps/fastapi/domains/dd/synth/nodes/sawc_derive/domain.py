"""sawc_derive — pure helpers (thin-block detection, fence parsing, AST
validation, structural scoring, MPSC ranker, derive-gate predicate)."""
from __future__ import annotations

import ast

from .params import (
    DERIVED_MAX_CHARS,
    DERIVED_MAX_LINES,
    DERIVED_MIN_CHARS,
    DERIVED_MIN_LINES,
    THIN_MAX_CHARS,
    THIN_MAX_NEWLINES,
)
from .patterns import (
    FENCE_RE,
    IMPORT_RE,
    LONE_ELLIPSIS_RE,
    SIGNATURE_ONLY_RE,
)


def is_thin_block(body: str) -> bool:
    """True when a vault code body is too thin to teach effectively.

    "Thin" = signature-only OR very short. The two-gate AND lets a short
    example like a 4-line snippet through, while catching:
        list_skills(client: Client) -> list[SkillSummary]
    and similar single-line API references.

    The heuristic is conservative on purpose — we'd rather miss a derive
    opportunity than over-fire and re-generate already-good code blocks.
    """
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    n_newlines = stripped.count("\n")
    if len(stripped) > THIN_MAX_CHARS:
        return False
    if n_newlines > THIN_MAX_NEWLINES:
        return False
    # Single non-empty line that looks like a signature → thin.
    if n_newlines == 0 and SIGNATURE_ONLY_RE.match(stripped):
        return True
    # 1-2 newlines but content fits the signature shape line-wise — also thin.
    if n_newlines <= THIN_MAX_NEWLINES:
        non_empty_lines = [
            ln for ln in stripped.splitlines() if ln.strip()
        ]
        if len(non_empty_lines) <= 2 and all(
            SIGNATURE_ONLY_RE.match(ln.strip()) for ln in non_empty_lines
        ):
            return True
    # Otherwise, fall through — short but isn't a pure signature.
    return False


def parse_code_block(raw: str) -> str:
    """Extract the first fenced code block from an LLM response. Returns
    the inner body (no fences). Empty string if no fenced block is
    present — the caller treats that as a failed sample."""
    if not raw:
        return ""
    m = FENCE_RE.search(raw)
    if not m:
        # Last-resort fallback: if the whole response is plausibly bare
        # code (no fences at all), return it. AST parse downstream is
        # the real gate.
        stripped = raw.strip()
        if "```" not in stripped and stripped:
            return stripped
        return ""
    return m.group(1).rstrip("\n")


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
    if DERIVED_MIN_LINES <= n_lines <= DERIVED_MAX_LINES:
        score += 2.0
    n_imports = sum(1 for ln in lines if IMPORT_RE.match(ln))
    if n_imports >= 1:
        score += 1.5
    if n_lines >= 3:
        score += 1.0
    if n_lines > 40:
        score -= min(2.0, (n_lines - 40) * 0.1)
    placeholders = (
        "YOUR_KEY_HERE", "YOUR_API_KEY", "# TODO", "# FIXME",
        "pass  # implement", "raise NotImplementedError",
    )
    for p in placeholders:
        if p in body:
            score -= 3.0
            break
    if LONE_ELLIPSIS_RE.search(body):
        score -= 3.0
    return round(score, 3)


def rank_mpsc_samples(
    samples: list[str],
) -> tuple[int | None, list[float]]:
    """Pick the best AST-valid sample by structural score.

    Returns (chosen_idx, scores). chosen_idx is None when no sample is
    both AST-valid AND in the LOC band — caller then records the attempt
    as `rejected_ast` (no AST pass) or `rejected_len` (AST passed but
    nothing in band)."""
    if not samples:
        return None, []
    scores = [score_derived_candidate(s) for s in samples]
    valid_idxs = [
        i for i, s in enumerate(samples) if python_ast_valid(s)
    ]
    if not valid_idxs:
        return None, scores
    in_band: list[int] = []
    for i in valid_idxs:
        body = samples[i]
        n_lines = sum(1 for ln in body.splitlines() if ln.strip())
        n_chars = len(body)
        if (
            DERIVED_MIN_LINES <= n_lines <= DERIVED_MAX_LINES
            and DERIVED_MIN_CHARS <= n_chars <= DERIVED_MAX_CHARS
        ):
            in_band.append(i)
    if not in_band:
        return None, scores
    chosen = max(in_band, key = lambda i: scores[i])
    return chosen, scores


def body_passes_derive_gate(body: str) -> bool:
    """Deterministic 'good enough' gate for Optimal-Stopping on MPSC.
    Used by sawc_derive's service layer to short-circuit the remaining
    MPSC samples when sample 0 is already shippable."""
    if not body:
        return False
    if not python_ast_valid(body):
        return False
    n_chars = len(body)
    if not (DERIVED_MIN_CHARS <= n_chars <= DERIVED_MAX_CHARS):
        return False
    n_lines = sum(1 for ln in body.splitlines() if ln.strip())
    if not (DERIVED_MIN_LINES <= n_lines <= DERIVED_MAX_LINES):
        return False
    return True
