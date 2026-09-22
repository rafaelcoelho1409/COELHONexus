"""checklist_eval — pure helpers: deterministic pre-gate checks, aggregation,
chapter/digest rendering for the judge, prompt builders, LLM verdict coercion,
manifest hashing, and the CoCoA/atomic-claim-grounding shared helpers (JSON
extraction, identifier overlap, prompt-block rendering, fence stripping,
cache-key hashing)."""
from __future__ import annotations
from . import params, patterns, prompts, schemas, versions

import ast
import json
import random
import re
from collections import Counter
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError


def parse_json(text: str) -> dict | None:
    if not text:
        return None
    m = patterns.JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def extract_identifiers(text: str) -> set[str]:
    """Lowercased identifiers ≥params.MIN_IDENT_LEN chars, noise-filtered."""
    if not text:
        return set()
    out: set[str] = set()
    for m in patterns.IDENT_RE.finditer(text):
        tok = m.group(0).lower()
        if len(tok) < params.MIN_IDENT_LEN:
            continue
        if tok in params.NOISE_IDENTS:
            continue
        out.add(tok)
    return out


def has_keyword_overlap(*, code_body: str, explanation: str) -> bool:
    """True iff explanation shares ≥1 informative identifier with code_body."""
    code_idents = extract_identifiers(code_body)
    if not code_idents:
        return True   # nothing to anchor against → don't false-fail
    expl_idents = extract_identifiers(explanation)
    return bool(code_idents & expl_idents)


def render_blocks_for_explainer(blocks: list[dict]) -> str:
    parts: list[str] = []
    for b in blocks:
        bid = b["id"]
        lang = b.get("lang") or ""
        body = (b.get("body") or "")[:params.CODE_EXCERPT_CHARS]
        parts.append(
            f"[id = {bid}, lang = {lang}]\n```{lang}\n{body}\n```"
        )
    return "\n\n".join(parts)


def render_pairs_for_judge(pairs: list[dict]) -> str:
    parts: list[str] = []
    for p in pairs:
        pid = p["id"]
        spec = (p.get("spec") or "").strip()
        sub  = (p.get("subheading") or "").strip()
        expl = (p.get("explanation") or "").strip()
        parts.append(
            f"[id = {pid}]\n"
            f"  SUBHEADING:  {sub}\n"
            f"  EXPLANATION: {expl}\n"
            f"  SPEC:        {spec}"
        )
    return "\n\n".join(parts)


def strip_fences(s: str) -> str:
    """Strip ``` fence markers from a vault body so the explainer sees clean code."""
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


def detect_lang(s: str) -> str:
    """Pull the lang from a ```python\n...``` fence; default 'python'."""
    if not s:
        return "python"
    head = s.lstrip().split("\n", 1)[0]
    if head.startswith("```"):
        return head[3:].strip() or "python"
    return "python"


def prose_cache_key(prose: str) -> str:
    """16-hex sha256 of truncated prose (same truncation as LLM input, so key is semantically accurate)."""
    return sha256(prose.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deterministic pre-gate checks (7 of the 12 criteria).
# ---------------------------------------------------------------------------

def check_all_sections_present(sawc: dict) -> schemas.CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_done = int(cs.get("n_sections_completed", 0))
    n_total = int(cs.get("n_sections", 0))
    passed = (n_total > 0) and (n_done == n_total)
    return schemas.CriterionResult(
        name = "all_sections_present",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"only {n_done}/{n_total} sections completed (sawc reported "
                 f"some sections failed to write). mgsr_replan should retry "
                 f"the missing sections."
        ),
    )


def check_no_placeholder_sections(sawc: dict) -> schemas.CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_fb = int(cs.get("n_sections_fallback", 0))
    passed = n_fb == 0
    return schemas.CriterionResult(
        name = "no_placeholder_sections",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"{n_fb} section(s) are placeholders (all 3 writer drafts "
                 f"failed). mgsr_replan should target these specifically "
                 f"with a fresh outline + retry."
        ),
    )


def check_unique_headings(sawc: dict) -> schemas.CriterionResult:
    sections = sawc.get("sections") or []
    headings = [(s.get("heading") or "").strip().casefold() for s in sections]
    n_total = len(headings)
    n_unique = len(set(headings))
    passed = n_total == n_unique
    if passed:
        feedback = ""
    else:
        seen: set[str] = set()
        dupes: list[str] = []
        for h in headings:
            if h in seen and h not in dupes:
                dupes.append(h)
            seen.add(h)
        feedback = (
            f"duplicate section headings (case-insensitive): "
            f"{sorted(set(dupes))[:3]}. mgsr_replan should rename or merge."
        )
    return schemas.CriterionResult(
        name = "unique_headings",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_all_sections_cite_at_least_1(sawc: dict) -> schemas.CriterionResult:
    sections = sawc.get("sections") or []
    thin: list[str] = []
    for s in sections:
        n_cites = len(s.get("citations") or [])
        if n_cites < params.MIN_CITATIONS_PER_SECTION:
            thin.append(s.get("section_id", "?"))
    passed = not thin
    return schemas.CriterionResult(
        name = "all_sections_cite_at_least_1",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"sections with <{params.MIN_CITATIONS_PER_SECTION} citation(s): "
                 f"{thin}. add a citation grounding each section's primary "
                 f"claim."
        ),
    )


def check_density_within_bounds(sawc: dict) -> schemas.CriterionResult:
    """Chapter-wide average explanation words must land in [DENSITY_MIN, DENSITY_MAX]."""
    cs = sawc.get("coverage_stats") or {}
    avg = float(cs.get("avg_explanation_words", 0))
    floor = params.DENSITY_MIN_AVG_EXPLANATION_WORDS
    ceil = params.DENSITY_MAX_AVG_EXPLANATION_WORDS
    passed = floor <= avg <= ceil
    if passed:
        feedback = ""
    elif avg < floor:
        feedback = (
            f"explanations are too thin ({avg:.0f} avg words; floor "
            f"{floor:.0f}). expand the 1-2 sentence lead-in BEFORE each "
            f"code block with concrete API/parameter detail."
        )
    else:
        feedback = (
            f"explanations are too verbose ({avg:.0f} avg words; ceiling "
            f"{ceil:.0f}). compress to 1-2 sentences — the code is the "
            f"point, the prose just sets it up."
        )
    return schemas.CriterionResult(
        name = "density_within_bounds",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_repair_rate_low(sawc: dict) -> schemas.CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_repairs = int(cs.get("n_repairs", 0))
    n_drafts = int(cs.get("n_total_drafts_fired", 0))
    rate = (n_repairs / n_drafts) if n_drafts else 0.0
    passed = rate < params.REPAIR_RATE_MAX
    return schemas.CriterionResult(
        name = "repair_rate_low",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"high writer-repair rate ({n_repairs}/{n_drafts} = "
                 f"{rate:.0%}; ceiling {params.REPAIR_RATE_MAX:.0%}). The writer "
                 f"struggled with Pydantic+cross-ref compliance — consider "
                 f"a clearer outline or tighter contributions."
        ),
    )


def check_picker_fallback_rate_low(sawc: dict) -> schemas.CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_fb = int(cs.get("n_picker_fallbacks", 0))
    n_picks = int(cs.get("n_critic_picks", 0))
    rate = (n_fb / n_picks) if n_picks else 0.0
    passed = rate < params.PICKER_FALLBACK_RATE_MAX
    return schemas.CriterionResult(
        name = "picker_fallback_rate_low",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"high critic-picker fallback rate ({n_fb}/{n_picks} = "
                 f"{rate:.0%}; ceiling {params.PICKER_FALLBACK_RATE_MAX:.0%}). "
                 f"the critic LLM frequently returned malformed JSON; "
                 f"the structural-score fallback handled it, but quality "
                 f"signal is degraded."
        ),
    )


def check_code_density_appropriate(sawc: dict) -> schemas.CriterionResult:
    """Avg code subtopics/section ≥ floor AND ≥ MIN_CODE_REF_COVERAGE_FRACTION of hashes used."""
    sections = sawc.get("sections") or []
    if not sections:
        return schemas.CriterionResult(
            name = "code_density_appropriate",
            passed = False,
            kind = "deterministic",
            feedback = "no sections — chapter is empty",
        )

    n_refs_per_section: list[tuple[str, int]] = []
    thin_coverage: list[str] = []
    n_total_refs = 0
    for s in sections:
        sid = s.get("section_id", "?")
        subtopics = s.get("subtopics") or []
        n_refs = sum(1 for st in subtopics if (st or {}).get("code_ref_hash"))
        n_total_refs += n_refs
        n_refs_per_section.append((sid, n_refs))
        n_allowed = int(s.get("n_allowed_hashes") or 0)
        if n_allowed >= 3:
            coverage = n_refs / max(1, n_allowed)
            if coverage < params.MIN_CODE_REF_COVERAGE_FRACTION:
                thin_coverage.append(f"{sid}({n_refs}/{n_allowed})")
    avg = n_total_refs / len(sections)
    passed = (
        avg >= params.MIN_AVG_CODE_REFS_PER_SECTION
        and len(thin_coverage) <= len(sections) // 2   # tolerate 50% thin
    )
    if passed:
        feedback = ""
    else:
        zeros = [sid for sid, n in n_refs_per_section if n == 0]
        feedback = (
            f"code density too low: avg {avg:.2f} subtopics/section "
            f"(floor {params.MIN_AVG_CODE_REFS_PER_SECTION}); "
            f"{len(zeros)} sections with 0 code subtopics"
        )
        if zeros[:5]:
            feedback += f": {zeros[:5]}"
        if thin_coverage[:5]:
            feedback += (
                f"; {len(thin_coverage)} sections under-using code bank: "
                f"{thin_coverage[:5]}"
            )
        feedback += (
            ". This is a CODE-FIRST learning resource — every section "
            "must emit ≥3 (subheading, explanation, code block) subtopics."
        )
    return schemas.CriterionResult(
        name = "code_density_appropriate",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_code_uniqueness_ratio(sawc: dict) -> schemas.CriterionResult:
    """Adaptive uniqueness floor (0.50/0.35/0.30 by bank size); excludes derived subtopics."""
    sections = sawc.get("sections") or []
    if not sections:
        return schemas.CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "no sections — vacuously true",
        )

    hashes: list[str] = []
    for s in sections:
        for st in (s.get("subtopics") or []):
            if not isinstance(st, dict):
                continue
            if (st.get("code_source") or "verbatim") == "derived":
                continue
            h = st.get("code_ref_hash")
            if h:
                hashes.append(h)

    if not hashes:
        return schemas.CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "no verbatim code blocks — vacuously true",
        )

    n_total = len(hashes)
    n_unique = len(set(hashes))
    ratio = n_unique / n_total

    if n_unique >= 30:
        adaptive_floor = 0.50
    elif n_unique >= 15:
        adaptive_floor = 0.35
    else:
        adaptive_floor = 0.30
    passed = ratio >= adaptive_floor

    if passed:
        return schemas.CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "",
        )

    top = Counter(hashes).most_common(3)
    sample = ", ".join(f"{h[:8]}…×{n}" for h, n in top if n > 1)
    feedback = (
        f"code uniqueness {ratio:.0%} ({n_unique} unique / {n_total} "
        f"total verbatim blocks); adaptive floor {adaptive_floor:.0%} "
        f"(scaled to bank diversity). Top duplicates: {sample}. "
        f"Sections are recycling the same vault snippets across "
        f"different subtopics — split overloaded sections or merge "
        f"sections that share most of their code base."
    )
    return schemas.CriterionResult(
        name = "code_uniqueness_ratio",
        passed = False,
        kind = "deterministic",
        feedback = feedback,
    )


# Python str/list/dict methods that are always synchronous. Chaining one
# directly onto the result of an inner call, then `await`-ing the whole
# expression, means the method actually ran on the un-awaited coroutine
# object, not on its resolved value — e.g. `await x.evaluate(...).strip()`
# calls .strip() on the coroutine `x.evaluate(...)`, not on the string it
# will eventually produce. Caught in the wild in a sawc_derive-generated
# crawl4ai example (`domains.dd.synth.nodes.sawc_derive`); AST-parse alone
# (already done at sawc_derive generation time) doesn't catch it because
# the code is syntactically valid — it only fails at runtime.
def _find_unawaited_chain_bugs(tree: ast.AST) -> list[str]:
    """Detect `await <call>(...).sync_method(...)` — AST-only, no
    execution, so it stays inside domain.py's purity contract."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in params.SYNC_ONLY_METHODS:
            continue
        if not isinstance(func.value, ast.Call):
            continue
        hits.append(
            f"line {getattr(node, 'lineno', '?')}: `await ....{func.attr}(...)` "
            f"runs .{func.attr}() on the un-awaited coroutine, not its "
            f"result — likely needs `(await ...).{func.attr}(...)`"
        )
    return hits


def check_code_snippet_sanity(sawc: dict) -> schemas.CriterionResult:
    """AST-parse every sawc_derive `derived_code` block: reject syntax
    errors and scan for the un-awaited-chain bug class above. Only
    `derived_code` is checked — verbatim vault snippets are substituted
    from the vault at render time and aren't inline in `sawc`, so
    checking them here would require an I/O fetch that domain.py can't
    do."""
    sections = sawc.get("sections") or []
    problems: list[str] = []
    for s in sections:
        sid = s.get("section_id", "?")
        for st in (s.get("subtopics") or []):
            st = st or {}
            if st.get("code_source") != "derived":
                continue
            code = st.get("derived_code") or ""
            if not code.strip():
                continue
            label = f"{sid}/{st.get('subheading', '?')}"
            try:
                tree = ast.parse(code)
            except SyntaxError as e:
                problems.append(f"{label}: SyntaxError: {e.msg} (line {e.lineno})")
                continue
            for hit in _find_unawaited_chain_bugs(tree):
                problems.append(f"{label}: {hit}")

    passed = not problems
    return schemas.CriterionResult(
        name = "code_snippet_sanity",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else "derived code block(s) have runtime-breaking bugs: "
                 + "; ".join(problems[:3])
                 + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
                 + ". mgsr_replan should regenerate these subtopics."
        ),
    )


# Ordered list — stable iteration = stable pass-rate denominators.
DETERMINISTIC_CHECKS = (
    check_all_sections_present,
    check_no_placeholder_sections,
    check_unique_headings,
    check_all_sections_cite_at_least_1,
    check_density_within_bounds,
    check_repair_rate_low,
    check_picker_fallback_rate_low,
    check_code_density_appropriate,
    check_code_uniqueness_ratio,
    check_code_snippet_sanity,
)


def aggregate_pass_rate(
    results: list[schemas.CriterionResult],
) -> tuple[int, int, float, bool]:
    """Compute (n_passed, n_total, pass_rate, chapter_passed) from
    the full criterion list."""
    n_total = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = (n_passed / n_total) if n_total else 0.0
    chapter_passed = pass_rate >= params.PASS_THRESHOLD
    return n_passed, n_total, pass_rate, chapter_passed


def collect_failed_feedback(results: list[schemas.CriterionResult]) -> list[str]:
    """Extract failed criteria feedback as `[criterion_name] text` for mgsr_replan."""
    out: list[str] = []
    for r in results:
        if not r.passed and r.feedback:
            out.append(f"[{r.name}] {r.feedback}")
    return out


def water_fill_blocks(
    blocks: list[str], *, char_cap: int,
) -> tuple[str, bool]:
    """Join `blocks` within `char_cap` total chars, water-filling fairly
    across all of them instead of sequential-fill-then-stop. A naive cap
    silently drops every block after whichever one blows the budget —
    for a chapter render that means every section after some midpoint
    (chapter_reads_coherently / terminology_consistent judge the WHOLE
    chapter, so never seeing its ending biases both verdicts); for a
    digest render it means later sections' grounding facts vanish
    entirely from what claims_grounded_in_sources gets to check against.
    Same algorithm as outline_sdp's source concatenation / sawc_write's
    vault-bank fix — order preserved, no entry ever fully zeroed out."""
    n = len(blocks)
    if n == 0:
        return "", False
    alloc = [0] * n
    pending = list(range(n))
    remaining_budget = char_cap
    while pending and remaining_budget > 0:
        share = remaining_budget // len(pending)
        if share <= 0:
            break
        still_pending: list[int] = []
        for i in pending:
            need = len(blocks[i]) - alloc[i]
            take = min(need, share)
            alloc[i] += take
            remaining_budget -= take
            if alloc[i] < len(blocks[i]):
                still_pending.append(i)
        pending = still_pending
    truncated = any(alloc[i] < len(blocks[i]) for i in range(n))
    parts = [blocks[i][: alloc[i]] for i in range(n) if alloc[i] > 0]
    return "\n".join(parts), truncated


def render_chapter_for_judge(
    sawc: dict,
    *,
    char_cap: int = params.MAX_RENDERED_CHAPTER_CHARS,
) -> tuple[str, bool]:
    """Render v2 cookbook sections for the LLM-judge; returns (text, truncated_flag)."""
    sections = sawc.get("sections") or []
    blocks: list[str] = []
    for s in sections:
        sid = s.get("section_id", "?")
        heading = s.get("heading", "?")
        block_lines: list[str] = [f"## {sid}: {heading}"]
        intro = (s.get("intro") or "").strip()
        if intro:
            block_lines.append("")
            block_lines.append(intro)
        subtopics = s.get("subtopics") or []
        for st in subtopics:
            st = st or {}
            block_lines.append("")
            block_lines.append(f"### {st.get('subheading', '?')}")
            expl = (st.get("explanation") or "").strip()
            if expl:
                block_lines.append("")
                block_lines.append(expl)
            h = (st.get("code_ref_hash") or "")
            if h:
                block_lines.append("")
                block_lines.append(f"[code-block: {h[:12]}…]")
        # Compact metadata at section end
        citations = s.get("citations") or []
        if citations:
            cite_summary = "; ".join(
                f"{(c.get('source_key') or '').rsplit('/', 1)[-1]} ("
                f"'{(c.get('claim') or '')[:80]}')"
                for c in citations[:5]
            )
            block_lines.append("")
            block_lines.append(f"[citations ({len(citations)}): {cite_summary}]")
        block_lines.append("")
        blocks.append("\n".join(block_lines))
    return water_fill_blocks(blocks, char_cap = char_cap)


def render_digest_for_grounding(
    digest: dict,
    *,
    char_cap: int = 20_000,
) -> str:
    """Render compressed per-section digest contributions for the grounding judge."""
    per_section = digest.get("per_section") or {}
    blocks: list[str] = []
    for sid in sorted(per_section.keys()):
        contribs = per_section[sid]
        if not contribs:
            continue
        block_lines = [f"## {sid} grounding:"]
        for c in contribs[:4]:
            src = c.get("source_key", "?")
            src_short = src.rsplit("/", 1)[-1] if src else "?"
            relevance = c.get("relevance", "?")
            summ = c.get("summary", "")
            facts = c.get("key_facts") or []
            block_lines.append(
                f"  - {src_short} ({relevance}): {summ[:200]}"
            )
            for f in facts[:3]:
                block_lines.append(f"    • {f[:200]}")
        blocks.append("\n".join(block_lines))
    text, _truncated = water_fill_blocks(blocks, char_cap = char_cap)
    return text


def criterion_order_for(chapter_id: str) -> list[str]:
    """Deterministic per-chapter shuffle; same chapter_id → same order (cache-safe) + bias averages out."""
    seed_material = (
        f"{chapter_id}|{versions.CHECKLIST_PROMPT_VERSION}".encode("utf-8")
    )
    seed = int.from_bytes(sha256(seed_material).digest()[:8], "big")
    rng = random.Random(seed)
    order = list(params.LLM_CRITERIA)
    rng.shuffle(order)
    return order


def build_judge_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> str:
    """Build the batched LLM-judge prompt with per-chapter criterion shuffle (position-bias mitigation)."""
    trunc_note = (
        "\n\nNOTE: The chapter text was truncated to fit the prompt — "
        "do NOT penalize 'incomplete chapter' or 'missing sections' if "
        "the visible content reads coherently up to the truncation point."
        if truncated else ""
    )
    order = criterion_order_for(chapter_id)
    criteria_block = "\n\n".join(prompts.CRITERION_BLOCKS[name] for name in order)
    output_lines = ",\n".join(
        f'  {name!r:<40}: {{"passed": ..., "feedback": "..."}}'
        for name in order
    )
    return (
        f"You are the Checklist Evaluator for chapter {chapter_id} "
        f"({chapter_title!r}) of framework {framework}. Apply 5 BINARY "
        f"criteria below. Each: PASS (true) or FAIL (false). If false, "
        f"give a 1-sentence specific feedback so mgsr_replan can act "
        f"surgically (which section + what's wrong). Be strict — don't "
        f"grade-inflate; pass only what you'd defend to a peer reviewer.\n\n"

        f"== CHAPTER (sections rendered top-to-bottom) =={trunc_note}\n"
        f"{rendered_chapter}\n"
        f"== END CHAPTER ==\n\n"

        f"== PER-SECTION GROUNDING (digest summaries — what each section "
        f"SHOULD cover, sourced from the digest_construct step) ==\n"
        f"{rendered_digest}\n"
        f"== END GROUNDING ==\n\n"

        f"== CRITERIA — answer each with PASS or FAIL + 1-sentence "
        f"specific feedback if FAIL ==\n\n"
        f"{criteria_block}\n\n"

        f"OUTPUT — strict JSON, exactly these 5 keys (each value: "
        f'{{"passed": bool, "feedback": "1-sentence specific reason if '
        f'false; empty string if true"}}):\n'
        f"{{\n{output_lines}\n}}\n\n"

        f"Respond ONLY with valid JSON. NO prose commentary, NO markdown "
        f"wrapping. Feedback should name a specific section + symptom "
        f"(e.g., 's4 opens with \"In this chapter we will explore...\"' "
        f"or 's7 cites 0024-isbn.md but its claim isn't in the key_facts')."
    )


def build_repair_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
    current_json: str,
    issues: list[str],
) -> str:
    """Repair prompt when the judge's first response was Pydantic-
    invalid (missing keys / wrong shape)."""
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix the JSON output. Keep the same 5-key shape; only correct "
        f"the structural issues below.\n\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"FRAMEWORK: {framework}\n\n"
        f"CURRENT (broken) JSON:\n{current_json}\n\n"
        f"ISSUES TO FIX:\n{issues_block}\n\n"
        f"Required keys (each value = "
        f'{{"passed": bool, "feedback": str}}):\n'
        f"  - chapter_reads_coherently\n"
        f"  - claims_grounded_in_sources\n"
        f"  - terminology_consistent\n"
        f"  - prose_code_first_not_meta_framing\n"
        f"  - code_refs_introduced_in_prose\n\n"
        f"Respond ONLY with valid JSON, no commentary."
    )


def llm_payload_to_criteria(
    payload: schemas.LLMJudgePayload,
) -> list[schemas.CriterionResult]:
    """Map LLM judge payload → 5 CriterionResult entries in LLM_CRITERIA order."""
    name_to_verdict = {
        "chapter_reads_coherently":          payload.chapter_reads_coherently,
        "claims_grounded_in_sources":        payload.claims_grounded_in_sources,
        "terminology_consistent":            payload.terminology_consistent,
        "prose_code_first_not_meta_framing": payload.prose_code_first_not_meta_framing,
        "code_refs_introduced_in_prose":     payload.code_refs_introduced_in_prose,
    }
    return [
        schemas.CriterionResult(
            name = name,
            passed = name_to_verdict[name].passed,
            kind = "llm_judge",
            feedback = (
                name_to_verdict[name].feedback
                if not name_to_verdict[name].passed
                else ""
            ),
        )
        for name in params.LLM_CRITERIA
    ]


def compute_manifest_hash(
    *,
    sawc_manifest_hash: str,
    digest_manifest_hash: str,
) -> str:
    payload = (
        f"sawc={sawc_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={versions.CHECKLIST_PROMPT_VERSION}|"
        f"schema={versions.CHECKLIST_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same classifier idiom used across the
    synth pipeline. The Rotator is a universal gateway with no context
    -length-aware arm filtering, so the rendered chapter (up to
    MAX_RENDERED_CHAPTER_CHARS) can still exceed a small-context arm."""
    msg = str(e).lower()
    return any(marker in msg for marker in params.CONTEXT_OVERFLOW_MARKERS)


def parse_json_response(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = patterns.JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def try_parse_judge(
    raw: dict,
) -> tuple[Optional[schemas.LLMJudgePayload], Optional[str]]:
    try:
        return schemas.LLMJudgePayload.model_validate(raw), None
    except ValidationError as e:
        return None, shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def fallback_llm_verdicts(reason: str) -> list[schemas.CriterionResult]:
    """Conservatively fail all 5 LLM criteria when judge is unavailable (triggers mgsr_replan)."""
    out: list[schemas.CriterionResult] = []
    for name in params.LLM_CRITERIA:
        out.append(schemas.CriterionResult(
            name=name,
            passed=False,
            kind="llm_judge",
            feedback=(
                f"judge_unavailable: {reason}. Conservatively marked "
                f"FAIL so mgsr_replan re-evaluates next iteration."
            ),
        ))
    return out


def shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:6]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 6} more)" if len(errs) > 6 else ""
    return "; ".join(lines) + suffix


def load_checklist_payload(text: str) -> dict:
    """Parse the persisted checklist blob."""
    return json.loads(text)
