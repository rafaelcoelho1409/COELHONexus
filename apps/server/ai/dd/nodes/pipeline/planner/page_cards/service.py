"""Page-card fan-out and content-addressed caching."""
from __future__ import annotations
import domains
from . import domain, params, versions
from ..... import agents, graphs

import asyncio
import json
import logging
import time
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)
Context = graphs.pipeline.planner.entities.PlannerContext


def _manifest_entry_map(entries: list[dict]) -> dict[str, dict]:
    return {entry["key"]: entry for entry in entries if entry.get("key")}


async def page_cards_run(state: graphs.pipeline.planner.states.PlannerState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug") or ""
    source_keys = list(state.get("raw_files") or [])
    if not slug or not source_keys:
        return {"planner_version": "v2", "relevant_files": [], "page_cards_stats": {"skipped": "no_files"}}

    started = time.monotonic()
    graphs.service.emit(params.STEP, "start", n_pages = len(source_keys))
    manifest_obj = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug) or {}
    entries = _manifest_entry_map(manifest_obj.get("entries") or [])
    bodies = await domains.dd.pipeline.storage.service.read_all(blobs, source_keys)
    body_by_key = {key: body or "" for key, body in zip(source_keys, bodies)}
    body_digests = {key: domain.content_digest(body_by_key[key]) for key in source_keys}
    unique: dict[str, list[str]] = {}
    for key in source_keys:
        unique.setdefault(body_digests[key], []).append(key)

    digests = list(unique)
    cached = await domains.dd.pipeline.storage.service.read_items(
        blobs, slug, params.STEP, versions.PROMPT_VERSION, digests,
    )
    cards_by_digest: dict[str, dict] = {
        digest: value["card"]
        for digest, value in cached.items()
        if isinstance(value, dict) and isinstance(value.get("card"), dict)
    }
    todo = [digest for digest in digests if digest not in cards_by_digest]
    if todo:
        await graphs.service.settle(params.STEP, params.SETTLE_DELAY_S)
    semaphore = asyncio.Semaphore(params.CONCURRENCY)
    failed_digests: set[str] = set()
    fallback_digests: set[str] = set()
    llm_digests: set[str] = set()
    reference_kind_reclassified_digests: set[str] = set()
    role = context.agents.page_card() if any(body_by_key[unique[digest][0]].strip() for digest in todo) else None

    async def card_one(digest: str) -> None:
        key = unique[digest][0]
        body = body_by_key[key]
        entry = entries.get(key) or {}
        title = entry.get("title") or key.rsplit("/", 1)[-1]
        url = entry.get("url") or entry.get("source_path") or ""
        if not body.strip():
            cards_by_digest[digest] = domain.fallback_card(key = key, title = title, body = body, url = url)
            fallback_digests.add(digest)
            return
        request = json.dumps({
            "task": "Create a semantic page card grounded in this page.",
            "framework": slug,
            "title": title,
            "url": url,
            "page": body[:params.BODY_CHARS],
        }, ensure_ascii = False)
        meta: dict[str, Any] = {}
        try:
            llm_digests.add(digest)
            async with semaphore:
                answer = await agents.service.ask(
                    role,
                    agents.pipeline.planner.page_card.schemas.PageCard,
                    request,
                    attempts = params.ATTEMPTS,
                    wait_base_s = params.RETRY_WAIT_S,
                    meta = meta,
                )
            raw_card = answer.model_dump()
            cards_by_digest[digest] = domain.normalize_reference_kind(
                raw_card, key = key, title = title, url = url,
            )
            if raw_card.get("kind") == "reference" and cards_by_digest[digest].get("kind") != "reference":
                reference_kind_reclassified_digests.add(digest)
            await domains.dd.pipeline.storage.service.write_item(
                blobs, slug, params.STEP, versions.PROMPT_VERSION, digest,
                {"card": cards_by_digest[digest], "deployment": meta},
            )
        except Exception as error:
            failed_digests.add(digest)
            fallback_digests.add(digest)
            cards_by_digest[digest] = domain.fallback_card(key = key, title = title, body = body, url = url)
            logger.warning(f"[page_cards] {slug}/{key}: fallback card ({type(error).__name__}: {str(error)[:140]})")

    await asyncio.gather(*(card_one(digest) for digest in todo))

    cards: dict[str, dict] = {}
    relevant: list[str] = []
    duplicate_count = 0
    dropped: dict[str, dict] = {}
    for digest, keys_for_body in unique.items():
        canonical = keys_for_body[0]
        card = cards_by_digest.get(digest) or domain.fallback_card(
            key = canonical,
            title = (entries.get(canonical) or {}).get("title") or "",
            body = body_by_key[canonical],
            url = (entries.get(canonical) or {}).get("url") or "",
        )
        entry = entries.get(canonical) or {}
        original_kind = card.get("kind")
        card = domain.normalize_reference_kind(
            card,
            key = canonical,
            title = entry.get("title") or canonical.rsplit("/", 1)[-1],
            url = entry.get("url") or entry.get("source_path") or "",
        )
        if original_kind == "reference" and card.get("kind") != "reference":
            reference_kind_reclassified_digests.add(digest)
        body = body_by_key[canonical]
        cards[canonical] = {
            **card,
            "key": canonical,
            "content_digest": digest,
            "title": entry.get("title") or canonical.rsplit("/", 1)[-1],
            "url": entry.get("url") or entry.get("source_path") or "",
            "source_idx": int(entry.get("idx") or 0),
            "reading_minutes": domains.dd.pipeline.ledger.domain.pages_minutes([body]),
            "n_code_blocks": sum(1 for line in body.splitlines() if line.strip().startswith((chr(96) * 3, "~~~"))) // 2,
        }
        for key in keys_for_body[1:]:
            duplicate_count += 1
            reason = f"Exact duplicate of {canonical}."
            duplicate_entry = entries.get(key) or {}
            cards[key] = {
                **card, "key": key, "content_digest": digest, "keep": False,
                "title": duplicate_entry.get("title") or key.rsplit("/", 1)[-1],
                "url": duplicate_entry.get("url") or duplicate_entry.get("source_path") or "",
                "source_idx": int(duplicate_entry.get("idx") or 0),
                "reason": reason, "duplicate_of": canonical,
            }
            dropped[key] = {"reason": reason, "duplicate_of": canonical}
        if card.get("keep"):
            relevant.append(canonical)
        else:
            dropped[canonical] = {"reason": card.get("reason") or "Dropped by page-card review."}

    manifest_hash = domain.manifest_digest(slug, source_keys, body_digests, versions.PROMPT_VERSION)
    payload = {
        "schema_version": "1",
        "prompt_version": versions.PROMPT_VERSION,
        "framework_slug": slug,
        "manifest_hash": manifest_hash,
        "cards": cards,
        "dropped_pages": dropped,
        "source_keys": source_keys,
    }
    keys = domains.dd.pipeline.storage.keys
    cards_versioned = keys.page_cards_versioned_key(slug, manifest_hash)
    await domains.dd.pipeline.storage.service.write_blob(
        blobs, cards_versioned, keys.page_cards_latest_key(slug), payload,
    )
    stats = {
        "n_pages": len(source_keys), "n_distinct": len(unique), "n_kept": len(relevant),
        "n_dropped": len(dropped), "n_exact_duplicates": duplicate_count,
        "n_llm_cards": len(llm_digests - failed_digests),
        "n_llm_calls": len(llm_digests),
        "n_fallback_cards": len(fallback_digests), "n_cached_cards": len(cached),
        "n_reference_kind_reclassified": len(reference_kind_reclassified_digests),
        "cache_hit": not todo, "manifest_hash": manifest_hash,
        "wall_ms": graphs.service.elapsed_ms(started), "dropped_pages": dropped,
    }
    context.scratch[params.STEP] = {"cards": cards, "dropped_pages": dropped}
    graphs.service.emit(params.STEP, "done", **{key: value for key, value in stats.items() if key != "dropped_pages"})
    logger.info(
        f"[page_cards] {slug}: {len(relevant)}/{len(source_keys)} kept, {len(dropped)} dropped, "
        f"{stats['n_llm_cards']} cards, {stats['n_fallback_cards']} fallback, {stats['wall_ms']} ms"
    )
    return {
        "planner_version": "v2",
        "page_cards_ref": cards_versioned,
        "page_cards_stats": stats,
        "relevant_files": relevant,
        "off_topic_stats": {"n_dropped": len(dropped), "llm_errors": len(failed_digests)},
    }
