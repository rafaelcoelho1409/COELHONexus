"""digest_construct steps — the nodes of its subgraph: prepare, one source_router per chunk, collect (the second wave), finalize.

Each source page (or chunk of a page with many code blocks) is routed to the outline's sections by a source_router agent that checks its digest with a
tool. Every routed chunk is stored on its own, so a resumed chapter routes only what is missing. A chunk that fails gets one more try after a pause;
a source whose chunks all failed is kept as a heuristic stub (its opening text and every code block), and the deterministic passes that follow (route
the unrouted, fold overlapping sections, drop empty ones, index per section, coverage) are unchanged."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from ..... import agents, graphs, tools

import asyncio
import hashlib
import json
import logging
import time
from typing import Optional

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


def chunk_id(key: str, chunk: int) -> str:
    return f"{key}#{chunk}"


async def prepare(state: graphs.pipeline.synth.digest.states.DigestState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "digest_path", "digest_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    outline_key = keys.outline_latest_key(slug, chapter_id)
    outline_payload = await domains.dd.pipeline.storage.service.load_json(blobs, outline_key)
    if outline_payload is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "digest_path", "digest_stats", "outline_not_found", f"outline {outline_key!r} not in MinIO (or unreadable); run outline_sdp first", started,
            outline_key = outline_key,
        )}
    outline_sections = (outline_payload.get("outline") or {}).get("sections") or []
    source_keys = sorted(outline_payload.get("source_keys") or [])
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""
    if not outline_sections or not source_keys:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "digest_path", "digest_stats", "empty_outline_or_sources",
            f"outline has {len(outline_sections)} sections and {len(source_keys)} sources — both must be >0", started,
            n_sections = len(outline_sections), n_sources = len(source_keys),
        )}

    graphs.service.emit(params.STEP, "start", chapter_id = chapter_id, chapter_title = chapter_title, n_sections = len(outline_sections), n_sources = len(source_keys))

    # Sources without pre-built vaults have no sentinels; the router would route zero code refs. Sentinelize them here, in memory.
    vault = domains.dd.ingestion.vault
    bodies = list(await blobs.read_many(source_keys))
    pairs: list[tuple[str, str]] = []
    for key, raw in zip(source_keys, bodies):
        if not raw:
            logger.warning(f"[digest_construct] empty body for source {key!r}; skipping")
            continue
        text = raw.decode("utf-8", errors = "replace") if isinstance(raw, (bytes, bytearray)) else raw
        if "<code-ref hash=" not in text:
            try:
                text, _entries = vault.domain.sentinelize_doc(text)
            except Exception as error:
                logger.warning(f"[digest_construct] runtime-sentinelize failed for {key!r}: {type(error).__name__}: {error}; using the raw body")
        pairs.append((key, text))
    if not pairs:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "digest_path", "digest_stats", "all_sources_empty", "every source body returned empty from MinIO", started,
        )}

    total_bytes = sum(len(body) for _, body in pairs)
    all_hashes: set[str] = set()
    for _, body in pairs:
        all_hashes.update(domain.extract_vault_hashes(body))
    graphs.service.emit(
        params.STEP, "outline_loaded",
        n_sections = len(outline_sections), n_sources = len(pairs), n_total_vault_hashes = len(all_hashes), total_bytes = total_bytes,
    )

    manifest_hash = domain.compute_manifest_hash(
        outline_manifest_hash = outline_manifest_hash, source_keys = [key for key, _ in pairs], sources_bytes = total_bytes,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)

    if await blobs.exists(versioned_key) and await blobs.exists(latest_key):
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = graphs.service.elapsed_ms(started)
            stats = {
                "n_sources": len(cached.get("per_source") or []),
                "n_sections": cov.get("n_sections", 0),
                "n_sections_covered": cov.get("sections_with_primary", 0),
                "n_empty_sections": len(cov.get("empty_sections") or []),
                "n_merged_sections": len(cached.get("merged_sections") or {}),
                "merged_sections": cached.get("merged_sections") or {},
                "n_over_spread": len(cov.get("over_spread_sources") or []),
                "n_orphan_code_refs": cov.get("orphan_code_refs", 0),
                "n_pydantic_fail": cached.get("n_pydantic_fail", 0),
                "wall_ms": elapsed,
                "store_path": latest_key,
                "versioned_path": versioned_key,
                "manifest_hash": manifest_hash,
                "cache_hit": True,
                "prompt_version": cached.get("prompt_version"),
            }
            graphs.service.emit(
                params.STEP, "done",
                n_sources = stats["n_sources"], n_sections = stats["n_sections"], n_sections_covered = stats["n_sections_covered"],
                n_empty_sections = stats["n_empty_sections"], n_orphan_code_refs = stats["n_orphan_code_refs"], wall_ms = elapsed, cache_hit = True,
            )
            logger.info(f"[digest_construct] {slug}/{chapter_id}: CACHE HIT — {stats['n_sources']} sources, {stats['n_sections_covered']}/{stats['n_sections']} sections covered, {elapsed} ms")
            return {"finished": True, "digest_path": latest_key, "digest_stats": stats}
        except Exception as error:
            logger.warning(f"[digest_construct] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    items: list[dict] = []
    chunks: dict[str, str] = {}
    for key, body in pairs:
        parts = domain.split_source_by_blocks(body, params.CHUNK_MAX_BLOCKS)
        for index, part in enumerate(parts):
            identity = chunk_id(key, index)
            chunks[identity] = part
            items.append({
                "id": identity, "key": key, "chunk": index, "n_chunks": len(parts),
                "digest": domains.dd.pipeline.storage.domain.digest(versions.DIGEST_PROMPT_VERSION, outline_manifest_hash, key, str(index), hashlib.sha256(part.encode()).hexdigest()),
            })
    stored = await domains.dd.pipeline.storage.service.read_chapter_items(
        blobs, slug, chapter_id, params.STEP, versions.DIGEST_PROMPT_VERSION, [item["digest"] for item in items],
    )
    cached_items = {item["id"]: stored[item["digest"]] for item in items if item["digest"] in stored}
    todo = [item for item in items if item["id"] not in cached_items]
    context.scratch[params.STEP] = {
        "pairs": dict(pairs),
        "chunks": chunks,
        "cached": cached_items,
        "outline_sections": outline_sections,
        "all_hashes": sorted(all_hashes),
        "n_sources": len(pairs),
        "frame": {"framework_slug": slug, "chapter_id": chapter_id, "chapter_title": chapter_title},
    }
    context.counters[params.STEP] = {"routed": 0}
    logger.info(f"[digest_construct] {slug}/{chapter_id}: {len(items)} chunks of {len(pairs)} sources ({len(cached_items)} routed by an earlier run, {len(todo)} to route)")
    return {
        "finished": False,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "outline_manifest_hash": outline_manifest_hash,
        "chapter_title": chapter_title,
        "items": items,
        "todo": todo,
        "wave": 0,
    }


def request_text(state: dict, item: dict, outline_sections: list[dict], text: str, hashes: list[str]) -> str:
    present_hashes = set(hashes)
    return json.dumps(
        {
            "framework": state["framework_slug"],
            "chapter_id": state["chapter_id"],
            "chapter_title": state["chapter_title"],
            "outline": [
                {
                    "section_id": section["section_id"],
                    "heading": section.get("heading"),
                    "description": section.get("description"),
                    "code_refs": [digest for digest in section.get("code_refs") or [] if digest in present_hashes],
                }
                for section in outline_sections
            ],
            "source_key": item["key"],
            "vault_hashes": hashes,
            "source_md": text,
        },
        ensure_ascii = False,
    )


async def route_source(state: dict, runtime: Runtime[Context]) -> dict:
    """Route one chunk. Returns its digest (the router's, normalized), or why it has none."""
    context = runtime.context
    item = state["item"]
    data = context.scratch[params.STEP]
    text = data["chunks"][item["id"]]
    outline_sections = data["outline_sections"]
    valid_sections = {s["section_id"] for s in outline_sections}
    hashes = domain.extract_vault_hashes(text)
    valid_hashes = set(hashes)
    expected_code_sections = {
        digest: section["section_id"]
        for section in outline_sections
        for digest in section.get("code_refs") or []
        if digest in valid_hashes
    }

    async def check(candidate: dict) -> list[str]:
        payload, error = domain.try_parse_payload(candidate)
        if payload is None:
            return [f"The digest does not parse: {error}"]
        return domain.validate_source_digest(
            payload,
            valid_section_ids = valid_sections,
            valid_vault_hashes = valid_hashes,
            expected_code_sections = expected_code_sections,
        )

    frame = data["frame"]
    started = time.monotonic()
    last_error: Optional[str] = None
    payload = None
    cap = params.MAX_SOURCE_CHARS
    for attempt in range(2):
        role = context.agents.source_router(tools = [tools.pipeline.synth.check.tool.check_digest_tool(check)])
        try:
            answer = await agents.service.ask(
                role,
                agents.pipeline.synth.schemas.DigestAnswer,
                request_text(frame, item, outline_sections, text[:cap], hashes),
                attempts = params.ASK_ATTEMPTS,
                wait_base_s = params.RETRY_WAIT_S,
                check = lambda a: bool(a.contributes_to),
            )
        except Exception as error:
            last_error = "context_overflow" if domain.is_context_overflow_error(error) else f"{type(error).__name__}: {str(error)[:200]}"
            if domain.is_context_overflow_error(error) and attempt == 0:
                cap //= 2
                continue
            break
        payload, parse_error = domain.try_parse_payload(answer.model_dump())
        if payload is not None:
            last_error = None
            break
        last_error = f"pydantic_fail: {parse_error}"
        break

    counts = context.counters.setdefault(params.STEP, {"routed": 0})
    counts["routed"] += 1
    wall_ms = graphs.service.elapsed_ms(started)
    if payload is None:
        logger.warning(f"[digest_construct] {item['id']}: routing failed ({last_error})")
        graphs.service.emit(params.STEP, "source_done", source_key = item["key"], chunk = item["chunk"], ok = False, error = (last_error or "")[:120], wall_ms = wall_ms)
        return {"results": [{"id": item["id"], "key": item["key"], "chunk": item["chunk"], "digest": None, "error": last_error or "unknown"}]}

    remaining = domain.validate_source_digest(
        payload,
        valid_section_ids = valid_sections,
        valid_vault_hashes = valid_hashes,
        expected_code_sections = expected_code_sections,
    )
    if remaining:
        # Kept: build_per_section_index drops unknown ids safely.
        logger.info(f"[digest_construct] {item['id']}: kept with {len(remaining)} unresolved issue(s): {remaining[0][:80]}")
    if not payload.source_title or payload.source_title.lower() in {"untitled", "n/a", "none"}:
        payload.source_title = domain.derive_source_title_fallback(text, item["key"])
    # Never asked of the model (it knows which source it is digesting), but the writer's per-section routing reads exactly this field.
    for contribution in payload.contributes_to:
        contribution.source_key = item["key"]
    digest = schemas.SourceDigest(
        source_key = item["key"],
        source_title = payload.source_title,
        overall_summary = payload.overall_summary,
        contributes_to = payload.contributes_to,
        unassigned_code_refs = payload.unassigned_code_refs,
        wall_ms = wall_ms,
    ).model_dump()
    try:
        await domains.dd.pipeline.storage.service.write_chapter_item(
            context.blobs, frame["framework_slug"], frame["chapter_id"], params.STEP, versions.DIGEST_PROMPT_VERSION, item["digest"], digest,
        )
    except Exception as error:
        logger.warning(f"[digest_construct] routed chunk not stored ({type(error).__name__}: {error})")
    graphs.service.emit(
        params.STEP, "source_done",
        source_key = item["key"], chunk = item["chunk"], ok = True, n_contributions = len(payload.contributes_to), n_unassigned = len(payload.unassigned_code_refs), wall_ms = wall_ms,
    )
    return {"results": [{"id": item["id"], "key": item["key"], "chunk": item["chunk"], "digest": digest}]}


async def collect(state: graphs.pipeline.synth.digest.states.DigestState, runtime: Runtime[Context]) -> dict:
    """After a wave: the items that failed go back once, after a pause (the arms that just stalled are ejected for tens of seconds)."""
    done = {r["id"] for r in state.get("results") or [] if r.get("digest") is not None}
    failed = [item for item in state.get("todo") or [] if item["id"] not in done]
    if failed and state.get("wave", 0) == 0:
        logger.info(f"[digest_construct] {state['framework_slug']}/{state['chapter_id']}: retrying {len(failed)}/{len(state['todo'])} failed chunk(s) as a second wave")
        graphs.service.emit(params.STEP, "second_wave", n_failed = len(failed))
        await asyncio.sleep(params.WAVE_PAUSE_S)
        return {"todo": failed, "wave": 1}
    return {"todo": []}


async def finalize(state: graphs.pipeline.synth.digest.states.DigestState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[params.STEP]
    blobs = context.blobs
    slug = state["framework_slug"]
    chapter_id = state["chapter_id"]
    outline_sections = data["outline_sections"]
    pairs = list(data["pairs"].items())

    # The last good result per chunk (the second wave may have fixed a first-wave failure); the stored ones of an earlier run count too.
    by_chunk: dict[str, dict] = dict(data["cached"])
    errors: dict[str, str] = {}
    for result in state.get("results") or []:
        if result.get("digest") is not None:
            by_chunk[result["id"]] = result["digest"]
        else:
            errors.setdefault(result["id"], result.get("error") or "unknown")

    per_source: list[schemas.SourceDigest] = []
    failure_reasons: list[str] = []
    n_stubs = 0
    n_llm_ok = 0
    for key, body in pairs:
        wanted = [item for item in state["items"] if item["key"] == key]
        ok = [schemas.SourceDigest.model_validate(by_chunk[item["id"]]) for item in wanted if item["id"] in by_chunk]
        if ok:
            n_llm_ok += 1
            per_source.append(ok[0] if len(wanted) == 1 else domain.merge_chunk_digests(key, ok))
            continue
        failure_reasons.extend(errors.get(item["id"], "unknown") for item in wanted)
        # A source that failed every attempt is not dropped: a stub (title + opening text, all its code blocks) goes through the same routing as an unrouted source.
        stub = domain.stub_digest_for_failed(key, body)
        if stub is not None:
            per_source.append(stub)
            n_stubs += 1
    n_pydantic_fail = len(failure_reasons)
    error_breakdown: dict[str, int] = {}
    for reason in failure_reasons:
        kind = (reason or "unknown").split(":", 1)[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1
    graphs.service.emit(params.STEP, "digests_aggregated", n_digests_ok = len(per_source), n_pydantic_fail = n_pydantic_fail, n_total = len(pairs))
    if n_stubs:
        logger.info(f"[digest_construct] {slug}/{chapter_id}: {n_stubs} source(s) the router could not digest kept as heuristic stubs (page opening + every code block)")

    section_ids = [s["section_id"] for s in outline_sections]
    per_source, rerouted = domain.route_unrouted_sources(per_source, outline_sections, dict(pairs))
    if rerouted:
        logger.info(f"[digest_construct] {slug}/{chapter_id}: {len(rerouted)} source(s) the router sent to no section were placed by title/summary overlap")
    per_source, merged_sections = domain.merge_overlapping_sections(per_source, outline_sections)
    if merged_sections:
        logger.info(f"[digest_construct] {slug}/{chapter_id}: source-pool merge folded {len(merged_sections)} section(s) → {sorted(set(merged_sections.values()))}")
    n_merged = len(merged_sections)
    merged_sections = domain.drop_empty_sections(per_source, outline_sections, merged_sections)
    if len(merged_sections) > n_merged:
        logger.info(
            f"[digest_construct] {slug}/{chapter_id}: {len(merged_sections) - n_merged} section(s) no source was routed to are not written "
            f"(the writer would invent them): {sorted(list(merged_sections)[n_merged:])}"
        )

    per_section = domain.build_per_section_index(per_source, section_ids)
    coverage = domain.compute_coverage_stats(
        per_source = per_source, per_section = per_section, section_ids = section_ids, all_vault_hashes = data["all_hashes"],
    )
    chapter_digest = schemas.ChapterDigest(
        chapter_id = chapter_id,
        chapter_title = state["chapter_title"],
        framework_slug = slug,
        n_pydantic_fail = n_pydantic_fail,
        per_source = per_source,
        per_section = per_section,
        coverage_stats = coverage,
        merged_sections = merged_sections,
    )
    payload = chapter_digest.model_dump()
    payload["outline_manifest_hash"] = state["outline_manifest_hash"]
    payload["digest_manifest_hash"] = state["manifest_hash"]
    payload["source_keys"] = [key for key, _ in pairs]
    payload["n_total_vault_hashes"] = len(data["all_hashes"])
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await blobs.write(state["versioned_key"], body, content_type = "application/json")
    await blobs.write(state["latest_key"], body, content_type = "application/json")

    elapsed = graphs.service.elapsed_ms(state["started"])
    stats = {
        "n_sources": len(per_source),
        "n_sections": coverage.n_sections,
        "n_sections_covered": coverage.sections_with_primary,
        "n_empty_sections": len(coverage.empty_sections),
        "empty_sections": coverage.empty_sections,
        "n_merged_sections": len(merged_sections),
        "merged_sections": merged_sections,
        "n_over_spread": len(coverage.over_spread_sources),
        "over_spread_sources": coverage.over_spread_sources,
        "n_orphan_code_refs": coverage.orphan_code_refs,
        "n_total_vault_hashes": len(data["all_hashes"]),
        "n_pydantic_fail": n_pydantic_fail,
        "error_breakdown": error_breakdown,
        "avg_sources_per_section": coverage.avg_sources_per_section,
        "avg_sections_per_source": coverage.avg_sections_per_source,
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "prompt_version": versions.DIGEST_PROMPT_VERSION,
    }
    graphs.service.emit(
        params.STEP, "done",
        n_sources = stats["n_sources"], n_sections = stats["n_sections"], n_sections_covered = stats["n_sections_covered"], n_empty_sections = stats["n_empty_sections"],
        n_merged_sections = stats["n_merged_sections"], n_orphan_code_refs = stats["n_orphan_code_refs"], n_pydantic_fail = n_pydantic_fail, wall_ms = elapsed,
    )
    logger.info(
        f"[digest_construct] {slug}/{chapter_id}: {n_llm_ok}/{len(pairs)} sources routed (failed = {dict(sorted(error_breakdown.items()))}, {n_stubs} kept as stubs), "
        f"{stats['n_sections_covered']}/{stats['n_sections']} sections with primary, {stats['n_empty_sections']} empty, {stats['n_orphan_code_refs']} orphan refs, {elapsed} ms"
    )
    return {"digest_path": state["latest_key"], "digest_stats": stats}
