"""sawc_derive — pure derivation and capstone composition logic."""
from __future__ import annotations
from . import params, patterns

import ast



def fence_inner(fence_text: str) -> str:
    """The code between a vault entry's fence lines (a vault entry's `fence_text` is the whole fence; thinness and the deriver's reference are about the code)."""
    lines = (fence_text or "").strip("\n").splitlines()
    if len(lines) >= 2 and lines[0].lstrip().startswith(("```", "~~~")) and lines[-1].strip().startswith(("```", "~~~")):
        return "\n".join(lines[1:-1])
    return fence_text or ""


def is_thin_block(body: str) -> bool:
    """True when vault body is too thin to teach. Conservative: short AND signature-only gate so 4-line snippets pass while bare API signatures are caught."""
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    n_newlines = stripped.count("\n")
    if len(stripped) > params.THIN_MAX_CHARS:
        return False
    if n_newlines > params.THIN_MAX_NEWLINES:
        return False
    # Single non-empty line that looks like a signature → thin.
    if n_newlines == 0 and patterns.SIGNATURE_ONLY_RE.match(stripped):
        return True
    # 1-2 newlines but content fits the signature shape line-wise — also thin.
    if n_newlines <= params.THIN_MAX_NEWLINES:
        non_empty_lines = [
            ln for ln in stripped.splitlines() if ln.strip()
        ]
        if len(non_empty_lines) <= 2 and all(
            patterns.SIGNATURE_ONLY_RE.match(ln.strip()) for ln in non_empty_lines
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
    m = patterns.FENCE_RE.search(raw)
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
    """Structural score; higher=better. AST valid (+4), LOC band (+2), imports (+1.5), multi-line (+1); penalizes excess length and placeholder leaks (-3)."""
    if not body or not body.strip():
        return -10.0
    score = 0.0
    if python_ast_valid(body):
        score += 4.0
    lines = [ln for ln in body.splitlines() if ln.strip()]
    n_lines = len(lines)
    if params.DERIVED_MIN_LINES <= n_lines <= params.DERIVED_MAX_LINES:
        score += 2.0
    n_imports = sum(1 for ln in lines if patterns.IMPORT_RE.match(ln))
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
    if patterns.LONE_ELLIPSIS_RE.search(body):
        score -= 3.0
    return round(score, 3)


def rank_mpsc_samples(
    samples: list[str],
) -> tuple[int | None, list[float]]:
    """Pick best AST-valid sample by structural score. chosen_idx=None when no sample is both AST-valid AND in the LOC band."""
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
            params.DERIVED_MIN_LINES <= n_lines <= params.DERIVED_MAX_LINES
            and params.DERIVED_MIN_CHARS <= n_chars <= params.DERIVED_MAX_CHARS
        ):
            in_band.append(i)
    if not in_band:
        return None, scores
    chosen = max(in_band, key = lambda i: scores[i])
    return chosen, scores


def api_names(code: str) -> frozenset[str]:
    """Return imported and called API names, excluding names common to most examples."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return frozenset(
        name for name in names
        if len(name) >= 3 and not name.startswith("_") and name not in params.COMMON_API_NAMES
    )


def _truncate_code(code: str, chars: int) -> str:
    """Cut code at a line end while staying within the character limit."""
    if len(code) <= chars:
        return code
    return code[:chars].rsplit("\n", 1)[0]


def collect_steps(sections: list[dict], vault: dict) -> tuple[list[dict], list[frozenset[str]]]:
    """Build the chapter context shown to the capstone designer and its API-name sets."""
    steps: list[dict] = []
    names: list[frozenset[str]] = []
    spent = 0
    for section in sections:
        heading = str(section.get("heading") or "")
        if heading.startswith(params.CAPSTONE_PREFIX):
            continue
        examples = []
        for subtopic in section.get("subtopics") or []:
            code = subtopic.get("derived_code") or ""
            if not code and subtopic.get("code_ref_hash") in vault:
                entry = vault[subtopic["code_ref_hash"]]
                code = fence_inner(entry.fence_text or "")
            code = _truncate_code(code, params.CAPSTONE_BLOCK_CHARS)
            if spent + len(code) > params.CAPSTONE_CODE_CHARS:
                code = "\n".join(code.splitlines()[:6])
            spent += len(code)
            names.append(api_names(code))
            examples.append({
                "subheading": str(subtopic.get("subheading") or ""),
                "demonstrates": str(subtopic.get("explanation") or ""),
                "code": code,
            })
        steps.append({"heading": heading, "examples": examples})
    return steps, names


def use_share(steps_names: list[frozenset[str]], capstone_code: str) -> tuple[float, list[str]]:
    """Return the share of steps whose key API the capstone uses and names from omitted steps."""
    about = [names for names in steps_names if names]
    if not about:
        return 1.0, []
    used = api_names(capstone_code) | frozenset(patterns.IDENTIFIER_RE.findall(capstone_code))
    left_out = [names for names in about if not names & used]
    return (len(about) - len(left_out)) / len(about), sorted({sorted(names)[0] for names in left_out})


def capstone_issues(code: str, steps_names: list[frozenset[str]]) -> list[str]:
    """Check that a capstone parses, fits its size band, and uses most chapter step APIs."""
    body = parse_code_block(code or "") or (code or "").strip()
    if not body:
        return ["The code is empty."]
    if not python_ast_valid(body):
        return ["The code does not parse as valid Python (check imports, brackets, indentation)."]
    issues = []
    lines = sum(1 for line in body.splitlines() if line.strip())
    if not params.CAPSTONE_MIN_LINES <= lines <= params.CAPSTONE_MAX_LINES:
        issues.append(f"The application has {lines} non-blank lines; it must have {params.CAPSTONE_MIN_LINES} to {params.CAPSTONE_MAX_LINES}.")
    share, left_out = use_share(steps_names, body)
    if share < params.CAPSTONE_MIN_USE_SHARE:
        issues.append(
            f"The application uses the code of only {share:.0%} of the chapter's steps (at least {params.CAPSTONE_MIN_USE_SHARE:.0%}). "
            f"It leaves out: {', '.join(left_out[:6])}. Combine those into the program."
        )
    return issues


def _trim_words(text: str, limit: int) -> str:
    return " ".join((text or "").split()[:limit])


def build_section(answer: dict, sections: list[dict]) -> dict | None:
    """Build the chapter's final section from the capstone designer's response."""
    subtopics = []
    for part in (answer.get("parts") or [])[:params.CAPSTONE_MAX_PARTS]:
        code = parse_code_block(part.get("code") or "") or (part.get("code") or "").strip()
        if not code:
            continue
        explanation = _trim_words(part.get("explanation") or "", 80)
        if len(explanation.split()) < 8:
            explanation = f"{explanation} {params.EMPTY_CAPSTONE_PART}".strip()
        subtopics.append({
            "code_ref_hash": "",
            "subheading": _trim_words(part.get("subheading") or "", 10) or "Build the application",
            "explanation": explanation,
            "code_source": "derived",
            "derived_code": code,
        })
    if not subtopics:
        return None
    title = _trim_words(patterns.CAPSTONE_TITLE_PREFIX_RE.sub("", answer.get("title") or ""), 7)
    intro = " ".join((answer.get("intro") or "").split())[:params.INTRO_MAX_CHARS]
    seen: set[str] = set()
    citations = []
    for section in sections:
        for citation in section.get("citations") or []:
            key = citation.get("source_key")
            if key and key not in seen and len(citations) < params.CAPSTONE_CITATIONS:
                seen.add(key)
                citations.append(dict(citation))
    taken = {str(section.get("section_id")) for section in sections}
    number = len(sections) + 1
    while f"s{number}" in taken:
        number += 1
    return {
        "section_id": f"s{number}",
        "heading": f"{params.CAPSTONE_PREFIX}: {title}" if title else params.CAPSTONE_PREFIX,
        "intro": intro if len(intro) >= params.INTRO_MIN_CHARS else params.EMPTY_CAPSTONE_INTRO,
        "subtopics": subtopics,
        "citations": citations,
    }
