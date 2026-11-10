"""render_audit_write steps — the nodes of its subgraph: prepare, one code_normalizer per distinct code block, finalize.

Rendering, the byte-exact audit and the writes are deterministic (a vault round-trip: the writer never copies code, the sentinels are replaced by the vault's
fences at materialization). The one model step is the code normalization: ingestion can mangle a block's whitespace (Mintlify MDX export, flattened
indentation), so each distinct code block is repaired once by a code_normalizer agent (Python is checked with a parser tool) and cached by content hash for
every later chapter. A block it cannot repair ships as it came, and still passes the audit."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from ..... import agents, graphs, tools

import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


async def load_per_source_vaults(blobs, slug: str, source_keys: list[str]) -> tuple[dict[str, str], int, int]:
    """Load and merge the per-source vault manifests; a source without one is sentinelized in memory (otherwise nothing resolves and the chapter has no code blocks)."""
    vault = domains.dd.ingestion.vault
    manifests: list[dict] = []
    n_skipped = 0
    n_runtime = 0
    for source_key in source_keys:
        vault_key = keys.source_key_to_vault_key(source_key, slug)
        if await blobs.exists(vault_key):
            try:
                manifests.append(json.loads(await blobs.read_text(vault_key)))
                continue
            except Exception as error:
                logger.warning(f"[render_audit_write] vault {vault_key!r} unreadable: {type(error).__name__}: {error} — falling back to runtime")
        try:
            raw = await blobs.read_text(source_key)
            if not raw or "<code-ref hash=" in raw:
                n_skipped += 1
                continue
            _, entries = vault.domain.sentinelize_doc(raw)
            if entries:
                manifests.append({"entries": {h: (e.model_dump() if hasattr(e, "model_dump") else dict(e)) for h, e in entries.items()}})
                n_runtime += 1
            else:
                n_skipped += 1
        except Exception as error:
            n_skipped += 1
            logger.warning(f"[render_audit_write] runtime-sentinelize failed for {source_key!r}: {type(error).__name__}: {error}")
    merged = domain.merge_vault_entries(manifests)
    if n_runtime:
        logger.info(f"[render_audit_write] {slug}: runtime-sentinelized {n_runtime} sources at vault-load time (no pre-built vaults found); merged vault has {len(merged)} entries total")
    return merged, len(manifests), n_skipped


def normalizable(fence_text: str) -> tuple | None:
    """`(open marker, info string, body, close marker, lang)` of a vault entry worth normalizing, else None (empty, oversized, plain text and terminal output)."""
    if not fence_text or len(fence_text) > params.NORMALIZE_MAX_FENCE_CHARS:
        return None
    parts = domain.split_fence(fence_text)
    if parts is None:
        return None
    open_marker, info_string, body, close_marker = parts
    lang = domain.lang_from_info(info_string)
    if lang in params.SKIP_LANGS or not body.strip():
        return None
    return open_marker, info_string, body, close_marker, lang


async def prepare(state: graphs.pipeline.synth.render.states.RenderState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "chapter_path", "chapter_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    # The best-seen iteration, not whatever ran last: a RETHINK loop that regressed must not ship the worse draft (a versioned key is immutable, so it reads like -latest.json).
    best_seen_path = state.get("best_seen_sawc_path")
    sawc_key = best_seen_path or keys.sawc_latest_key(slug, chapter_id)
    mgsr_key = keys.mgsr_latest_key(slug, chapter_id)
    if not await blobs.exists(sawc_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "chapter_path", "chapter_stats", "sawc_not_found", f"sawc {sawc_key!r} not in MinIO — run sawc_write first", started, sawc_key = sawc_key,
        )}
    if not await blobs.exists(mgsr_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "chapter_path", "chapter_stats", "mgsr_not_found", f"mgsr {mgsr_key!r} not in MinIO — run mgsr_replan first", started, mgsr_key = mgsr_key,
        )}
    sawc = await domains.dd.pipeline.storage.service.load_json(blobs, sawc_key)
    mgsr = await domains.dd.pipeline.storage.service.load_json(blobs, mgsr_key)
    if sawc is None or mgsr is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure("chapter_path", "chapter_stats", "inputs_unreadable", "sawc/mgsr unreadable", started)}

    chapter_title = sawc.get("chapter_title") or chapter_id
    sections = sawc.get("sections") or []
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    mgsr_manifest_hash = mgsr.get("mgsr_manifest_hash") or ""
    decision = mgsr.get("decision") or {}
    graphs.service.emit(
        params.STEP, "start",
        chapter_id = chapter_id, chapter_title = chapter_title, n_sections = len(sections), mgsr_halt = decision.get("halt", True), mgsr_halt_reason = decision.get("halt_reason", "?"),
    )
    manifest_hash = domain.compute_manifest_hash(sawc_manifest_hash = sawc_manifest_hash, mgsr_manifest_hash = mgsr_manifest_hash)
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)

    if await blobs.exists(versioned_key) and await blobs.exists(latest_key):
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            arts = cached.get("artifacts") or []
            # A cache hit is valid only when its content artifacts also exist: defense against a partial-write crash state.
            if all([art.get("minio_key") and await blobs.exists(art["minio_key"]) for art in arts]):
                audit = cached.get("audit") or {}
                elapsed = graphs.service.elapsed_ms(started)
                readme_key = keys.artifact_key(slug, chapter_id, "README.md")
                stats = {
                    "audit_passed": audit.get("audit_passed", False),
                    "n_artifacts": len(arts),
                    "n_code_refs": audit.get("n_code_refs_referenced", 0),
                    "n_resolved": audit.get("n_resolved", 0),
                    "n_missing": len(audit.get("n_missing") or []),
                    "n_byte_drift": len(audit.get("n_byte_drift") or []),
                    "sentinels_in_output": audit.get("sentinels_in_output", 0),
                    "rendered_chars": cached.get("rendered_chars", 0),
                    "wall_ms": elapsed,
                    "store_path": latest_key,
                    "versioned_path": versioned_key,
                    "readme_path": readme_key,
                    "manifest_hash": manifest_hash,
                    "cache_hit": True,
                    "template_version": cached.get("template_version"),
                }
                graphs.service.emit(
                    params.STEP, "done",
                    audit_passed = stats["audit_passed"], n_artifacts = stats["n_artifacts"], n_code_refs = stats["n_code_refs"], n_resolved = stats["n_resolved"],
                    n_missing = stats["n_missing"], n_byte_drift = stats["n_byte_drift"], sentinels_in_output = stats["sentinels_in_output"],
                    rendered_chars = stats["rendered_chars"], wall_ms = elapsed, cache_hit = True,
                )
                logger.info(f"[render_audit_write] {slug}/{chapter_id}: CACHE HIT — audit_passed = {stats['audit_passed']}, refs = {stats['n_resolved']}/{stats['n_code_refs']}, {elapsed} ms")
                return {
                    "finished": True,
                    "chapter_path": readme_key,
                    "chapter_stats": stats,
                    "status": "done" if stats["audit_passed"] else "audit_failed",
                    **({"error": "cached render audit did not pass"} if not stats["audit_passed"] else {}),
                }
            logger.warning("[render_audit_write] cached render_result exists but artifacts missing — re-rendering")
        except Exception as error:
            logger.warning(f"[render_audit_write] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    plan = await domains.dd.pipeline.storage.service.load_json(blobs, keys.planner_latest_key(slug))
    source_keys: list[str] = []
    for chapter in (plan or {}).get("chapters") or []:
        if (chapter or {}).get("id") == chapter_id:
            source_keys = sorted(chapter.get("sources") or [])
            break
    vault, n_loaded, n_skipped = await load_per_source_vaults(blobs, slug, source_keys)

    # Normalize pass: ingestion-time drift (Mintlify MDX flatten, smart quotes, stripped tabs). A block already normalized by an earlier chapter comes from the cache.
    prefix = f"dd/{slug}/synth-cache/normalized/{versions.NORMALIZE_PROMPT_VERSION}/"
    stored = set(await blobs.list(prefix))
    normalized_hashes: set[str] = set()
    todo: list[str] = []
    n_norm = 0
    n_skip_norm = 0
    for h, fence in list(vault.items()):
        fence_text = fence if isinstance(fence, str) else str(fence or "")
        if normalizable(fence_text) is None:
            n_skip_norm += 1
            continue
        key = keys.normalize_cache_key(slug, h)
        if key in stored:
            try:
                cached_text = await blobs.read_text(key)
            except Exception:
                todo.append(h)
                continue
            if cached_text != fence_text:
                vault[h] = cached_text
                normalized_hashes.add(h)
                n_norm += 1
            else:
                n_skip_norm += 1
        else:
            todo.append(h)

    context.scratch[params.STEP] = {
        "slug": slug, "chapter_id": chapter_id, "sections": sections, "source_keys": source_keys, "vault": vault, "normalized_hashes": normalized_hashes,
        "counts": {"normalized": n_norm, "unchanged": n_skip_norm, "loaded": n_loaded, "skipped": n_skipped}, "best_seen_path": best_seen_path,
        "mgsr": mgsr,
    }
    logger.info(f"[render_audit_write] {slug}/{chapter_id}: {len(vault)} vault entries, {n_norm} normalized by an earlier chapter, {len(todo)} to normalize")
    return {
        "finished": False,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "sawc_manifest_hash": sawc_manifest_hash,
        "mgsr_manifest_hash": mgsr_manifest_hash,
        "chapter_title": chapter_title,
        "todo": todo,
    }


async def normalize_block(state: dict, runtime: Runtime[Context]) -> dict:
    """Repair one distinct code block. The repaired fence is cached by content hash; a block the agent cannot repair, or answers badly, ships as it came (still byte-exact for the audit)."""
    context = runtime.context
    data = context.scratch[params.STEP]
    h = state["hash"]
    fence_text = data["vault"][h] if isinstance(data["vault"][h], str) else str(data["vault"][h] or "")
    parts = normalizable(fence_text)
    if parts is None:
        return {"normalized": [{"hash": h, "changed": False}]}
    open_marker, info_string, body, close_marker, lang = parts
    is_python = lang in params.PYTHON_LANGS

    async def check(candidate: dict) -> list[str]:
        if not is_python:
            return []
        ok, error = domain.python_ast_valid(domain.strip_code_fences(candidate.get("code") or ""))
        return [] if ok else [f"The code does not parse as Python: {error}"]

    role = context.agents.code_normalizer(tools = [tools.pipeline.synth.check.tool.check_code_tool(check)])
    try:
        answer = await agents.service.ask(
            role, agents.pipeline.synth.schemas.NormalizedCodeAnswer, json.dumps({"lang": lang or "code", "code": body}, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
    except Exception as error:
        # A failure here only means the original, possibly slightly mangled, code ships verbatim; nothing is cached, so the next chapter that cites this hash tries again.
        logger.warning(f"[render-normalize] agent failed lang={lang!r}: {type(error).__name__}: {error}")
        return {"normalized": [{"hash": h, "changed": False}]}
    fixed = domain.strip_code_fences(answer.code or "")
    # Residual fence markers mean the model nested a fenced block inside its answer: take the largest inner block, or reject.
    if "```" in fixed or "~~~" in fixed:
        inner = domain.extract_largest_fenced_body(answer.code or "")
        if not (inner and inner.strip()):
            logger.info(f"[render-normalize] hash={h} residual fence markers with no extractable inner block — keeping the original")
            return {"normalized": [{"hash": h, "changed": False}]}
        fixed = inner
    if not fixed.strip():
        return {"normalized": [{"hash": h, "changed": False}]}
    if is_python and not domain.python_ast_valid(fixed)[0]:
        logger.info(f"[render-normalize] hash={h} python still does not parse after the agent's checks — keeping its best-effort output")
    new_fence = f"{open_marker}{info_string}\n{fixed}\n{close_marker}"
    try:
        await context.blobs.write(keys.normalize_cache_key(data["slug"], h), new_fence, content_type = "text/plain; charset=utf-8")
    except Exception as error:
        logger.warning(f"[render-normalize] cache write failed for {h}: {type(error).__name__}: {error}")
    changed = new_fence != fence_text
    if changed:
        data["vault"][h] = new_fence
        data["normalized_hashes"].add(h)
    return {"normalized": [{"hash": h, "changed": changed}]}


async def publish_best_checklist(blobs, slug: str, chapter_id: str) -> bool:
    """checklist-latest.json is the evaluation of whatever iteration ran LAST, but render ships the best-seen one (asyncio ch-05: the page showed 15/17 with "all sections present"
    failing for a README that has all four). Once the chapter is rendered nothing re-reads the pointer for feedback, so it is replaced by the shipped iteration's evaluation. Fail-soft."""
    try:
        best_key = keys.checklist_best_key(slug, chapter_id)
        if not await blobs.exists(best_key):
            return False
        await blobs.write(keys.checklist_latest_key(slug, chapter_id), await blobs.read_text(best_key), content_type = "application/json")
        return True
    except Exception as error:
        logger.warning(f"[render_audit_write] {slug}/{chapter_id}: could not publish the best-seen evaluation: {type(error).__name__}: {error}")
        return False


async def finalize(state: graphs.pipeline.synth.render.states.RenderState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    data = context.scratch[params.STEP]
    slug, chapter_id = data["slug"], data["chapter_id"]
    sections = data["sections"]
    vault = data["vault"]
    chapter_title = state["chapter_title"]
    thread_id = state.get("thread_id") or ""
    counts = data["counts"]
    n_norm = counts["normalized"] + sum(1 for r in state.get("normalized") or [] if r["changed"])
    n_skip_norm = counts["unchanged"] + sum(1 for r in state.get("normalized") or [] if not r["changed"])
    graphs.service.emit(
        params.STEP, "inputs_loaded",
        n_sources = len(data["source_keys"]), n_vault_files_loaded = counts["loaded"], n_vault_files_skipped = counts["skipped"], n_vault_entries = len(vault),
        n_codes_normalized = n_norm, n_codes_unchanged = n_skip_norm,
    )

    resolution_log: list[schemas.CodeRefResolution] = []
    sections_ctx = [
        domain.build_section_context(s, vault = vault, resolution_log = resolution_log, normalized_hashes = data["normalized_hashes"])
        for s in sections
    ]
    # Cross-reference within-chapter recycled code blocks and omit misrouted ones (audit-safe: only rewrites code_block strings).
    dedup_stats = domain.dedupe_and_align_sections(sections_ctx, drop_mismatch = params.drop_mismatch())
    if dedup_stats["n_dedup"] or dedup_stats["n_mismatch"]:
        logger.info(f"[render_audit_write] {slug}/{chapter_id}: write-path pass — {dedup_stats['n_dedup']} recycled code block(s) cross-referenced, {dedup_stats['n_mismatch']} misrouted block(s) omitted")

    n_subtopics_total = sum(len(s.get("subtopics") or []) for s in sections)
    n_citations_total = sum(len(s.get("citations") or []) for s in sections)
    chapter_md = domain.render_chapter_md(chapter_title, sections_ctx)
    # Audit AFTER rendering: sentinels_in_output is measured on the rendered markdown.
    audit = domain.compute_audit(resolution_log = resolution_log, vault = vault, rendered_chapter_md = chapter_md)
    coverage = domain.coverage_metrics(sections, data["source_keys"], len(vault), len(audit.n_orphan_unused))
    logger.info(
        f"[render_audit_write] {slug}/{chapter_id}: coverage — cited {coverage['n_sources_cited']}/{coverage['n_sources_total']} sources, "
        f"{coverage['n_code_used']}/{coverage['n_code_available']} code blocks used"
    )

    # CONTENT-PRESENT GATE: empty sections (0 subtopics) pass the byte audit since they have no code refs, which masked two chapters. Fail the audit explicitly.
    n_placeholder_sections = sum(1 for s in sections_ctx if not s.get("subtopics"))
    if n_placeholder_sections:
        audit = audit.model_copy(update = {"audit_passed": False})
        logger.warning(f"[render_audit_write] {slug}/{chapter_id}: {n_placeholder_sections}/{len(sections_ctx)} section(s) are EMPTY placeholders (the writer produced 0 subtopics) — failing the audit")
    graphs.service.emit(
        params.STEP, "rendered",
        chapter_chars = len(chapter_md), n_sections_rendered = len(sections_ctx), n_placeholder_sections = n_placeholder_sections, n_code_refs_resolved = audit.n_resolved,
        n_code_refs_missing = len(audit.n_missing), n_code_refs_drift = len(audit.n_byte_drift), sentinels_in_output = audit.sentinels_in_output, audit_passed = audit.audit_passed,
        n_code_deduped = dedup_stats["n_dedup"], n_code_mismatch_omitted = dedup_stats["n_mismatch"],
    )

    readme_key = keys.artifact_key(slug, chapter_id, "README.md")
    await blobs.write(readme_key, chapter_md, content_type = "text/markdown; charset=utf-8")
    artifacts = [schemas.RenderedArtifact(
        name = "README.md", minio_key = readme_key, size_bytes = len(chapter_md.encode("utf-8")), sha256 = domain.sha256_bytes(chapter_md),
    )]
    graphs.service.emit(
        params.STEP, "artifacts_written",
        n_artifacts = len(artifacts), total_bytes = sum(a.size_bytes for a in artifacts), artifact_names = [a.name for a in artifacts],
    )
    # Tutorial chapters also ship the code as a folder to run (each step's examples, the capstone and `main.py`).
    code_files = domain.code_files(sections_ctx) if graphs.pipeline.synth.params.tutorial_mode() else {}
    for relative, content in code_files.items():
        await blobs.write(keys.artifact_key(slug, chapter_id, relative), content, content_type = "text/plain; charset=utf-8")
    if code_files:
        graphs.service.emit(params.STEP, "code_exported", n_files = len(code_files))

    # Fingerprint of the plan chapter this render was built from (fail-soft: a missing plan just leaves it empty, i.e. "valid").
    plan_hash = ""
    plan = await domains.dd.pipeline.storage.service.load_json(blobs, keys.planner_latest_key(slug))
    if plan is not None:
        plan_hash = domain.plan_sources_hash(next((c for c in plan.get("chapters") or [] if isinstance(c, dict) and c.get("id") == chapter_id), None))

    elapsed = graphs.service.elapsed_ms(state["started"])
    result = schemas.RenderResult(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        artifacts = artifacts,
        audit = audit,
        rendered_chars = len(chapter_md),
        n_sections = len(sections),
        n_subtopics_total = n_subtopics_total,
        n_citations_total = n_citations_total,
        sawc_manifest_hash = state["sawc_manifest_hash"],
        mgsr_manifest_hash = state["mgsr_manifest_hash"],
        render_manifest_hash = state["manifest_hash"],
        wall_ms = elapsed,
        # Passed explicitly: the schema's default "" makes the chapters API return None, which breaks the web's canvas repaint after a refresh.
        thread_id = thread_id,
        code_files = sorted(code_files),
        plan_sources_hash = plan_hash,
        **coverage,
    )
    body = json.dumps(result.model_dump(), indent = 2, ensure_ascii = False)
    await blobs.write(state["versioned_key"], body, content_type = "application/json")
    await blobs.write(state["latest_key"], body, content_type = "application/json")

    stats = {
        "audit_passed": audit.audit_passed,
        "n_artifacts": len(artifacts),
        "n_code_refs": audit.n_code_refs_referenced,
        "n_resolved": audit.n_resolved,
        "n_missing": len(audit.n_missing),
        "n_byte_drift": len(audit.n_byte_drift),
        "n_orphan_unused": len(audit.n_orphan_unused),
        "sentinels_in_output": audit.sentinels_in_output,
        "rendered_chars": len(chapter_md),
        "n_sections": len(sections),
        "n_subtopics_total": n_subtopics_total,
        "n_citations_total": n_citations_total,
        "n_vault_files_loaded": counts["loaded"],
        "n_vault_files_skipped": counts["skipped"],
        "n_vault_entries": len(vault),
        "n_codes_normalized": n_norm,
        **coverage,
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "readme_path": readme_key,
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "template_version": versions.RENDER_TEMPLATE_VERSION,
    }
    graphs.service.emit(
        params.STEP, "done",
        audit_passed = audit.audit_passed, n_artifacts = len(artifacts), n_code_refs = audit.n_code_refs_referenced, n_resolved = audit.n_resolved, n_missing = len(audit.n_missing),
        n_byte_drift = len(audit.n_byte_drift), sentinels_in_output = audit.sentinels_in_output, rendered_chars = len(chapter_md), wall_ms = elapsed,
    )
    logger.info(
        f"[render_audit_write] {slug}/{chapter_id}: sawc source = {'best-seen' if data['best_seen_path'] else 'latest'}, audit_passed = {audit.audit_passed}, "
        f"{audit.n_resolved}/{audit.n_code_refs_referenced} code_refs resolved, {len(audit.n_missing)} missing, {len(audit.n_byte_drift)} drift, "
        f"{audit.sentinels_in_output} sentinels left; {len(artifacts)} artifact(s) written ({sum(a.size_bytes for a in artifacts)} bytes); {elapsed} ms"
    )
    if data["best_seen_path"]:
        await publish_best_checklist(blobs, slug, chapter_id)
    patch = {"chapter_path": readme_key, "chapter_stats": stats}
    if not audit.audit_passed:
        patch["status"] = "audit_failed"
        patch["error"] = f"render audit failed: missing = {len(audit.n_missing)} drift = {len(audit.n_byte_drift)} unresolved_sentinels = {audit.sentinels_in_output}"
    elif not bool(mgsr.get("checklist_chapter_passed", False)):
        patch["status"] = "needs_review"
        patch["error"] = "The chapter rendered, but one or more required Synth quality checks remain unresolved. See checklist feedback."
    else:
        patch["status"] = "done"
    return patch
