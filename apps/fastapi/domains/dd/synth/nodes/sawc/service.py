"""SAWC — Section-Aware Writer-Critic. v2 cookbook: {heading, intro, subtopics: [{subheading, explanation, code_ref_hash}], citations}.
Best-of-N writer drafts + critic-picker (MAMM-Refine arXiv 2503.15272); 2-attempt repair loop for alignment violations."""
from __future__ import annotations
import domains, infra
from . import domain, keys, params, schemas, versions

import asyncio
import json
import logging
import os
import random
import time
from typing import Optional


logger = logging.getLogger(__name__)


# Draft-call attempts before permanently losing this draft slot (best-of-N
# still covers a single bad draw, but N_DRAFTS=2 shares the SAME prompt —
# a systematic context overflow fails every draft identically, so this
# retry is not redundant with best-of-N).
_MAX_CALL_ATTEMPTS = 2


@infra.langfuse.prompts.with_langfuse_override("dd.synth.sawc.repair")
def build_repair_prompt(
    *,
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
    current_json: str,
    issues: list[str],
    prose_mode: bool = False,
) -> str:
    """Repair prompt: same context as writer + issue list, requesting a corrected output. Wrapped in a LangFuse-managed-override layer (falls back to this local body when no template is published)."""
    prose = prose_mode or not allowed_hashes
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites else "(none)"
    )
    hash_list = (
        "PROSE MODE — this section has NO code. Set EVERY subtopic's "
        "code_ref_hash to \"\" (empty) and write substantial source-grounded "
        "conceptual prose (3-6 distinct subtopics, 40-80 word explanations)."
        if prose
        else (
            "\n".join(f"  - {h}" for h in allowed_hashes)
            if allowed_hashes else "  (none)"
        )
    )
    source_list = (
        "\n".join(f"  - {k}" for k in valid_source_keys)
        if valid_source_keys else "  (none)"
    )
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this cookbook-schema section draft. "
        f"Keep the same v2 schema (heading + intro + subtopics + citations). "
        f"Preserve good subtopics and citations; ONLY change what's "
        f"needed to clear the issues below.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION: {section_id} — {section_heading}\n"
        f"GOAL: {section_description}\n"
        f"PREREQUISITES: {prereqs_str}\n\n"

        f"ALLOWED VAULT HASHES (use ONLY these for subtopics[*].code_ref_hash):\n"
        f"{hash_list}\n\n"

        f"VALID CITATION SOURCE_KEYS (use ONLY these for citations):\n"
        f"{source_list}\n\n"

        f"CONTRIBUTIONS (for grounding):\n"
        f"{domain.format_contributions_block(contributions)}\n\n"

        f"MEMORY:\n{domain.format_memory_block(memory)}\n\n"

        f"CURRENT DRAFT:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Schema reminder: {{\n"
        f'  "heading": "...",\n'
        f'  "intro": "1-2 sentence section framing",\n'
        f'  "subtopics": [\n'
        f'    {{"subheading": "2-10 words", "explanation": "8-80 words", '
        f'"code_ref_hash": "{"" if prose else "16-hex"}"}},\n'
        f'    ... 3-12 entries ...\n'
        f'  ],\n'
        f'  "citations": [{{"source_key": "...", "claim": "..."}}, ...]\n'
        f"}}\n\n"

        f"Respond ONLY with valid JSON matching the v2 schema. "
        f"NO commentary, NO markdown wrapping."
    )


_CONCURRENCY           = 8

async def _load_chapter_vault_rich(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, domains.dd.synth.nodes.vault.schemas.VaultEntry], int, int]:
    """Returns (vault, n_loaded, n_skipped). VaultEntry values (not just fence text) enable Visible Vault with lang+line_count. Mirrors digest's per-source fallback so both nodes have identical vault visibility."""
    rich_vault: dict[str, domains.dd.synth.nodes.vault.schemas.VaultEntry] = {}
    n_loaded = 0
    n_skipped = 0
    for source_key in source_keys:
        vault_key = domains.dd.synth.nodes.render.keys.source_key_to_vault_key(source_key, slug)
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
                        rich_vault[h] = domains.dd.synth.nodes.vault.schemas.VaultEntry(**entry_dict)
                    except Exception:
                        if entry_dict.get("fence_text"):
                            rich_vault[h] = domains.dd.synth.nodes.vault.schemas.VaultEntry(
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
                _, entries = domains.dd.synth.nodes.vault.domain.sentinelize_doc(raw)
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
    prose_mode: bool = False,
    already_shown_hashes: set[str] | None = None,
    citation_fallback: bool = False,
    prior_feedback: list[str] | None = None,
) -> schemas.Section:
    """N drafts → critic-pick → Section. Optimal-Stopping BoN (arXiv 2510.01394): fire draft 1 first; ship directly if it passes zero-violations gate, else parallel fan-out + tournament. Disabled via KD_SAWC_OPTIMAL_STOPPING=false."""
    async with sem:
        t0 = time.monotonic()

        def _make_draft_coro(idx: int):
            return _draft_one_section(
                draft_idx=idx,
                n_total=params.N_DRAFTS,
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
                prose_mode=prose_mode,
                already_shown_hashes=already_shown_hashes,
                prior_feedback=prior_feedback,
            )

        if _OPTIMAL_STOPPING_ENABLED and params.N_DRAFTS >= 2:
            # Fire draft 1 first, decide whether to fire the rest
            r0 = await _make_draft_coro(0)
            results = [r0]
            draft1, _dep1, _wall1, _repairs1, _err1 = r0
            good_enough = False
            if draft1 is not None:
                issues_1 = domain.validate_section_against_inputs(
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
                    _make_draft_coro(i) for i in range(1, params.N_DRAFTS)
                ])
                results.extend(remaining)
        else:
            # Original parallel fan-out (kill switch or N=1)
            results = await asyncio.gather(*[
                _make_draft_coro(i) for i in range(params.N_DRAFTS)
            ])

        valid: list[tuple[int, schemas.LLMSectionDraft, str, int, int]] = []
        draft_errors: list[str] = []
        for i, (draft, dep, wall, repairs, err) in enumerate(results):
            if draft is not None:
                valid.append((i, draft, dep or "", wall, repairs))
            elif err:
                draft_errors.append(err)

        if not valid:
            # ALL drafts failed → placeholder. Tag WHY in .issues (same
            # convention as "placeholder"/"citation_fallback") so
            # sawc_write_run can aggregate an error_breakdown per chapter
            # — before this, a total section failure was undiagnosable
            # from logs alone (digest_construct already had this for
            # per-source failures; sawc_write never did).
            error_summary = ",".join(draft_errors) if draft_errors else "unknown"
            logger.warning(
                f"[sawc_write] {section_id}: ALL {len(results)} draft "
                f"attempt(s) failed ({error_summary}) — emitting placeholder"
            )
            await domains.dd.synth.runtime.progress.service.emit_progress(
                thread_id, "sawc_write", "section_picked",
                section_id=section_id, chosen_idx=-1,
                n_violations=0, fallback="all_drafts_failed",
                structural_score=0.0,
            )
            await domains.dd.synth.runtime.progress.service.emit_progress(
                thread_id, "sawc_write", "section_done",
                section_id=section_id, n_subtopics=0,
                n_citations=0, total_explanation_chars=0,
                n_repairs=sum(r[3] for r in results),
                wall_ms=int((time.monotonic() - t0) * 1000),
                fallback="placeholder",
            )
            return domain.placeholder_section(
                section_id=section_id,
                heading=section_heading,
                n_repairs=sum(r[3] for r in results),
                deployment_writer=(
                    next((d for _, _, d, _, _ in valid), None)
                    if valid else None
                ),
                error_tags=draft_errors,
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

        original_draft_idx = valid[chosen_idx][0]
        chosen_draft = valid[chosen_idx][1]
        dep_writer = valid[chosen_idx][2]
        chosen_repairs = valid[chosen_idx][4]

        # Re-validate the chosen draft so `issues` is accurate (in case
        # the picker chose one with remaining violations after repair
        # exhaustion)
        chosen_issues = domain.validate_section_against_inputs(
            chosen_draft,
            expected_heading=section_heading,
            allowed_hashes=set(allowed_hashes),
            valid_source_keys=set(valid_source_keys),
            vault_rich=vault_rich,
        )

        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "section_picked",
            section_id=section_id,
            chosen_idx=original_draft_idx,
            n_violations=len(chosen_issues),
            fallback=fallback,
            structural_score=structural_score,
            deployment_critic=dep_critic,
        )

        section = schemas.Section(
            section_id=section_id,
            heading=chosen_draft.heading,
            intro=chosen_draft.intro,
            subtopics=chosen_draft.subtopics,
            citations=chosen_draft.citations,
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment_writer=dep_writer,
            deployment_critic=dep_critic,
            n_drafts_tried=params.N_DRAFTS,
            n_repairs=chosen_repairs,
            chosen_draft_idx=original_draft_idx,
            structural_score=structural_score,
            fallback_picker=fallback,
            issues=(
                chosen_issues + ["citation_fallback"]
                if citation_fallback else chosen_issues
            ),
        )

        total_expl_chars = sum(
            len(st.explanation) for st in section.subtopics
        )
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "section_done",
            section_id=section_id,
            n_subtopics=len(section.subtopics),
            n_citations=len(section.citations),
            total_explanation_chars=total_expl_chars,
            n_repairs=chosen_repairs,
            wall_ms=section.wall_ms,
        )
        return section


_OPTIMAL_STOPPING_MIN_SUBTOPICS = 4

_OPTIMAL_STOPPING_MIN_CITATIONS = 2

_OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_SAWC_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")


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
    prose_mode: bool = False,
    already_shown_hashes: set[str] | None = None,
    prior_feedback: list[str] | None = None,
) -> tuple[Optional[schemas.LLMSectionDraft], Optional[str], int, int, Optional[str]]:
    """One writer call → parse → Pydantic → cross-ref → repair. Returns
    (draft, deployment, wall_ms, n_repairs, error_reason); draft=None on
    irrecoverable failure, with error_reason set so the caller can
    aggregate WHY (mirrors digest_construct's error_breakdown — before
    this, a section's total draft failure was undiagnosable from logs)."""
    t0 = time.monotonic()
    allowed_hash_set = set(allowed_hashes)
    valid_source_set = set(valid_source_keys)

    def _build_prompt(vault_char_budget: int | None) -> str:
        return domain.build_writer_prompt(
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
            prose_mode=prose_mode,
            already_shown_hashes=already_shown_hashes,
            vault_char_budget=vault_char_budget,
            prior_feedback=prior_feedback,
        )

    prompt = _build_prompt(None)

    deployment: Optional[str] = None
    response: Optional[str] = None
    last_error: Optional[Exception] = None
    for call_attempt in range(_MAX_CALL_ATTEMPTS):
        try:
            # NIM/Mistral accept response_format=json_schema server-side; Gemini handled by repair loop.
            response, meta = await domains.settings.chat.service.chat_text_async(
                prompt,
                max_tokens=_MAX_TOKENS_DRAFT,
                temperature=_TEMPERATURE_DRAFT,
                response_format=_SAWC_DRAFT_RESPONSE_FORMAT,
                timeout_s=_TIMEOUT_S_DRAFT,
            )
            deployment = (meta or {}).get("deployment")
            last_error = None
            break
        except Exception as e:
            last_error = e
            if call_attempt < _MAX_CALL_ATTEMPTS - 1:
                # The vault-bank cap above should keep this rare, but a
                # content-heavy section (many allowed_hashes, each
                # capped) can still add up — the Rotator has no context
                # -length-aware arm filtering, so retry at half the
                # vault budget rather than just reproducing the failure.
                if domain.is_context_overflow_error(e):
                    prompt = _build_prompt(domain.MAX_VAULT_CHARS_TOTAL // 2)
                await asyncio.sleep(1.0 + random.random())
    if last_error is not None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        error_tag = (
            "context_overflow" if domain.is_context_overflow_error(last_error)
            else type(last_error).__name__
        )
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"{type(last_error).__name__}: {str(last_error)[:120]}",
            wall_ms=wall_ms,
        )
        logger.warning(
            f"[sawc_write] {section_id} draft {draft_idx}: LLM call "
            f"failed after {_MAX_CALL_ATTEMPTS} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        return None, None, wall_ms, 0, error_tag

    parsed = domain.parse_json_response(response)
    if not parsed:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error="parse_failed", wall_ms=wall_ms,
            deployment=deployment,
        )
        logger.info(
            f"[sawc_write] {section_id} draft {draft_idx}: response not "
            f"parseable as JSON"
        )
        return None, deployment, wall_ms, 0, "parse_failed"

    draft, err = domain.try_parse_draft(parsed)
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
            prose_mode=prose_mode,
        )
        try:
            rr, rm = await domains.settings.chat.service.chat_text_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                timeout_s=_TIMEOUT_S_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = domain.parse_json_response(rr)
            if rp:
                current = rp
                draft, err = domain.try_parse_draft(rp)
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: repair "
                f"attempt {n_repairs} failed: {type(e).__name__}: {e}"
            )
            break

    if draft is None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"pydantic_fail: {err}",
            wall_ms=wall_ms, deployment=deployment,
        )
        logger.info(
            f"[sawc_write] {section_id} draft {draft_idx}: pydantic-reject "
            f"after {n_repairs} repair(s): {err}"
        )
        return None, deployment, wall_ms, n_repairs, "pydantic_fail"

    # Cross-ref validation (heading/hashes/citations alignment).
    issues = domain.validate_section_against_inputs(
        draft,
        expected_heading=section_heading,
        allowed_hashes=allowed_hash_set,
        valid_source_keys=valid_source_set,
        vault_rich=vault_rich,
    )
    # Repair on HARD issues only; soft issues (subheading/explanation↔code mismatch) still ship in .issues but skip repair — LLM can't close them reliably.
    while domain.hard_issues(issues) and n_repairs < _MAX_REPAIR_ATTEMPTS:
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
            prose_mode=prose_mode,
        )
        try:
            rr, rm = await domains.settings.chat.service.chat_text_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                timeout_s=_TIMEOUT_S_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = domain.parse_json_response(rr)
            if not rp:
                break
            new_draft, new_err = domain.try_parse_draft(rp)
            if new_draft is None:
                break
            new_issues = domain.validate_section_against_inputs(
                new_draft,
                expected_heading=section_heading,
                allowed_hashes=allowed_hash_set,
                valid_source_keys=valid_source_set,
                vault_rich=vault_rich,
            )
            if len(domain.hard_issues(new_issues)) < len(domain.hard_issues(issues)):
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
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "sawc_write", "section_draft_done",
        section_id=section_id, draft_idx=draft_idx, n_total=n_total,
        ok=True, wall_ms=wall_ms, deployment=deployment,
        n_subtopics=len(draft.subtopics),
        n_citations=len(draft.citations),
        n_violations=len(issues),
    )
    return draft, deployment, wall_ms, n_repairs, None

async def _critic_pick_best(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    candidates: list[schemas.LLMSectionDraft],
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    thread_id: str,
    vault_rich: dict | None = None,
) -> tuple[int, Optional[str], Optional[str], float]:
    """Pairwise tournament picker. fallback_used=None means at least one match got a clean LLM verdict; "structural_score" means all fell back to tiebreak. Returns (chosen_idx, deployment_critic, fallback_used, structural_score)."""
    summaries = [
        domain.summarize_candidate(
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


_TEMPERATURE_DRAFT     = 0.5

_TEMPERATURE_REPAIR    = 0.2

_MAX_TOKENS_DRAFT      = 8000

_MAX_TOKENS_REPAIR     = 8000

# chat_text_async's own default (30s) was undersized — confirmed
# live across 5 study runs (2026-09-05/07): sawc_write's per-section
# drafting is the single heaviest generation task in the whole pipeline
# and was by far the worst-hit, routinely losing entire sections to
# APITimeoutError and driving most of the sustained-outage halts tracked
# in the Synth known-issues doc. Same fix as outline/digest.
#   2026-09-08: raised 120s -> 150s. Percentile analysis (14-day Langfuse
#   trace data): successful-call max=118.9s, p99=106.8s — real successes
#   were stacking right up against the old 120s ceiling. Only ~8% of
#   failures are actually at-ceiling (rescuable by this change); the
#   other ~90%+ fail at a fixed ~30s Rotator-side ReadTimeout this
#   timeout can't reach — see SYNTH-PERFORMANCE-ANALYSIS-2026-09-07.md.
_TIMEOUT_S_DRAFT       = 150.0
_TIMEOUT_S_REPAIR      = 150.0

_MAX_REPAIR_ATTEMPTS   = 2

_SAWC_DRAFT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "section_draft",
        "schema": schemas.LLMSectionDraft.model_json_schema(),
        "strict": False,
    },
}


async def _pairwise_judge_match(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    summary_a: dict,
    summary_b: dict,
) -> tuple[str, Optional[str]]:
    """One pairwise match → (winner_letter ∈ {"A","B"}, deployment_critic). Any parse/call failure falls back to structural-score tiebreak; tournament never aborts."""
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
        # json_object forces {"winner":"A"|"B"} without prose preamble — eliminates most parse-failed tiebreaks.
        response, meta = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens=_MAX_TOKENS_CRITIC,
            temperature=_TEMPERATURE_CRITIC,
            response_format={"type": "json_object"},
            timeout_s=_TIMEOUT_S_CRITIC,
        )
        deployment_critic = (meta or {}).get("deployment")
        parsed = domain.parse_json_response(response)
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


_MAX_TOKENS_CRITIC     = 300
_TIMEOUT_S_CRITIC      = 45.0

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

_TEMPERATURE_CRITIC    = 0.0


async def sawc_write_run(state: domains.dd.synth.state.SynthState) -> dict:
    """Run the Section-Aware Writer-Critic for one chapter."""
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
    minio = domains.dd.ingestion.storage.service.get_storage()

    outline_key = keys.outline_latest_key(slug, chapter_id)
    digest_key = keys.digest_latest_key(slug, chapter_id)

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
    dag = outline_payload.get("dag") or {}
    stages_raw = dag.get("stages") or {}
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    per_section_index: dict[str, list[dict]] = (
        digest_payload.get("per_section") or {}
    )
    # Cross-section vault-hash uniqueness — digest allows the same hash
    # for different sections; without dedup CLI corpora recycle 3-5 H2s.
    n_hashes_deduped, n_refs_removed = domain.dedupe_vault_hashes_across_sections(
        per_section_index,
    )
    if n_hashes_deduped:
        logger.info(
            f"[sawc_write] {slug}/{chapter_id}: cross-section dedup — "
            f"{n_hashes_deduped} hashes claimed by multiple sections; "
            f"removed {n_refs_removed} duplicate code_ref entries"
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

    sections_by_id: dict[str, dict] = {
        s["section_id"]: s for s in outline_sections
    }
    # Normalize stage keys to int and sort
    stages: dict[int, list[str]] = {
        int(k): list(v) for k, v in stages_raw.items()
    }
    # contributions are re-tagged to the winner; writing them produces a
    # hollow shell that bank-padding then back-fills with canonical code.
    merged_away: set[str] = set(
        (digest_payload.get("merged_sections") or {}).keys()
    )
    if merged_away:
        stages = {
            k: [sid for sid in v if sid not in merged_away]
            for k, v in stages.items()
        }
        stages = {k: v for k, v in stages.items() if v}
        logger.info(
            f"[sawc_write] {slug}/{chapter_id}: skipping "
            f"{len(merged_away)} digest-merged section(s) "
            f"{sorted(merged_away)}"
        )
    sorted_stage_indices = sorted(stages.keys())
    n_sections = sum(len(v) for v in stages.values())
    n_stages = len(sorted_stage_indices)

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "sawc_write", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_stages = n_stages,
        n_sections = n_sections,
        n_total_drafts = n_sections * params.N_DRAFTS,
    )

    # Track the iteration counter for the CoRefine loop .
    # Each sawc_write invocation bumps it by 1; refine_iter is part of the
    # manifest hash so loop iterations don't cache-hit each other.
    incoming_refine_iter = int(state.get("refine_iter") or 0)
    refine_iter = incoming_refine_iter + 1

    # Close the self-refine loop on a RETHINK iteration — previously a
    # RETHINK just reran the writer prompt unchanged, with zero signal
    # about what checklist_eval actually flagged, so some iterations
    # regressed instead of improving (confirmed live this session:
    # chapters 3 and 6 both scored worse on iteration 2 than iteration 1).
    prior_feedback: list[str] | None = None
    if incoming_refine_iter > 0:
        prior_feedback = (
            state.get("checklist_stats") or {}
        ).get("failed_feedback") or None

    # Best-seen iteration tracking. Fixed 2026-09-05 — this used to just
    # forward whatever iteration 1 wrote, forever, unconditionally (the
    # "checklist score updated in mgsr_replan after sawc returns" this
    # comment used to promise never actually happened: mgsr_replan has
    # zero references to either field). render_audit_write reads
    # best_seen_sawc_path directly now, so this comparison is what makes
    # "best-seen-rescue" actually rescue the best iteration instead of
    # silently shipping whichever one happened to run last — including
    # ones that regressed after a RETHINK loop made things worse.
    incoming_best_score = state.get("best_seen_score")
    incoming_best_path = state.get("best_seen_sawc_path")
    # Plateau detection (graph._route_after_mgsr) needs THIS iteration's
    # decision to compare against the score from the iteration that just
    # finished. checklist_eval is about to overwrite checklist_stats with
    # a fresh pass_rate — so the LAST iteration's value must be captured
    # here, before that happens, and carried forward as prev_checklist_score.
    # (Previously read as `state.get("prev_checklist_score")` — always None,
    # since nothing ever wrote that field; the plateau halt could never fire.)
    carried_prev_score = (state.get("checklist_stats") or {}).get("pass_rate")

    if incoming_refine_iter > 0 and carried_prev_score is not None:
        # The iteration that JUST finished (N-1) has a real score now —
        # reconstruct its own versioned key (content-addressed, so this is
        # exact, not a guess) and promote it to best-seen if it beats the
        # running record.
        prev_iter_versioned_key = keys.versioned_blob_key(
            slug, chapter_id,
            domain.compute_manifest_hash(
                outline_manifest_hash = outline_manifest_hash,
                digest_manifest_hash = digest_manifest_hash,
                refine_iter = incoming_refine_iter,
            ),
        )
        if incoming_best_score is None or carried_prev_score > incoming_best_score:
            if incoming_best_score is not None:
                logger.info(
                    f"[sawc_write] {slug}/{chapter_id}: new best-seen "
                    f"iteration {incoming_refine_iter} "
                    f"(score {carried_prev_score:.2%} > prior best "
                    f"{incoming_best_score:.2%})"
                )
            incoming_best_score = carried_prev_score
            incoming_best_path = prev_iter_versioned_key

    manifest_hash = domain.compute_manifest_hash(
        outline_manifest_hash = outline_manifest_hash,
        digest_manifest_hash = digest_manifest_hash,
        refine_iter = refine_iter,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = keys.latest_blob_key(slug, chapter_id)

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
            await domains.dd.synth.runtime.progress.service.emit_progress(
                thread_id, "sawc_write", "done",
                n_sections = stats["n_sections"],
                n_completed = stats["n_completed"],
                n_fallback = stats["n_fallback"],
                n_repairs = stats["n_repairs"],
                total_drafts_fired = stats["n_total_drafts_fired"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[sawc_write] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_completed']}/{stats['n_sections']} sections, "
                f"{stats['n_repairs']} repairs, {elapsed} ms"
            )
            # Cache hit preserves best-seen — same draft, unchanged tracking.
            patch = {
                "sawc_path":   latest_key,
                "sawc_stats":  stats,
                "refine_iter": refine_iter,
            }
            if incoming_best_path:
                patch["best_seen_sawc_path"] = incoming_best_path
            if incoming_best_score is not None:
                patch["best_seen_score"] = incoming_best_score
            if carried_prev_score is not None:
                patch["prev_checklist_score"] = carried_prev_score
            return patch
        except Exception as e:
            logger.warning(
                f"[sawc_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # Visible Vault (arXiv 2601.03640): LLM sees actual code bodies; render substitutes via hash → byte-perfect fidelity preserved.
    vault_rich, n_vaults_loaded, n_vaults_skipped = await _load_chapter_vault_rich(
        minio, slug, valid_source_keys,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: visible vault loaded — "
        f"{len(vault_rich)} entries across {n_vaults_loaded} sources "
        f"(skipped {n_vaults_skipped})"
    )

    sem = asyncio.Semaphore(_CONCURRENCY)
    memory_ledger: list[schemas.MemoryEntry] = []
    completed_sections: dict[str, schemas.Section] = {}
    chapter_used_hashes: set[str] = set()
    n_total_drafts_fired = 0
    n_critic_picks = 0
    n_picker_fallbacks = 0

    for stage_idx in sorted_stage_indices:
        stage_section_ids = stages[stage_idx]
        stage_t0 = time.monotonic()
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "stage_start",
            stage_idx = stage_idx,
            n_sections_in_stage = len(stage_section_ids),
            section_ids = stage_section_ids,
        )

        # SurveyGen-I §3.2.2: memory accumulates between stages, not within — all sections in this stage see the same snapshot.
        memory_snapshot = [m.model_dump() for m in memory_ledger]

        async def _run_section(sid: str) -> schemas.Section:
            outline_sec = sections_by_id.get(sid)
            if not outline_sec:
                logger.warning(
                    f"[sawc_write] section_id {sid!r} in stages but not in "
                    f"outline.sections — emitting placeholder"
                )
                return domain.placeholder_section(
                    section_id = sid,
                    heading = sid,
                    n_repairs = 0,
                    deployment_writer = None,
                )
            contributions = per_section_index.get(sid) or []
            allowed_hashes_set: set[str] = set()
            for c in contributions:
                for h in (c.get("code_refs") or []):
                    allowed_hashes_set.add(h)
            # Gate prose_mode on pre-pad count — post-pad would pull stray hashes into a no-code section.
            n_routed_hashes = len(allowed_hashes_set)
            _MIN_BANK_SIZE = 6
            _BANK_PAD_TO = 20
            if vault_rich and len(allowed_hashes_set) < _MIN_BANK_SIZE:
                chapter_wide = list(vault_rich.keys())
                ranked_chapter = domains.dd.synth.nodes.vault.domain.rank_hashes_by_pedagogy(
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

            if vault_rich:
                allowed_hashes = domains.dd.synth.nodes.vault.domain.rank_hashes_by_pedagogy(
                    sorted(allowed_hashes_set), vault_rich,
                )
            else:
                allowed_hashes = sorted(allowed_hashes_set)
            n_primary_contribs = sum(
                1 for c in contributions if c.get("relevance") == "primary"
            )
            # Restrict citations to sources digest_construct routed to THIS section (not chapter-wide) to prevent cross-section drift. Fail-safe: zero-routed sections fall back to chapter-wide.
            section_source_keys: list[str] = sorted({
                c.get("source_key", "") for c in contributions
                if c.get("source_key")
            })
            citation_fallback = not section_source_keys
            if citation_fallback:
                section_source_keys = valid_source_keys
                logger.info(
                    f"[sawc_write] {sid}: digest routed 0 sources to "
                    f"this section; falling back to chapter-wide "
                    f"({len(valid_source_keys)} sources) for citations"
                )
            # PROSE PATH: gate on pre-pad n_routed_hashes (not padded bank) so a no-code section with stray chapter hashes stays prose instead of failing to placeholder.
            prose_mode = (n_routed_hashes == 0) or (len(allowed_hashes) < params.SUBTOPICS_MIN)
            return await _write_section_best_of_n(
                sem = sem,
                section_id = sid,
                section_heading = outline_sec.get("heading") or sid,
                section_description = outline_sec.get("description") or "",
                section_prerequisites = (
                    outline_sec.get("prerequisites") or []
                ),
                contributions = contributions,
                allowed_hashes = allowed_hashes,
                vault_rich = vault_rich,
                valid_source_keys = section_source_keys,
                memory = memory_snapshot,
                n_primary_contribs = n_primary_contribs,
                framework = slug,
                chapter_id = chapter_id,
                chapter_title = chapter_title,
                thread_id = thread_id,
                prose_mode = prose_mode,
                already_shown_hashes = set(chapter_used_hashes),
                citation_fallback = citation_fallback,
                prior_feedback = prior_feedback,
            )

        section_results = await asyncio.gather(
            *(_run_section(sid) for sid in stage_section_ids),
            return_exceptions = True,
        )

        n_stage_completed = 0
        n_stage_failed = 0
        for sid, result in zip(stage_section_ids, section_results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"[sawc_write] {sid}: gather raised "
                    f"{type(result).__name__}: {result} — emitting placeholder"
                )
                completed_sections[sid] = domain.placeholder_section(
                    section_id = sid,
                    heading = sections_by_id.get(sid, {}).get("heading", sid),
                    n_repairs = 0,
                    deployment_writer = None,
                )
                n_stage_failed += 1
            else:
                completed_sections[sid] = result
                # All non-placeholder sections count toward drafts fired
                n_total_drafts_fired += params.N_DRAFTS
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
                memory_ledger.append(domain.extract_memory_entry(
                    sec,
                    section_contributions = contribs,
                    section_heading = sec.heading,
                ))
            except Exception as e:
                logger.warning(
                    f"[sawc_write] memory extract failed for {sid}: "
                    f"{type(e).__name__}: {e}"
                )

            for st in (getattr(sec, "subtopics", None) or []):
                h = getattr(st, "code_ref_hash", "")
                if h:
                    chapter_used_hashes.add(h)

        stage_ms = int((time.monotonic() - stage_t0) * 1000)
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "sawc_write", "stage_done",
            stage_idx = stage_idx,
            n_completed = n_stage_completed,
            n_failed = n_stage_failed,
            wall_ms = stage_ms,
        )

    # Preserve outline order so downstream consumers can iterate sections
    # in reading order (sawc returns stage-grouped order; flatten back)
    section_order = [s["section_id"] for s in outline_sections]
    final_sections = [
        completed_sections[sid] for sid in section_order
        if sid in completed_sections
    ]

    coverage = domain.compute_sawc_stats(
        sections = final_sections,
        n_stages = n_stages,
        n_total_drafts_fired = n_total_drafts_fired,
        n_critic_picks = n_critic_picks,
        n_picker_fallbacks = n_picker_fallbacks,
    )

    # Aggregate WHY any section went fully empty — same idea as
    # digest_construct's error_breakdown, applied to sawc's own draft
    # failures (previously invisible: a total section failure only ever
    # showed up as "0/N sections written," with no reason attached).
    error_breakdown: dict[str, int] = {}
    for s in final_sections:
        for tag in (s.issues or []):
            if tag.startswith("draft_fail:"):
                kind = tag.split(":", 1)[1] or "unknown"
                error_breakdown[kind] = error_breakdown.get(kind, 0) + 1

    chapter_draft = schemas.ChapterDraft(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        sections = final_sections,
        memory_final = memory_ledger,
        coverage_stats = coverage,
    )

    payload = chapter_draft.model_dump()
    payload["outline_manifest_hash"] = outline_manifest_hash
    payload["digest_manifest_hash"]  = digest_manifest_hash
    payload["sawc_manifest_hash"]    = manifest_hash

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":            coverage.n_sections,
        "n_completed":           coverage.n_sections_completed,
        "n_fallback":            coverage.n_sections_fallback,
        "n_citation_fallback":   coverage.n_sections_citation_fallback,
        "n_stages":              coverage.n_stages,
        "n_total_drafts_fired":  coverage.n_total_drafts_fired,
        "n_critic_picks":        coverage.n_critic_picks,
        "n_picker_fallbacks":    coverage.n_picker_fallbacks,
        "n_repairs":             coverage.n_repairs,
        "total_subtopics":       coverage.total_subtopics,
        "total_citations":       coverage.total_citations,
        "avg_subtopics_per_section": coverage.avg_subtopics_per_section,
        "avg_explanation_words":     coverage.avg_explanation_words,
        "error_breakdown":       error_breakdown,
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "prompt_version":        versions.SAWC_PROMPT_VERSION,
    }
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "sawc_write", "done",
        n_sections = stats["n_sections"],
        n_completed = stats["n_completed"],
        n_fallback = stats["n_fallback"],
        n_citation_fallback = stats["n_citation_fallback"],
        n_repairs = stats["n_repairs"],
        total_drafts_fired = stats["n_total_drafts_fired"],
        wall_ms = elapsed,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: "
        f"{stats['n_completed']}/{stats['n_sections']} sections written, "
        f"{stats['n_fallback']} fallbacks "
        f"(failed = {dict(sorted(error_breakdown.items()))}), "
        f"{stats['n_citation_fallback']} citation-fallback (chapter-wide), "
        f"{stats['n_repairs']} repairs, "
        f"{stats['n_total_drafts_fired']} drafts fired, "
        f"{stats['n_picker_fallbacks']} picker fallbacks, "
        f"refine_iter = {refine_iter}, {elapsed} ms"
    )
    # Best-seen comparison already happened above (see carried_prev_score);
    # this just forwards whatever incoming_best_score/incoming_best_path
    # ended up being — either the running record, or this call's own
    # promotion of the iteration that just finished.
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
        # overwrite latest_key.
        patch["best_seen_sawc_path"] = versioned_key
    if incoming_best_score is not None:
        patch["best_seen_score"] = incoming_best_score
    if carried_prev_score is not None:
        patch["prev_checklist_score"] = carried_prev_score
    return patch


# Convenience loader for downstream nodes
