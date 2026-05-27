"""sawc_write — Structure-Aware Writing Controller (SurveyGen-I + MAMM).

Step 6 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the sawc_write deep
research report). The third LLM-driven synth graph node, runs after
digest_construct commits its checkpoint.

WHAT IT DOES (per chapter):

  1. Loads outline-latest.json (from outline_sdp) + digest-latest.json
     (from digest_construct). The outline carries the DAG stages; the
     digest carries the per_section index telling the writer what each
     section should cover.
  2. Iterates DAG stages SEQUENTIALLY (stage 0 → stage 1 → ...). Within
     each stage, sections write CONCURRENTLY (bounded by `_CONCURRENCY`).
     This is the SurveyGen-I §3.2 stage-parallel algorithm.
  3. For EACH section, runs MAMM-Refine multi-agent best-of-N:
       - Fire N=3 writer drafts in parallel (3 distinct rotator picks)
       - 1 critic-picker call from a DIFFERENT model family
       - Picker chooses by structural rubric; falls back to deterministic
         structural scoring (Self-Certainty proxy) if critic LLM fails
  4. After each stage completes, derives MemoryEntry per section
     DETERMINISTICALLY (no extra LLM call — pulls terminology from
     digest contributions). The accumulated memory ledger is passed
     to the NEXT stage's sections so they have cross-section context.
  5. Persists ChapterDraft to MinIO (versioned + latest pointer).
  6. Returns state patch with `sawc_path` + `sawc_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/sawc/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/sawc-latest.json

  Manifest hash includes:
    outline_manifest_hash
    digest_manifest_hash
    prompt_version
    schema_version

  Cache hit returns immediately + emits `done` SSE with cache_hit=true.

FAIL-SOFT BEHAVIOR (matches outline_sdp / digest_construct patterns):

  - One draft's LLM call fails: log + emit section_draft_done(ok=false),
    keep going with the remaining drafts. Picker chooses from the
    successful ones.
  - All 3 drafts fail: emit a placeholder Section + flag in `issues`.
    mgsr_replan will re-target this section for retry.
  - Critic LLM returns malformed JSON / wrong index: fall back to
    structural scoring (Self-Certainty proxy) over the same candidates.
  - Pydantic validation fails on a draft: run repair LLM call with the
    validation errors as feedback. Max 2 repair attempts.

SSE EVENTS — real-time UI mechanism (per the established pattern):

  start              chapter_id, chapter_title, n_stages, n_sections,
                      n_total_drafts (= 3 × n_sections)
  stage_start        stage_idx, n_sections_in_stage, section_ids
  section_draft_done section_id, draft_idx, n_total (3), ok, wall_ms,
                      deployment, error?, n_paragraphs?
  section_picked     section_id, chosen_idx, n_violations, fallback?,
                      structural_score, deployment_critic
  section_done       section_id, n_paragraphs, n_code_refs, n_citations,
                      total_chars, n_repairs, wall_ms
  stage_done         stage_idx, n_completed, n_failed, wall_ms
  done               n_sections, n_completed, n_fallback, n_repairs,
                      total_drafts_fired, wall_ms, cache_hit
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async
from domains.llm.rotator.chain.service import _is_heavyweight as _sawc_writer_filter

from ..observability.spans import traced
from ..progress import emit_progress
from ..render.service import (
    source_key_to_vault_key as _source_key_to_vault_key,
)
from ..vault.service import (
    format_entries_for_prompt as _format_entries_for_prompt,
    rank_hashes_by_pedagogy as _rank_hashes_by_pedagogy,
)
from ..vault.types import VaultEntry
from .constants import (
    SAWC_PROMPT_VERSION,
    SAWC_SCHEMA_VERSION,
    _N_DRAFTS,
)
from .service import (
    build_critic_picker_prompt,
    build_repair_prompt,
    build_writer_prompt,
    compute_sawc_stats,
    extract_memory_entry,
    hard_issues,
    score_draft_structural,
    summarize_candidate,
    validate_section_against_inputs,
)
from .types import (
    ChapterDraft,
    Citation,
    MemoryEntry,
    SAWCStats,
    Section,
    Subtopic,
    _LLMSectionDraft,
)
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables (quality > speed per project memory feedback_kd_quality_over_speed)
# =============================================================================
# 2026-05-26 (DD-SYNTH-SPEED-SOTA): removed stale local `_N_DRAFTS = 3`
# override that was shadowing constants.py's `_N_DRAFTS = 2` (task #142,
# MAMM-Refine N=3→N=2). The single source of truth is now constants.py.
_CONCURRENCY           = 6       # max concurrent SECTIONS per stage
_TEMPERATURE_DRAFT     = 0.5     # variety across drafts (MAMM diversity)
_TEMPERATURE_CRITIC    = 0.0
_TEMPERATURE_REPAIR    = 0.2
_MAX_TOKENS_DRAFT      = 8000
_MAX_TOKENS_CRITIC     = 300
_MAX_TOKENS_REPAIR     = 8000
_MAX_REPAIR_ATTEMPTS   = 2

# DD-SYNTH-SPEED-SOTA #4 (2026-05-26) — Optimal-Stopping BoN sequential
# decision rule (arXiv 2510.01394, Oct 2025). Fire draft 1, ship it if
# "good enough" (zero violations + >=K subtopics + >=K citations), else
# fire remaining N-1 drafts and run pairwise tournament. Default true;
# env `KD_SAWC_OPTIMAL_STOPPING=false` reverts to fixed-N parallel.
_OPTIMAL_STOPPING_MIN_SUBTOPICS = 4
_OPTIMAL_STOPPING_MIN_CITATIONS = 2
_OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_SAWC_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

# R1 (2026-05-26 late evening) — reverted CORR-2 (json_object → json_schema).
#
# Empirical: Run 3 (post-CORR-2) made repair rates WORSE not better:
#   BU ch-01: 37.5% → 50%
#   BU ch-02: 57%   → 58%
#   CC ch-01: (new) → 63%
#   CC ch-02: (new) → 58%
# Diagnosis: json_object lets the model emit valid-JSON-but-loose
# structure that Pydantic field validators reject at a higher rate
# (subheading word counts, citation min, explanation length bounds).
# json_schema mode constrains the model's output shape server-side
# closer to what Pydantic expects, even with strict=False. The
# original Wave A1 read was correct; CORR-2 was the wrong call.
_SAWC_DRAFT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "section_draft",
        "schema": _LLMSectionDraft.model_json_schema(),
        "strict": False,
    },
}

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc/{manifest_hash}.json"


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def _outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


def _digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"


# =============================================================================
# Visible Vault loader (2026-05-24, Ship #1)
# =============================================================================
# Loads per-source vault manifests and returns RICH VaultEntry objects
# (hash + body + lang + line_count) — unlike render's loader which flattens
# to hash → fence_text only. SAWC needs the rich entries to render visible
# code envelopes in the writer prompt so the LLM can pick the pedagogically
# valuable hashes from informed context.
#
# This loader is intentionally separate from render's so that:
# (a) render keeps its lean hash → fence_text contract for substitution
# (b) sawc gets full metadata for prompt rendering
# (c) neither pollutes vault/service.py (which is pure functions, no I/O)
async def _load_chapter_vault_rich(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, VaultEntry], int, int]:
    """Returns (vault, n_loaded, n_skipped). Each value in `vault` is a
    VaultEntry — not just the fence text — so writer prompts can render
    full visible envelopes with lang + line_count metadata.

    Resolution per source (mirrors digest's read-time fallback so both
    nodes have identical vault visibility):
      1. Pre-built per-source vault file at `synth-vault/{slug}/pages/...`
      2. Runtime sentinelization of the raw ingestion page (preferred
         fallback when the consolidated llms-full crawl built only one
         mega-vault and individual per-page vaults are missing)
    """
    from ..vault.service import sentinelize_doc as _sentinelize_doc

    rich_vault: dict[str, VaultEntry] = {}
    n_loaded = 0
    n_skipped = 0
    for source_key in source_keys:
        # Try the pre-built per-source vault first.
        vault_key = _source_key_to_vault_key(source_key, slug)
        used_runtime = False
        if await minio.exists(vault_key):
            try:
                text = await minio.read_text(vault_key)
                manifest = json.loads(text)
                entries = (manifest or {}).get("entries") or {}
                for h, entry_dict in entries.items():
                    if not isinstance(entry_dict, dict):
                        continue
                    try:
                        rich_vault[h] = VaultEntry(**entry_dict)
                    except Exception:
                        if entry_dict.get("fence_text"):
                            rich_vault[h] = VaultEntry(
                                hash=h,
                                fence_text=entry_dict.get("fence_text", ""),
                                info_string=entry_dict.get("info_string", ""),
                                lang=entry_dict.get("lang", ""),
                                line_count=int(entry_dict.get("line_count") or 0),
                                char_count=int(entry_dict.get("char_count") or 0),
                                sentinel_kind=entry_dict.get(
                                    "sentinel_kind", "fence_backtick",
                                ),
                            )
                n_loaded += 1
                continue
            except Exception as e:
                logger.warning(
                    f"[sawc_write] vault {vault_key!r} unreadable: "
                    f"{type(e).__name__}: {e} — falling back to runtime"
                )
                used_runtime = True
        else:
            used_runtime = True

        # Runtime fallback: read raw ingestion page + sentinelize on-the-fly.
        # This is the path the fastmcp/etc corpora use today because
        # ingestion only built one consolidated vault for llms-full.
        if used_runtime:
            try:
                raw = await minio.read_text(source_key)
                if not raw or "<code-ref hash=" in raw:
                    n_skipped += 1
                    continue
                _, entries = _sentinelize_doc(raw)
                if entries:
                    for h, e in entries.items():
                        if h not in rich_vault:
                            rich_vault[h] = e
                    n_loaded += 1
                else:
                    n_skipped += 1
            except Exception as e:
                n_skipped += 1
                logger.warning(
                    f"[sawc_write] runtime-sentinelize failed for "
                    f"{source_key!r}: {type(e).__name__}: {e}"
                )
    return rich_vault, n_loaded, n_skipped


# =============================================================================
# JSON helpers
# =============================================================================
def _parse_json_response(text: str) -> Optional[dict]:
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
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


def _try_parse_draft(
    raw: dict,
) -> tuple[Optional[_LLMSectionDraft], Optional[str]]:
    try:
        return _LLMSectionDraft.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


# =============================================================================
# Per-draft pipeline
# =============================================================================
async def _draft_one_section(
    *,
    draft_idx: int,
    n_total: int,
    thread_id: str,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> tuple[Optional[_LLMSectionDraft], Optional[str], int, int]:
    """One writer call → parse → Pydantic → cross-ref → repair.

    Returns (draft, deployment, wall_ms, n_repairs). draft is None
    on irrecoverable failure.

    Emits ONE `section_draft_done` event so the UI shows progress
    through the N=3 fan-out (real-time mechanism we established for
    outline_sdp + digest_construct)."""
    t0 = time.monotonic()
    allowed_hash_set = set(allowed_hashes)
    valid_source_set = set(valid_source_keys)

    prompt = build_writer_prompt(
        framework=framework,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        section_id=section_id,
        section_heading=section_heading,
        section_description=section_description,
        section_prerequisites=section_prerequisites,
        contributions=contributions,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        memory=memory,
        n_primary_contribs=n_primary_contribs,
        vault_rich=vault_rich,
    )

    deployment: Optional[str] = None
    try:
        # Option B (2026-05-24): writer drafts use the dd-synth-write
        # bandit pool restricted to heavyweight reasoning models.
        # Workhorse arms (mistral-small, magistral-small, devstral-medium
        # under medium budget) stay reserved for dd-grader filter tasks.
        # DD-SYNTH-SPEED-SOTA #1 (2026-05-26): response_format=json_schema
        # is attached server-side for NIM/Mistral arms — repair loop below
        # still handles Gemini and any provider slip-through.
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_DRAFT,
            temperature=_TEMPERATURE_DRAFT,
            dd_process="dd-synth-write",
            candidate_filter=_sawc_writer_filter,
            response_format=_SAWC_DRAFT_RESPONSE_FORMAT,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=wall_ms,
        )
        return None, None, wall_ms, 0

    parsed = _parse_json_response(response)
    if not parsed:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error="parse_failed", wall_ms=wall_ms,
            deployment=deployment,
        )
        return None, deployment, wall_ms, 0

    draft, err = _try_parse_draft(parsed)
    n_repairs = 0
    current = parsed

    # Pydantic-fail repair loop
    while draft is None and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        issues = [f"Pydantic schema rejected the previous output: {err}"]
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(current, indent=2),
            issues=issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp:
                current = rp
                draft, err = _try_parse_draft(rp)
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: repair "
                f"attempt {n_repairs} failed: {type(e).__name__}: {e}"
            )
            break

    if draft is None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"pydantic_fail: {err}",
            wall_ms=wall_ms, deployment=deployment,
        )
        return None, deployment, wall_ms, n_repairs

    # Cross-ref validation (heading/hashes/citations + Ship B/E alignment)
    issues = validate_section_against_inputs(
        draft,
        expected_heading=section_heading,
        allowed_hashes=allowed_hash_set,
        valid_source_keys=valid_source_set,
        vault_rich=vault_rich,
    )
    # S3 (2026-05-26 late evening) — repair only on HARD issues. Soft
    # quality-nudge issues (subheading/explanation↔code mismatch,
    # subtopic-shy-of-bank) still ship in .issues for downstream but
    # don't burn the repair budget — the LLM can't reliably close them.
    while hard_issues(issues) and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(draft.model_dump(), indent=2),
            issues=issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if not rp:
                break
            new_draft, new_err = _try_parse_draft(rp)
            if new_draft is None:
                break
            new_issues = validate_section_against_inputs(
                new_draft,
                expected_heading=section_heading,
                allowed_hashes=allowed_hash_set,
                valid_source_keys=valid_source_set,
                vault_rich=vault_rich,
            )
            # Accept ONLY if it strictly reduces violation count
            # S3 — accept only when HARD issues strictly decreased.
            if len(hard_issues(new_issues)) < len(hard_issues(issues)):
                draft = new_draft
                issues = new_issues
            else:
                break
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: cross-ref "
                f"repair attempt {n_repairs} failed: "
                f"{type(e).__name__}: {e}"
            )
            break

    wall_ms = int((time.monotonic() - t0) * 1000)
    await emit_progress(
        thread_id, "sawc_write", "section_draft_done",
        section_id=section_id, draft_idx=draft_idx, n_total=n_total,
        ok=True, wall_ms=wall_ms, deployment=deployment,
        n_subtopics=len(draft.subtopics),
        n_citations=len(draft.citations),
        n_violations=len(issues),
    )
    return draft, deployment, wall_ms, n_repairs


# =============================================================================
# Critic picker — PAIRWISE TOURNAMENT (2026-05-24, supersedes pointwise N-pick)
# =============================================================================
# Why pairwise: Landesberg et al. Mar 2026 (arXiv:2603.12520) — pointwise LLM
# scoring on similar-quality long-form drafts captures only 21% of selection
# signal; 67% of pairwise comparisons tie. Knockout tournament with cross-
# family critics (PoLL-style diversity via bandit-routed arms across separate
# calls) recovers ~61% of selection signal. For N=3 we do 2 matches.
# Falls back to deterministic structural-score on every parse failure inside
# each match, so the tournament can never abort.
#
# Trade: 2 critic LLM calls instead of 1 for N=3. Cost is trivial under
# `feedback_kd_quality_over_speed` (tokens are free).
# See docs/KD-SYNTH-SOTA-2026-05-24.md §3 #1.

_PAIRWISE_PICKER_PROMPT = """You are picking the BETTER of two technical-documentation
drafts for the same section. The section is part of a larger distilled book.

Choose by these criteria in order:
1. Checklist coverage (does the draft address every outline point named?)
2. Citation density (does it cite/reference the source documentation it claims?)
3. Structural completeness (no truncations, no orphan code-refs, no placeholder text)
4. Clarity and concision (well-organized, no rambling)

You MUST choose A or B. Ties are NOT allowed.

=== SECTION ===
heading: {section_heading}
expected primary source contributions: {n_primary_contribs}

=== DRAFT A — structural summary ===
{summary_a}

=== DRAFT B — structural summary ===
{summary_b}

Answer in JSON: {{"winner": "A" | "B", "reason": "one short sentence"}}"""


async def _pairwise_judge_match(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    summary_a: dict,
    summary_b: dict,
) -> tuple[str, Optional[str]]:
    """Run ONE pairwise match. Returns (winner_letter, deployment_critic).

    winner_letter ∈ {"A", "B"}. On any parse / call failure, returns the
    structural-score winner via deterministic tiebreak — the tournament
    never aborts.
    """
    # Compact JSON-stringified summary keeps the prompt token-light.
    def _fmt_summary(s: dict) -> str:
        return json.dumps(
            {
                "structural_score": s.get("structural_score"),
                "n_paragraphs":     s.get("n_paragraphs"),
                "total_chars":      s.get("total_chars"),
                "n_code_refs":      s.get("n_code_refs"),
                "n_citations":      s.get("n_citations"),
                "heading_matches":  s.get("heading_matches"),
                "n_unknown_hashes": s.get("n_unknown_hashes"),
                "n_unknown_keys":   s.get("n_unknown_keys"),
            },
            indent=2,
        )

    prompt = _PAIRWISE_PICKER_PROMPT.format(
        section_heading=section_heading,
        n_primary_contribs=n_primary_contribs,
        summary_a=_fmt_summary(summary_a),
        summary_b=_fmt_summary(summary_b),
    )

    try:
        # DD-SYNTH-SPEED-SOTA #A7 (2026-05-26): json_object forces the
        # pairwise critic to emit valid JSON {"winner": "A"|"B", "reason": ...}
        # without prose preamble, eliminating ~most parse-failed tiebreaks.
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_CRITIC,
            temperature=_TEMPERATURE_CRITIC,
            response_format={"type": "json_object"},
        )
        deployment_critic = (meta or {}).get("deployment")
        parsed = _parse_json_response(response)
        if parsed and "winner" in parsed:
            w = str(parsed["winner"]).strip().upper()[:1]
            if w in ("A", "B"):
                return w, deployment_critic
    except Exception as e:
        logger.warning(
            f"[sawc_write] {section_id}: pairwise match failed: "
            f"{type(e).__name__}: {e} — structural tiebreak"
        )

    # Structural tiebreak — never abort the tournament.
    s_a = summary_a.get("structural_score", 0.0)
    s_b = summary_b.get("structural_score", 0.0)
    return ("A" if s_a >= s_b else "B"), None


async def _critic_pick_best(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    candidates: list[_LLMSectionDraft],
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    thread_id: str,
    vault_rich: dict | None = None,
) -> tuple[int, Optional[str], Optional[str], float]:
    """Pairwise tournament picker. Returns
    (chosen_idx, deployment_critic, fallback_used, structural_score).

    fallback_used ∈ {None, "structural_score"} — None means at least one
    pairwise match got a clean LLM verdict; "structural_score" means every
    match fell back to deterministic tiebreak.

    For N=3: 2 matches (knockout). For N=2: 1 match. For N=1: trivial.
    """
    summaries = [
        summarize_candidate(
            c,
            expected_heading=expected_heading,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            n_primary_contribs=n_primary_contribs,
            vault_rich=vault_rich,
        )
        for c in candidates
    ]

    if len(candidates) <= 1:
        score = summaries[0]["structural_score"] if summaries else 0.0
        return 0, None, None, score

    # Knockout: indices represent positions in `candidates`. Each match
    # picks between two positions; the winner advances.
    n = len(candidates)
    advancing = list(range(n))
    deployment_critic: Optional[str] = None
    n_llm_picks = 0

    # Pairwise knockout — log_2(N) rounds, but for N=3 it's just 2 matches:
    # round 1: cand[0] vs cand[1]; round 2: winner vs cand[2].
    while len(advancing) > 1:
        next_round: list[int] = []
        # Pair the front: idx_a vs idx_b → winner. Carry odd survivor forward.
        i = 0
        while i + 1 < len(advancing):
            idx_a, idx_b = advancing[i], advancing[i + 1]
            winner_letter, dep = await _pairwise_judge_match(
                section_id=section_id,
                section_heading=section_heading,
                n_primary_contribs=n_primary_contribs,
                summary_a=summaries[idx_a],
                summary_b=summaries[idx_b],
            )
            if dep is not None:
                deployment_critic = dep
                n_llm_picks += 1
            next_round.append(idx_a if winner_letter == "A" else idx_b)
            i += 2
        if i < len(advancing):
            next_round.append(advancing[i])  # bye for odd survivor
        advancing = next_round

    winner_idx = advancing[0]
    fallback_used = None if n_llm_picks > 0 else "structural_score"
    return (
        winner_idx,
        deployment_critic,
        fallback_used,
        summaries[winner_idx]["structural_score"],
    )


# =============================================================================
# Placeholder section (when ALL drafts fail)
# =============================================================================
def _placeholder_section(
    *,
    section_id: str,
    heading: str,
    n_repairs: int,
    deployment_writer: Optional[str],
) -> Section:
    """Returned when every writer draft + every repair attempt fails.
    Keeps the chapter assemblable and surfaces the failure to
    mgsr_replan via `issues`.

    v2 cookbook schema: empty subtopics list signals "no code emitted";
    the checklist density gate flags this for the mgsr→sawc loop.
    """
    return Section(
        section_id=section_id,
        heading=heading,
        intro=(
            f"This section ({heading}) is awaiting content. The synth "
            f"writer was unable to produce a valid draft on its initial "
            f"pass; mgsr_replan should retarget this section or merge "
            f"it into an adjacent section in the next iteration."
        ),
        subtopics=[],
        citations=[],
        n_drafts_tried=_N_DRAFTS,
        n_repairs=n_repairs,
        deployment_writer=deployment_writer,
        issues=["placeholder"],
    )


# =============================================================================
# Per-section pipeline (best-of-N + critic pick)
# =============================================================================
async def _write_section_best_of_n(
    *,
    sem: asyncio.Semaphore,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    vault_rich: dict | None = None,
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    thread_id: str,
) -> Section:
    """Full per-section pipeline: N drafts → critic-pick → Section.

    DD-SYNTH-SPEED-SOTA #4 (2026-05-26) — Optimal-Stopping BoN: fire draft 1
    sequentially; if it passes the deterministic "good enough" gate (zero
    violations + >=N_min subtopics + >=N_min citations), ship it directly
    and skip the remaining N-1 drafts. Otherwise fall through to the
    original parallel fan-out + pairwise tournament. arXiv 2510.01394
    (Oct 2025): 15-35% sample reduction at equal Best-of-N quality.
    Disabled via `KD_SAWC_OPTIMAL_STOPPING=false`.
    """
    async with sem:
        t0 = time.monotonic()

        def _make_draft_coro(idx: int):
            return _draft_one_section(
                draft_idx=idx,
                n_total=_N_DRAFTS,
                thread_id=thread_id,
                framework=framework,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                section_id=section_id,
                section_heading=section_heading,
                section_description=section_description,
                section_prerequisites=section_prerequisites,
                contributions=contributions,
                allowed_hashes=allowed_hashes,
                valid_source_keys=valid_source_keys,
                memory=memory,
                n_primary_contribs=n_primary_contribs,
                vault_rich=vault_rich,
            )

        if _OPTIMAL_STOPPING_ENABLED and _N_DRAFTS >= 2:
            # Fire draft 1 first, decide whether to fire the rest
            r0 = await _make_draft_coro(0)
            results = [r0]
            draft1, _dep1, _wall1, _repairs1 = r0
            good_enough = False
            if draft1 is not None:
                issues_1 = validate_section_against_inputs(
                    draft1,
                    expected_heading=section_heading,
                    allowed_hashes=set(allowed_hashes),
                    valid_source_keys=set(valid_source_keys),
                    vault_rich=vault_rich,
                )
                if (
                    len(issues_1) == 0
                    and len(draft1.subtopics) >= _OPTIMAL_STOPPING_MIN_SUBTOPICS
                    and len(draft1.citations) >= _OPTIMAL_STOPPING_MIN_CITATIONS
                ):
                    good_enough = True
            if not good_enough:
                # Fan out remaining drafts in parallel
                remaining = await asyncio.gather(*[
                    _make_draft_coro(i) for i in range(1, _N_DRAFTS)
                ])
                results.extend(remaining)
        else:
            # Original parallel fan-out (kill switch or N=1)
            results = await asyncio.gather(*[
                _make_draft_coro(i) for i in range(_N_DRAFTS)
            ])

        # Filter to drafts that parsed + validated
        valid: list[tuple[int, _LLMSectionDraft, str, int, int]] = []
        for i, (draft, dep, wall, repairs) in enumerate(results):
            if draft is not None:
                valid.append((i, draft, dep or "", wall, repairs))

        if not valid:
            # ALL drafts failed → placeholder
            await emit_progress(
                thread_id, "sawc_write", "section_picked",
                section_id=section_id, chosen_idx=-1,
                n_violations=0, fallback="all_drafts_failed",
                structural_score=0.0,
            )
            await emit_progress(
                thread_id, "sawc_write", "section_done",
                section_id=section_id, n_subtopics=0,
                n_citations=0, total_explanation_chars=0,
                n_repairs=sum(r[3] for r in results),
                wall_ms=int((time.monotonic() - t0) * 1000),
                fallback="placeholder",
            )
            return _placeholder_section(
                section_id=section_id,
                heading=section_heading,
                n_repairs=sum(r[3] for r in results),
                deployment_writer=(
                    next((d for _, _, d, _, _ in valid), None)
                    if valid else None
                ),
            )

        # Critic picker over valid drafts (rerank, not regenerate)
        chosen_idx, dep_critic, fallback, structural_score = (
            await _critic_pick_best(
                section_id=section_id,
                section_heading=section_heading,
                n_primary_contribs=n_primary_contribs,
                candidates=[d for _, d, _, _, _ in valid],
                expected_heading=section_heading,
                allowed_hashes=set(allowed_hashes),
                valid_source_keys=set(valid_source_keys),
                thread_id=thread_id,
                vault_rich=vault_rich,
            )
        )

        # Map picker index → original draft index (for transparency)
        original_draft_idx = valid[chosen_idx][0]
        chosen_draft = valid[chosen_idx][1]
        dep_writer = valid[chosen_idx][2]
        chosen_repairs = valid[chosen_idx][4]

        # Re-validate the chosen draft so `issues` is accurate (in case
        # the picker chose one with remaining violations after repair
        # exhaustion)
        chosen_issues = validate_section_against_inputs(
            chosen_draft,
            expected_heading=section_heading,
            allowed_hashes=set(allowed_hashes),
            valid_source_keys=set(valid_source_keys),
            vault_rich=vault_rich,
        )

        await emit_progress(
            thread_id, "sawc_write", "section_picked",
            section_id=section_id,
            chosen_idx=original_draft_idx,
            n_violations=len(chosen_issues),
            fallback=fallback,
            structural_score=structural_score,
            deployment_critic=dep_critic,
        )

        section = Section(
            section_id=section_id,
            heading=chosen_draft.heading,
            intro=chosen_draft.intro,
            subtopics=chosen_draft.subtopics,
            citations=chosen_draft.citations,
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment_writer=dep_writer,
            deployment_critic=dep_critic,
            n_drafts_tried=_N_DRAFTS,
            n_repairs=chosen_repairs,
            chosen_draft_idx=original_draft_idx,
            structural_score=structural_score,
            fallback_picker=fallback,
            issues=chosen_issues,
        )

        total_expl_chars = sum(
            len(st.explanation) for st in section.subtopics
        )
        await emit_progress(
            thread_id, "sawc_write", "section_done",
            section_id=section_id,
            n_subtopics=len(section.subtopics),
            n_citations=len(section.citations),
            total_explanation_chars=total_expl_chars,
            n_repairs=chosen_repairs,
            wall_ms=section.wall_ms,
        )
        return section


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    digest_manifest_hash: str,
    refine_iter: int = 0,
) -> str:
    """Content-addressed manifest hash for sawc cache key. Includes
    refine_iter (2026-05-24, CoRefine loop closure) so each mgsr→sawc loop
    iteration produces fresh drafts via bandit-routed exploration — without
    this, the cache would short-circuit the loop with stale results."""
    payload = (
        f"outline={outline_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={SAWC_PROMPT_VERSION}|"
        f"schema={SAWC_SCHEMA_VERSION}|"
        f"iter={refine_iter}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The node
# =============================================================================
@traced("sawc_write")
async def sawc_write(state: SynthState) -> dict:
    """Run the Structure-Aware Writing Controller for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "sawc_path":  "",
            "sawc_stats": {"skipped": "no_slug_or_chapter_id", "wall_ms": 0},
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # ── Load outline + digest blobs ────────────────────────────────────
    outline_key = _outline_latest_key(slug, chapter_id)
    digest_key = _digest_latest_key(slug, chapter_id)

    if not await minio.exists(outline_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":     "outline_not_found",
                "outline_key": outline_key,
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline {outline_key!r} not in MinIO — run outline_sdp first",
        }
    if not await minio.exists(digest_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "digest_not_found",
                "digest_key": digest_key,
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"digest {digest_key!r} not in MinIO — run digest_construct first",
        }

    try:
        outline_text = await minio.read_text(outline_key)
        outline_payload = json.loads(outline_text)
        digest_text = await minio.read_text(digest_key)
        digest_payload = json.loads(digest_text)
    except Exception as e:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline/digest unreadable: {type(e).__name__}: {e}",
        }

    outline_data = outline_payload.get("outline") or {}
    outline_sections = outline_data.get("sections") or []
    challenges = outline_data.get("challenges") or []
    flashcards = outline_data.get("flashcards") or []
    dag = outline_payload.get("dag") or {}
    stages_raw = dag.get("stages") or {}
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    per_section_index: dict[str, list[dict]] = (
        digest_payload.get("per_section") or {}
    )
    per_source_list: list[dict] = digest_payload.get("per_source") or []
    valid_source_keys: list[str] = sorted({
        s.get("source_key", "") for s in per_source_list
        if s.get("source_key")
    })
    digest_manifest_hash = digest_payload.get("digest_manifest_hash") or ""

    if not outline_sections or not stages_raw:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "empty_outline_or_stages",
                "n_sections": len(outline_sections),
                "n_stages":   len(stages_raw),
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline has {len(outline_sections)} sections, dag "
                      f"has {len(stages_raw)} stages — both must be >0",
        }

    # Build section_id → outline_section lookup
    sections_by_id: dict[str, dict] = {
        s["section_id"]: s for s in outline_sections
    }
    # Normalize stage keys to int and sort
    stages: dict[int, list[str]] = {
        int(k): list(v) for k, v in stages_raw.items()
    }
    sorted_stage_indices = sorted(stages.keys())
    n_sections = len(outline_sections)
    n_stages = len(sorted_stage_indices)

    await emit_progress(
        thread_id, "sawc_write", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_stages=n_stages,
        n_sections=n_sections,
        n_total_drafts=n_sections * _N_DRAFTS,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    # Track the iteration counter for the CoRefine loop (2026-05-24).
    # Each sawc_write invocation bumps it by 1; refine_iter is part of the
    # manifest hash so loop iterations don't cache-hit each other.
    incoming_refine_iter = int(state.get("refine_iter") or 0)
    refine_iter = incoming_refine_iter + 1

    # Ship #6 (2026-05-24) — OP-12 best-seen rescue. Carry the
    # iteration with the highest checklist score across loop turns.
    incoming_best_score = state.get("best_seen_score")
    incoming_best_path = state.get("best_seen_sawc_path")
    incoming_prev_score = state.get("prev_checklist_score")

    manifest_hash = _compute_manifest_hash(
        outline_manifest_hash=outline_manifest_hash,
        digest_manifest_hash=digest_manifest_hash,
        refine_iter=refine_iter,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":      cov.get("n_sections", 0),
                "n_completed":     cov.get("n_sections_completed", 0),
                "n_fallback":      cov.get("n_sections_fallback", 0),
                "n_repairs":       cov.get("n_repairs", 0),
                "n_stages":        cov.get("n_stages", 0),
                "n_total_drafts_fired": cov.get("n_total_drafts_fired", 0),
                "n_picker_fallbacks":   cov.get("n_picker_fallbacks", 0),
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "sawc_write", "done",
                n_sections=stats["n_sections"],
                n_completed=stats["n_completed"],
                n_fallback=stats["n_fallback"],
                n_repairs=stats["n_repairs"],
                total_drafts_fired=stats["n_total_drafts_fired"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[sawc_write] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_completed']}/{stats['n_sections']} sections, "
                f"{stats['n_repairs']} repairs, {elapsed} ms"
            )
            # Ship #6: cache-hit preserves best-seen tracking — we
            # rerun the same draft, so best-seen is unchanged.
            patch = {
                "sawc_path":   latest_key,
                "sawc_stats":  stats,
                "refine_iter": refine_iter,
            }
            if incoming_best_path:
                patch["best_seen_sawc_path"] = incoming_best_path
            if incoming_best_score is not None:
                patch["best_seen_score"] = incoming_best_score
            return patch
        except Exception as e:
            logger.warning(
                f"[sawc_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # ── Visible Vault load (2026-05-24, Ship #1) ──────────────────────
    # Load the full vault entries for every source contributing to this
    # chapter so the writer prompt can render <code id="..." lang="...">
    # {body}</code> envelopes — the LLM sees actual code instead of opaque
    # hashes. Render-time substitution still uses the same hash → vault[id]
    # path so byte-perfect fidelity is preserved (Deterministic Quoting,
    # Yeung 2025; arXiv 2601.03640).
    vault_rich, n_vaults_loaded, n_vaults_skipped = await _load_chapter_vault_rich(
        minio, slug, valid_source_keys,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: visible vault loaded — "
        f"{len(vault_rich)} entries across {n_vaults_loaded} sources "
        f"(skipped {n_vaults_skipped})"
    )

    # ── Stage loop (sequential across stages, parallel within) ─────────
    sem = asyncio.Semaphore(_CONCURRENCY)
    memory_ledger: list[MemoryEntry] = []
    completed_sections: dict[str, Section] = {}
    n_total_drafts_fired = 0
    n_critic_picks = 0
    n_picker_fallbacks = 0

    for stage_idx in sorted_stage_indices:
        stage_section_ids = stages[stage_idx]
        stage_t0 = time.monotonic()
        await emit_progress(
            thread_id, "sawc_write", "stage_start",
            stage_idx=stage_idx,
            n_sections_in_stage=len(stage_section_ids),
            section_ids=stage_section_ids,
        )

        # Freeze memory snapshot for this stage — all sections at this
        # stage see the SAME memory (per SurveyGen-I §3.2.2: memory
        # accumulates BETWEEN stages, not within)
        memory_snapshot = [m.model_dump() for m in memory_ledger]

        async def _run_section(sid: str) -> Section:
            outline_sec = sections_by_id.get(sid)
            if not outline_sec:
                logger.warning(
                    f"[sawc_write] section_id {sid!r} in stages but not in "
                    f"outline.sections — emitting placeholder"
                )
                return _placeholder_section(
                    section_id=sid,
                    heading=sid,
                    n_repairs=0,
                    deployment_writer=None,
                )
            contributions = per_section_index.get(sid) or []
            # Allowed hashes = union of code_refs across all this section's
            # contributions (digest already gave us LLM-grounded routing)
            # Ship #2 (2026-05-24) — code inventory: pedagogical ranking
            # of allowed hashes so the writer prompt presents canonical
            # examples first. The LLM's bandit-routed picks land on the
            # highest-priority hashes when it caps its citations.
            #
            # Ship A (2026-05-24 evening, code-first implementation roadmap):
            # CRITICAL — augment per-section bank from chapter-wide vault
            # when digest under-routes. Empirical observation: digest's LLM
            # often emits empty `code_refs` per contribution → 12+ sections
            # end up with allowed_hashes=[] even though the chapter vault
            # has 1499 entries. Pad with top-20 pedagogically-ranked hashes
            # from the chapter-wide vault when the digest-routed bank is
            # thin. The LLM picks 3-6 from the augmented bank using the
            # existing visible-vault renderer. See
            # docs/KD-CODE-FIRST-IMPLEMENTATION-2026-05-24.md §3 #2.
            allowed_hashes_set: set[str] = set()
            for c in contributions:
                for h in (c.get("code_refs") or []):
                    allowed_hashes_set.add(h)
            # Ship A: augment thin digest-routed banks with chapter-wide
            # vault. Threshold = 6 (below which most sections empirically
            # emit too few code_refs). Pad with up to 20 highest-pedagogy
            # hashes from the chapter vault not already in the routed set.
            _MIN_BANK_SIZE = 6
            _BANK_PAD_TO = 20
            if vault_rich and len(allowed_hashes_set) < _MIN_BANK_SIZE:
                chapter_wide = list(vault_rich.keys())
                ranked_chapter = _rank_hashes_by_pedagogy(
                    chapter_wide, vault_rich,
                )
                needed = _BANK_PAD_TO - len(allowed_hashes_set)
                pads = [
                    h for h in ranked_chapter
                    if h not in allowed_hashes_set
                ][:needed]
                if pads:
                    allowed_hashes_set.update(pads)
                    logger.info(
                        f"[sawc_write] {sid}: digest-routed bank had "
                        f"{len(allowed_hashes_set) - len(pads)} hashes < "
                        f"{_MIN_BANK_SIZE}; padded with {len(pads)} pedagogically-"
                        f"ranked chapter-wide hashes → bank size now "
                        f"{len(allowed_hashes_set)}"
                    )

            # Re-order by pedagogical score (canonical small examples
            # first); fall back to sorted-hash if vault is empty.
            if vault_rich:
                allowed_hashes = _rank_hashes_by_pedagogy(
                    sorted(allowed_hashes_set), vault_rich,
                )
            else:
                allowed_hashes = sorted(allowed_hashes_set)
            n_primary_contribs = sum(
                1 for c in contributions if c.get("relevance") == "primary"
            )
            return await _write_section_best_of_n(
                sem=sem,
                section_id=sid,
                section_heading=outline_sec.get("heading") or sid,
                section_description=outline_sec.get("description") or "",
                section_prerequisites=(
                    outline_sec.get("prerequisites") or []
                ),
                contributions=contributions,
                allowed_hashes=allowed_hashes,
                vault_rich=vault_rich,
                valid_source_keys=valid_source_keys,
                memory=memory_snapshot,
                n_primary_contribs=n_primary_contribs,
                framework=slug,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                thread_id=thread_id,
            )

        section_results = await asyncio.gather(
            *(_run_section(sid) for sid in stage_section_ids),
            return_exceptions=True,
        )

        n_stage_completed = 0
        n_stage_failed = 0
        for sid, result in zip(stage_section_ids, section_results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"[sawc_write] {sid}: gather raised "
                    f"{type(result).__name__}: {result} — emitting placeholder"
                )
                completed_sections[sid] = _placeholder_section(
                    section_id=sid,
                    heading=sections_by_id.get(sid, {}).get("heading", sid),
                    n_repairs=0,
                    deployment_writer=None,
                )
                n_stage_failed += 1
            else:
                completed_sections[sid] = result
                # All non-placeholder sections count toward drafts fired
                n_total_drafts_fired += _N_DRAFTS
                n_critic_picks += 1
                if result.fallback_picker == "structural_score":
                    n_picker_fallbacks += 1
                if "placeholder" in result.issues:
                    n_stage_failed += 1
                else:
                    n_stage_completed += 1

            # Accumulate memory entry deterministically
            sec = completed_sections[sid]
            contribs = per_section_index.get(sid) or []
            try:
                memory_ledger.append(extract_memory_entry(
                    sec,
                    section_contributions=contribs,
                    section_heading=sec.heading,
                ))
            except Exception as e:
                logger.warning(
                    f"[sawc_write] memory extract failed for {sid}: "
                    f"{type(e).__name__}: {e}"
                )

        stage_ms = int((time.monotonic() - stage_t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "stage_done",
            stage_idx=stage_idx,
            n_completed=n_stage_completed,
            n_failed=n_stage_failed,
            wall_ms=stage_ms,
        )

    # ── Assemble + persist ChapterDraft ────────────────────────────────
    # Preserve outline order so downstream consumers can iterate sections
    # in reading order (sawc returns stage-grouped order; flatten back)
    section_order = [s["section_id"] for s in outline_sections]
    final_sections = [
        completed_sections[sid] for sid in section_order
        if sid in completed_sections
    ]

    coverage = compute_sawc_stats(
        sections=final_sections,
        n_stages=n_stages,
        n_total_drafts_fired=n_total_drafts_fired,
        n_critic_picks=n_critic_picks,
        n_picker_fallbacks=n_picker_fallbacks,
    )

    chapter_draft = ChapterDraft(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        sections=final_sections,
        memory_final=memory_ledger,
        challenges=challenges,
        flashcards=flashcards,
        coverage_stats=coverage,
    )

    payload = chapter_draft.model_dump()
    payload["outline_manifest_hash"] = outline_manifest_hash
    payload["digest_manifest_hash"]  = digest_manifest_hash
    payload["sawc_manifest_hash"]    = manifest_hash

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":            coverage.n_sections,
        "n_completed":           coverage.n_sections_completed,
        "n_fallback":            coverage.n_sections_fallback,
        "n_stages":              coverage.n_stages,
        "n_total_drafts_fired":  coverage.n_total_drafts_fired,
        "n_critic_picks":        coverage.n_critic_picks,
        "n_picker_fallbacks":    coverage.n_picker_fallbacks,
        "n_repairs":             coverage.n_repairs,
        "total_subtopics":       coverage.total_subtopics,
        "total_citations":       coverage.total_citations,
        "avg_subtopics_per_section": coverage.avg_subtopics_per_section,
        "avg_explanation_words":     coverage.avg_explanation_words,
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "prompt_version":        SAWC_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "sawc_write", "done",
        n_sections=stats["n_sections"],
        n_completed=stats["n_completed"],
        n_fallback=stats["n_fallback"],
        n_repairs=stats["n_repairs"],
        total_drafts_fired=stats["n_total_drafts_fired"],
        wall_ms=elapsed,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: "
        f"{stats['n_completed']}/{stats['n_sections']} sections written, "
        f"{stats['n_fallback']} fallbacks, {stats['n_repairs']} repairs, "
        f"{stats['n_total_drafts_fired']} drafts fired, "
        f"{stats['n_picker_fallbacks']} picker fallbacks, "
        f"refine_iter={refine_iter}, {elapsed} ms"
    )
    # Ship #6 (2026-05-24) — best-seen tracker. We don't yet know THIS
    # iteration's checklist score (sawc runs before checklist), so the
    # tracker is updated in mgsr_replan once the score is known. Here
    # we just propagate the incoming best-seen forward; if no prior
    # best exists yet, default to the current sawc_path so render has
    # something to fall back on at budget halt.
    patch = {
        "sawc_path":   latest_key,
        "sawc_stats":  stats,
        "refine_iter": refine_iter,
    }
    if incoming_best_path:
        patch["best_seen_sawc_path"] = incoming_best_path
    else:
        # First iteration — current sawc IS the best-seen. We track the
        # VERSIONED key (immutable) not the latest pointer, so render can
        # load this specific iteration even after subsequent iterations
        # overwrite latest_key.
        patch["best_seen_sawc_path"] = versioned_key
    if incoming_best_score is not None:
        patch["best_seen_score"] = incoming_best_score
    return patch


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_sawc_payload(text: str) -> dict:
    """Parse the persisted sawc blob. Returns the full payload dict;
    downstream nodes pick the fields they need (sections, memory_final,
    coverage_stats, etc.)."""
    return json.loads(text)
