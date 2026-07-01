"""CoCoA two-stage alignment check (arXiv 2410.03131) — overrides c11+c12 on drift."""
from __future__ import annotations
from .keys import digest_latest_key, latest_blob_key, sawc_latest_key, versioned_blob_key
from .params import (
    DENSITY_MAX_AVG_EXPLANATION_WORDS,
    DENSITY_MAX_CHARS_PER_PARA,
    DENSITY_MIN_AVG_EXPLANATION_WORDS,
    DENSITY_MIN_CHARS_PER_PARA,
    FEEDBACK_MAX_CHARS,
    FEEDBACK_MIN_CHARS,
    LLM_CRITERIA,
    MAX_RENDERED_CHAPTER_CHARS,
    MIN_AVG_CODE_REFS_PER_SECTION,
    MIN_CITATIONS_PER_SECTION,
    MIN_CODE_REF_COVERAGE_FRACTION,
    PASS_THRESHOLD,
    PICKER_FALLBACK_RATE_MAX,
    REPAIR_RATE_MAX,
)
from .schemas import (
    ChecklistEvaluation,
    CriterionResult,
    LLMJudgePayload,
    LLMVerdict,
)
from .versions import CHECKLIST_PROMPT_VERSION, CHECKLIST_SCHEMA_VERSION

import json
import logging
import re

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage


logger = logging.getLogger(__name__)


COCOA_PROMPT_VERSION = "v1-cocoa-2026-05-25"

# Stage-1 cache keyed on vault hash + prompt_version; prompt revision auto-invalidates.
_COCOA_CACHE_PREFIX = f"synth-cache/cocoa-abstractions/{COCOA_PROMPT_VERSION}"

_DD_PROCESS_EXPLAINER = "dd-cocoa-explainer"
_DD_PROCESS_JUDGE     = "dd-cocoa-judge"

_MAX_SUBTOPICS_PER_BATCH = 60

_EXPLAINER_MAX_TOKENS = 8000
_JUDGE_MAX_TOKENS     = 4000
_EXPLAINER_TEMPERATURE = 0.0
_JUDGE_TEMPERATURE     = 0.0

# reverted 0.70 → 0.85 (CC run: 0.70 let through catastrophic mismatches; keyword-overlap pre-check now covers the BU regression).
_ALIGN_PASS_FRACTION = 0.85

# Keyword-overlap pre-check: zero shared identifiers between prose and code → misaligned (no LLM call needed).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Common words that don't carry alignment signal even if they appear in
# code (control-flow keywords, generic verbs). Lowercase. Frozenset for
# O(1) membership.
_NOISE_IDENTS = frozenset({
    "for", "and", "the", "with", "from", "import", "return", "true",
    "false", "none", "null", "this", "self", "type", "string", "int",
    "bool", "list", "dict", "set", "tuple", "any", "all", "function",
    "async", "await", "class", "def", "let", "var", "const", "new",
    "try", "except", "finally", "throw", "throws", "while", "case",
    "switch", "break", "continue", "yield", "lambda", "print", "log",
    "console", "data", "value", "result", "options", "params", "args",
    "main", "init", "name", "key", "id", "config", "test", "tests",
    "example", "examples", "default", "true", "false",
})
_MIN_IDENT_LEN = 3

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


def _extract_identifiers(text: str) -> set[str]:
    """Lowercased identifiers ≥_MIN_IDENT_LEN chars, noise-filtered."""
    if not text:
        return set()
    out: set[str] = set()
    for m in _IDENT_RE.finditer(text):
        tok = m.group(0).lower()
        if len(tok) < _MIN_IDENT_LEN:
            continue
        if tok in _NOISE_IDENTS:
            continue
        out.add(tok)
    return out


def _has_keyword_overlap(*, code_body: str, explanation: str) -> bool:
    """True iff explanation shares ≥1 informative identifier with code_body."""
    code_idents = _extract_identifiers(code_body)
    if not code_idents:
        return True   # nothing to anchor against → don't false-fail
    expl_idents = _extract_identifiers(explanation)
    return bool(code_idents & expl_idents)


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
            f"[id = {bid}, lang = {lang}]\n```{lang}\n{body}\n```"
        )
    return "\n\n".join(parts)


async def _read_cached_abstraction(minio, h: str) -> str | None:
    """Return cached spec for hash `h`, or None on miss/error."""
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
            content_type = "application/json",
        )
    except Exception as e:
        logger.debug(
            f"[cocoa] cache write failed for {h[:8]}…: "
            f"{type(e).__name__}: {e}"
        )


async def _explain_blocks(blocks: list[dict]) -> dict[str, str]:
    """Run the explainer on a batch of code blocks; derived blocks skip cache (body varies per run)."""
    if not blocks:
        return {}

    minio = get_storage()

    # derived blocks (code_source='derived') always go to the LLM; verbatim blocks cache by hash.
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
        blocks_block = _render_blocks_for_explainer(misses),
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens = _EXPLAINER_MAX_TOKENS,
            temperature = _EXPLAINER_TEMPERATURE,
            dd_process = _DD_PROCESS_EXPLAINER,
            response_format = {"type": "json_object"},
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

    # Derived blocks are unsafe to cache by hash (body varies).
    miss_by_id = {b["id"]: b for b in misses}
    for rid, spec in fresh.items():
        b = miss_by_id.get(rid)
        if not b:
            continue
        h = (b.get("hash") or "").strip()
        is_derived = (b.get("code_source") or "verbatim") == "derived"
        if h and not is_derived:
            await _write_cached_abstraction(minio, h, spec)

    return {**cached, **fresh}


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
      "reason": "<short string; required when aligned = false, optional otherwise>"}},
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
            f"[id = {pid}]\n"
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
        pairs_block = _render_pairs_for_judge(pairs),
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens = _JUDGE_MAX_TOKENS,
            temperature = _JUDGE_TEMPERATURE,
            dd_process = _DD_PROCESS_JUDGE,
            response_format = {"type": "json_object"},
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


async def cocoa_alignment_check(
    *,
    sawc_payload: dict,
    vault: dict[str, str],
) -> dict:
    """Run CoCoA two-stage alignment over every (subtopic, code) pair; fail-soft → passes."""
    sections = sawc_payload.get("sections") or []

    # Stable integer id so JSON round-trips are robust.
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

    # Zero-identifier overlap → auto-flagged misaligned (CC ch-01: 6 cases caught without LLM calls).
    structural_misaligned: list[dict] = []
    pairs_for_llm: list[dict] = []
    for p in pairs:
        if _has_keyword_overlap(
            code_body = p.get("body", ""),
            explanation = p.get("explanation", ""),
        ):
            pairs_for_llm.append(p)
        else:
            structural_misaligned.append({
                "section_id": p.get("section_id"),
                "subheading": p["subheading"],
                "reason": (
                    "explanation shares zero informative identifiers "
                    "with the cited code body (structural pre-check); "
                    "prose is talking about a different API"
                ),
            })
    if structural_misaligned:
        logger.info(
            f"[cocoa] keyword-overlap pre-check flagged "
            f"{len(structural_misaligned)}/{n_pairs} pairs as structurally "
            f"misaligned; LLM judge will only see "
            f"{len(pairs_for_llm)} pairs"
        )
    pairs = pairs_for_llm

    # Slice by _MAX_SUBTOPICS_PER_BATCH so prompts don't balloon.
    batches: list[list[dict]] = [
        pairs[i:i + _MAX_SUBTOPICS_PER_BATCH]
        for i in range(0, n_pairs, _MAX_SUBTOPICS_PER_BATCH)
    ]

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

    # Judge across all pairs that received an abstraction.
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
    misaligned: list[dict] = list(structural_misaligned)   # U5 merge
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

    # n_pairs is the ORIGINAL total (includes structurally-misaligned); structural pre-check is part of the alignment signal.
    n_judged = n_pairs
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


def _strip_fences(s: str) -> str:
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


def _detect_lang(s: str) -> str:
    """Pull the lang from a ```python\n...``` fence; default 'python'."""
    if not s:
        return "python"
    head = s.lstrip().split("\n", 1)[0]
    if head.startswith("```"):
        return head[3:].strip() or "python"
    return "python"
