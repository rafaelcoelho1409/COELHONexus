"""Post-ingest normalization.

Two responsibilities:

  1. Monolith split — when a tier produced one large markdown blob (mainly
     Tier 1's llms-full.txt), break it on H1/H2 boundaries into per-section
     files. Uses markdown-it-py's token stream so code fences, tables, and
     HTML blocks are atomic — never bisected.

  2. Cleanup pass — drop heading-only stubs (<64 B body) and SHA256-dedupe
     byte-identical sections. Both fixes carried forward from the verified
     v3 baseline; together they remove the noise that was inflating embed
     budget and creating tiny micro-clusters in the planner downstream.
"""
import hashlib
import logging
import re

from .store import ManifestEntry, Store


logger = logging.getLogger(__name__)

MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000
SPLIT_MIN_SECTION_BYTES = 64        # heading-only stub threshold


def _split_markdown_by_headings(
    text: str,
    split_levels: tuple[int, ...] = (1, 2),
) -> list[tuple[str, str, str]]:
    """Return [(h1, h2, body), ...] in document order.

    Code fences, tables, list blocks, and HTML blocks are atomic tokens —
    they cannot be bisected. Bodies are reconstructed by slicing the
    original text on the (start_line, end_line) ranges reported by
    markdown-it-py, so whitespace and fences round-trip exactly.
    """
    from markdown_it import MarkdownIt
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin

    md = (
        MarkdownIt("commonmark", {"html": True})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(deflist_plugin)
        .use(footnote_plugin)
    )
    tokens = md.parse(text)
    lines = text.splitlines(keepends=True)

    boundaries: list[tuple[int, int, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open" or tok.map is None:
            continue
        try:
            level = int(tok.tag[1])
        except (ValueError, IndexError):
            continue
        if level not in split_levels:
            continue
        heading_text = ""
        if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
            heading_text = (tokens[i + 1].content or "").strip()
        boundaries.append((tok.map[0], level, heading_text))

    if not boundaries:
        return [("", "", text)] if text else []

    sections: list[tuple[str, str, str]] = []
    if boundaries[0][0] > 0:
        preamble = "".join(lines[:boundaries[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble))

    current_h1 = ""
    for j, (start, level, heading_text) in enumerate(boundaries):
        end = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(lines)
        body = "".join(lines[start:end])
        if level == 1:
            current_h1 = heading_text
            sections.append((heading_text, "", body))
        else:
            sections.append((current_h1, heading_text, body))
    return sections


def _slugify_heading(s: str, fallback: str) -> str:
    s2 = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]
    return s2 or fallback


def split_monolith(
    body: str,
    parent_slug: str,
) -> tuple[list[tuple[str, str]], int, int]:
    """Split a single large markdown body into (slug, section_body) pairs.

    Returns (writes, stubs_dropped, duplicates_dropped). Pure function —
    caller is responsible for persistence.

    Below MONOLITH_SPLIT_THRESHOLD_BYTES → returns the body unchanged as
    a single-entry list (idempotent for already-small or pre-split corpora).
    """
    if len(body.encode("utf-8")) < MONOLITH_SPLIT_THRESHOLD_BYTES:
        return [(parent_slug, body)], 0, 0

    sections = _split_markdown_by_headings(body, split_levels=(1, 2))
    if len(sections) < 3:
        return [(parent_slug, body)], 0, 0

    width = max(4, len(str(max(0, len(sections) - 1))))
    writes: list[tuple[str, str]] = []
    for i, (h1, h2, sec_body) in enumerate(sections):
        heading = h2 or h1 or f"section-{i:0{width}d}"
        sub = _slugify_heading(heading, f"section-{i:0{width}d}")
        base = sub if sub.startswith(parent_slug) else f"{parent_slug}-{sub}"
        writes.append((f"{i:0{width}d}-{base}", sec_body))

    # Drop heading-only stubs.
    pre = len(writes)
    writes = [
        (s, b) for s, b in writes
        if len(b.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    stubs_dropped = pre - len(writes)

    # SHA256 content dedup. Keep first occurrence (lowest-ordinal slug) so
    # document order is preserved for downstream consumers.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    duplicates_dropped = 0
    for s, b in writes:
        h = hashlib.sha256(b.encode("utf-8")).hexdigest()
        if h in seen:
            duplicates_dropped += 1
            continue
        seen.add(h)
        deduped.append((s, b))
    writes = deduped

    if stubs_dropped or duplicates_dropped:
        logger.info(
            f"[post] split cleanup: -{stubs_dropped} stubs, "
            f"-{duplicates_dropped} dupes; {len(writes)} survive (was {pre})"
        )
    return writes, stubs_dropped, duplicates_dropped


def dedup_pages(
    pages: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], int, int]:
    """Dedup a multi-page corpus (tier 2/3/4).

    Input: [(slug, url, body), ...]
    Output: (filtered, stubs_dropped, duplicates_dropped)

    Drops stubs and SHA256-dedupes. Same rules as the monolith split's
    cleanup phase — applied to the page set from multi-URL tiers.
    """
    pre = len(pages)
    kept = [
        (s, u, b) for s, u, b in pages
        if len(b.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    stubs_dropped = pre - len(kept)

    seen: set[str] = set()
    deduped: list[tuple[str, str, str]] = []
    duplicates_dropped = 0
    for s, u, b in kept:
        h = hashlib.sha256(b.encode("utf-8")).hexdigest()
        if h in seen:
            duplicates_dropped += 1
            continue
        seen.add(h)
        deduped.append((s, u, b))
    return deduped, stubs_dropped, duplicates_dropped


async def apply_to_store(store: Store) -> dict:
    """If the store holds exactly one large entry, split it. Otherwise dedup
    the page set. Rewrites the manifest + returns a summary dict suitable
    for `Progress.record_post`.

    Bodies live in MinIO — old objects are deleted by their MinIO key as
    the new layout is written; manifest is then atomically replaced.
    """
    from .storage_minio import page_key

    current = store.manifest
    input_files = len(current)
    input_bytes = sum(e.bytes for e in current)

    # Single-entry monolith path
    if input_files == 1 and current[0].bytes >= MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed: {e}")
            return _summary("split", input_files, input_bytes, current)

        writes, stubs, dupes = split_monolith(body, only.slug)
        if len(writes) == 1 and writes[0][1] == body:
            return _summary(
                "split", input_files, input_bytes, current, was_split=False,
            )

        # Wipe the old monolith body, write new sections in parallel.
        await store.delete_body_by_key(only.key)
        new_entries: list[ManifestEntry] = []
        write_batch: list = []
        for new_idx, (slug, sec_body) in enumerate(writes):
            new_key = page_key(store.framework_slug, new_idx, slug)
            write_batch.append((new_key, sec_body, "text/markdown"))
            new_entries.append(ManifestEntry(
                idx=new_idx, slug=slug, url=only.url, tier=only.tier,
                bytes=len(sec_body.encode("utf-8")),
                title=slug, key=new_key,
            ))
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return _summary(
            "split", input_files, input_bytes, new_entries,
            was_split=True, stubs=stubs, dupes=dupes,
        )

    # Multi-page path — dedup
    if input_files == 0:
        return _summary("dedup", 0, 0, [])

    raw_pages: list[tuple[str, str, str]] = []
    for e in current:
        try:
            body = await store.read_body_by_key(e.key)
        except Exception:
            body = ""
        raw_pages.append((e.slug, e.url, body))

    deduped, stubs, dupes = dedup_pages(raw_pages)
    if stubs == 0 and dupes == 0:
        return _summary("dedup", input_files, input_bytes, current)

    # Wipe old MinIO objects, rewrite under contiguous idx (parallel).
    for e in current:
        await store.delete_body_by_key(e.key)
    new_entries = []
    write_batch: list = []
    for new_idx, (slug, url, body) in enumerate(deduped):
        prev = next(
            (e for e in current if e.url == url and e.slug == slug),
            None,
        )
        tier = prev.tier if prev else (current[0].tier if current else "unknown")
        title = prev.title if prev else slug
        new_key = page_key(store.framework_slug, new_idx, slug)
        write_batch.append((new_key, body, "text/markdown"))
        new_entries.append(ManifestEntry(
            idx=new_idx, slug=slug, url=url, tier=tier,
            bytes=len(body.encode("utf-8")),
            title=title, key=new_key,
        ))
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return _summary(
        "dedup", input_files, input_bytes, new_entries,
        was_split=False, stubs=stubs, dupes=dupes,
    )


def _summary(
    kind: str,
    input_files: int,
    input_bytes: int,
    out_entries: list[ManifestEntry],
    *,
    was_split: bool = False,
    stubs: int = 0,
    dupes: int = 0,
) -> dict:
    return {
        "kind": kind,
        "input_files": input_files,
        "input_bytes": input_bytes,
        "output_files": len(out_entries),
        "output_bytes": sum(e.bytes for e in out_entries),
        "was_split": was_split,
        "stubs_dropped": stubs,
        "duplicates_dropped": dupes,
    }
