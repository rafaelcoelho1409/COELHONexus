"""Build the writer's section evidence from the chapter's canonical code ledger, without a source-router LLM call."""
from __future__ import annotations
import ai, domains
from ..... import graphs

import hashlib
import json
import logging
import re
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)
Context = graphs.pipeline.synth.entities.SynthContext


def _source_excerpt(markdown: str, digest: str) -> str:
    """The prose nearest a code sentinel, kept as evidence for the step writer and checklist judge."""
    pattern = domains.dd.ingestion.vault.patterns.SENTINEL_RE
    matches = list(pattern.finditer(markdown))
    target_index = next((index for index, match in enumerate(matches) if match.group("hash") == digest), None)
    if target_index is None:
        return ""
    target = matches[target_index]
    previous_end = matches[target_index - 1].end() if target_index else 0
    next_start = matches[target_index + 1].start() if target_index + 1 < len(matches) else len(markdown)
    start = max(previous_end, target.start() - 1_000)
    end = min(next_start, target.end() + 1_000)
    excerpt = markdown[start:target.start()] + " " + markdown[target.end():end]
    excerpt = pattern.sub("", excerpt)
    excerpt = re.sub(r"[ \t]+", " ", excerpt)
    excerpt = re.sub(r"\n{3,}", "\n\n", excerpt)
    return " ".join(excerpt.split())[:900]


def _summary(block, heading: str, excerpt: str) -> str:
    label = ", ".join(block.defines[:3]) or f"{block.lang or 'code'} example"
    context = f" Source text: {excerpt}" if excerpt else ""
    return f"Canonical example for {heading}: the source block demonstrates {label}.{context}"[:1_200]


def _facts(block, excerpt: str) -> list[str]:
    sentences = [sentence.strip(" -•") for sentence in re.split(r"(?<=[.!?])\s+", excerpt) if len(sentence.strip()) >= 20]
    facts = [sentence[:300] for sentence in sentences[:3]]
    if block.defines:
        facts.append(f"The example defines {', '.join(block.defines[:3])}.")
    elif block.imports:
        facts.append(f"The example uses {', '.join(block.imports[:3])}.")
    elif not facts:
        facts.append(f"The source contains a {block.lang or 'code'} example with {block.lines} non-empty lines.")
    return facts[:4]


async def ledger_prepare(state: graphs.pipeline.synth.states.SynthState, runtime: Runtime[Context]) -> dict:
    """Persist a digest-compatible artifact whose evidence assignments are fully determined by the ledger and outline."""
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return graphs.pipeline.synth.service.failure(
            "ledger_path", "ledger_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )

    outline_key = ai.dd.nodes.pipeline.synth.outline.keys.latest_blob_key(slug, chapter_id)
    outline = await domains.dd.pipeline.storage.service.load_json(blobs, outline_key)
    if outline is None:
        return graphs.pipeline.synth.service.failure(
            "ledger_path", "ledger_stats", "outline_not_found", f"outline {outline_key!r} not in MinIO", started, outline_key = outline_key,
        )
    source_keys = sorted(set(outline.get("source_keys") or []))
    sections = (outline.get("outline") or {}).get("sections") or []
    if not source_keys or not sections:
        return graphs.pipeline.synth.service.failure(
            "ledger_path", "ledger_stats", "empty_outline_or_sources", "outline must contain at least one source and section", started,
        )

    ledger = await domains.dd.pipeline.ledger.service.build(blobs, slug, source_keys)
    source_bodies = list(await blobs.read_many(source_keys))
    source_texts: dict[str, str] = {}
    for source_key, body in zip(source_keys, source_bodies):
        if not body:
            continue
        text = body.decode("utf-8", errors = "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        if "<code-ref hash=" not in text:
            try:
                text, _entries = domains.dd.ingestion.vault.domain.sentinelize_doc(text)
            except Exception as error:
                logger.info(f"[ledger_prepare] could not create source sentinels for {source_key!r}: {type(error).__name__}")
        source_texts[source_key] = text
    section_by_id = {section.get("section_id"): section for section in sections if section.get("section_id")}
    per_section: dict[str, list[dict]] = {section_id: [] for section_id in section_by_id}
    per_source: dict[str, dict] = {
        source_key: {
            "source_key": source_key,
            "source_title": source_key.rsplit("/", 1)[-1],
            "overall_summary": "Source-grounded examples assigned by the canonical code ledger.",
            "contributes_to": [],
            "unassigned_code_refs": [],
        }
        for source_key in source_keys
    }
    assigned: set[str] = set()

    for section_id, section in section_by_id.items():
        for digest in list(section.get("code_refs") or []):
            block = ledger.blocks.get(digest)
            if block is None:
                continue
            source_key = next((key for key in block.pages if key in per_source), "")
            if not source_key:
                continue
            excerpt = _source_excerpt(source_texts.get(source_key, ""), digest)
            contribution = {
                "section_id": section_id,
                "source_key": source_key,
                "relevance": "primary",
                "summary": _summary(block, section.get("heading") or section_id, excerpt),
                "key_facts": _facts(block, excerpt),
                "code_refs": [digest],
            }
            per_section[section_id].append(contribution)
            per_source[source_key]["contributes_to"].append(contribution)
            assigned.add(digest)

    orphaned = [digest for digest in ledger.must_teach if digest not in assigned]
    coverage = {
        "n_sources": len(source_keys),
        "n_sections": len(section_by_id),
        "sections_with_primary": sum(bool(contributions) for contributions in per_section.values()),
        "empty_sections": [section_id for section_id, contributions in per_section.items() if not contributions],
        "over_spread_sources": [],
        "orphan_code_refs": len(orphaned),
        "avg_sources_per_section": sum(bool(items) for items in per_section.values()) / max(len(per_section), 1),
        "avg_sections_per_source": sum(len(item["contributes_to"]) for item in per_source.values()) / max(len(per_source), 1),
    }
    manifest_payload = {
        "outline": outline.get("manifest_hash") or "",
        "ledger": [(digest, ledger.blocks[digest].pages) for digest in ledger.must_teach],
        "assignments": [(section_id, section.get("code_refs") or []) for section_id, section in section_by_id.items()],
    }
    manifest_hash = hashlib.sha256(json.dumps(manifest_payload, sort_keys = True).encode("utf-8")).hexdigest()[:16]
    latest_key = ai.dd.nodes.pipeline.synth.ledger_prepare.keys.latest_blob_key(slug, chapter_id)
    versioned_key = ai.dd.nodes.pipeline.synth.ledger_prepare.keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    payload = {
        "schema_version": "synth-v2-code-ledger-1",
        "prompt_version": "deterministic-code-ledger-v1",
        "chapter_id": chapter_id,
        "chapter_title": outline.get("chapter_title") or chapter_id,
        "framework_slug": slug,
        "source_keys": source_keys,
        "n_total_vault_hashes": len(ledger.blocks),
        "per_source": list(per_source.values()),
        "per_section": per_section,
        "merged_sections": {},
        "coverage_stats": coverage,
        "digest_manifest_hash": manifest_hash,
        "outline_manifest_hash": outline.get("manifest_hash") or "",
        "ledger_manifest_hash": manifest_hash,
        "must_teach": list(ledger.must_teach),
        "orphaned_must_teach": orphaned,
    }
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await blobs.write(versioned_key, body, content_type = "application/json")
    await blobs.write(latest_key, body, content_type = "application/json")

    elapsed = graphs.service.elapsed_ms(started)
    stats = {
        "n_sources": len(source_keys),
        "n_sections": len(section_by_id),
        "n_sections_covered": coverage["sections_with_primary"],
        "n_best_code": len(ledger.must_teach),
        "n_assigned": len(assigned),
        "n_orphan_code_refs": len(orphaned),
        "wall_ms": elapsed,
        "store_path": latest_key,
        "versioned_path": versioned_key,
        "manifest_hash": manifest_hash,
        "cache_hit": False,
    }
    graphs.service.emit(
        "ledger_prepare", "done", chapter_id = chapter_id, n_best_code = stats["n_best_code"], n_assigned = stats["n_assigned"],
        n_sections = stats["n_sections"], n_orphan_code_refs = stats["n_orphan_code_refs"], wall_ms = elapsed,
    )
    logger.info(
        f"[ledger_prepare] {slug}/{chapter_id}: assigned {stats['n_assigned']}/{stats['n_best_code']} canonical examples across "
        f"{stats['n_sections']} outline sections; {elapsed} ms"
    )
    return {"ledger_path": latest_key, "ledger_stats": stats, "digest_path": latest_key, "digest_stats": stats}
