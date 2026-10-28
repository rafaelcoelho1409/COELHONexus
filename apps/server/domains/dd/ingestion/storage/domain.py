"""Pure helpers (no I/O) — a manifest and its entries as stored, the titles a manifest is read with, which entry a page index
names, and the answers the snapshot calls give."""
from __future__ import annotations
import domains
from . import entities, keys, params

import json
from dataclasses import asdict, fields


# ---- the manifest ---------------------------------------------------------------------------------------------------------
def new_entry(
    framework_slug: str,
    idx: int,
    *,
    slug: str,
    url: str,
    tier: str,
    size: int,
    title: str = "",
    section: str = "",
    notes: str = "",
    lastmod: str = "",
    anchors: list | None = None,
) -> entities.ManifestEntry:
    """The manifest entry of the page written as number `idx` (its key says where). A page with no title is titled by its slug."""
    return entities.ManifestEntry(
        idx = idx,
        slug = slug,
        url = url,
        tier = tier,
        bytes = size,
        title = title or slug,
        key = keys.page_key(framework_slug, idx, slug),
        section = section or "",
        notes = notes or "",
        lastmod = lastmod or "",
        anchors = list(anchors or []),
    )


def ordered_by_urls(entries: list[entities.ManifestEntry], url_list: list[str]) -> list[entities.ManifestEntry]:
    """The entries in the order of `url_list` (discovery order): a parallel fetch writes pages in the order they arrive, not the
    order of the site's chapters. A page the list does not name goes last; `entry.idx` is unchanged."""
    url_pos = {u: i for i, u in enumerate(url_list)}
    sentinel = len(url_list)

    def _key(entry: entities.ManifestEntry) -> tuple[int, int]:
        base = (entry.url or "").split("#", 1)[0]
        return (url_pos.get(base, sentinel), entry.idx)

    return sorted(entries, key = _key)


def manifest_payload(
    framework_slug: str,
    entries: list[entities.ManifestEntry],
    *,
    ingested_at: float,
    normalizer_version: int,
    extra: dict | None = None,
) -> dict:
    """The canonical manifest of a finished run. The pages were written through the normalizer at `normalizer_version`, which
    Synth's `ensure_framework_normalized` compares against; `extra` (framework name, tier, run id, …) is laid over the rest."""
    payload = {
        "framework_slug":     framework_slug,
        "ingested_at":        ingested_at,
        "page_count":         len(entries),
        "total_bytes":        sum(e.bytes for e in entries),
        "entries":            [asdict(e) for e in entries],
        "normalizer_version": normalizer_version,
    }
    if extra:
        payload.update(extra)
    return payload


def live_manifest_json(entries: list[entities.ManifestEntry]) -> str:
    """The in-flight run's manifest as Redis holds it."""
    return json.dumps([asdict(e) for e in entries])


def entries_from_manifest(manifest: dict | None) -> list[entities.ManifestEntry]:
    """The entries of a stored manifest, ignoring any field this version of `ManifestEntry` does not have."""
    valid = {f.name for f in fields(entities.ManifestEntry)}
    return [entities.ManifestEntry(**{k: v for k, v in e.items() if k in valid}) for e in (manifest or {}).get("entries", [])]


def parse_json(raw: str | bytes | None, default):
    """What a stored value says, or `default` when there is none or it cannot be read."""
    if not raw:
        return default
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except ValueError:  # malformed JSON and undecodable bytes are both ValueErrors
        return default


def entry_with_idx(entries: list[dict], idx: int) -> dict | None:
    """The manifest entry whose `idx` is `idx` — not the `idx`-th one in the list: tiers 2-4 store the manifest in the
    site's navigation order (`Store.reorder_by_url_list`), while `idx` is the order the pages were written in, so the two
    differ and a lookup by position opened a different page than the one clicked (React's "Learn › Installation" opened
    the tic-tac-toe tutorial). A manifest whose entries carry no `idx` at all is read by position."""
    for entry in entries:
        if entry.get("idx") == idx:
            return entry
    if entries and not any("idx" in entry for entry in entries) and 0 <= idx < len(entries):
        return entries[idx]
    return None


def entry_key(framework_slug: str, entry: dict, idx: int) -> str:
    """Where an entry's page is stored: the key it carries, or the one its index and slug make."""
    return entry.get("key") or keys.page_key(framework_slug, idx, entry.get("slug", ""))


def library_row(slug: str, manifest: dict, catalog_entry: dict) -> dict:
    """One framework of the library sidebar: what its finalized manifest says, with the catalog's name and logos where the manifest
    has none."""
    return {
        "slug": slug,
        "framework_name": manifest.get("framework_name") or catalog_entry.get("name") or slug,
        "logo": catalog_entry.get("logo"),
        "logos": catalog_entry.get("logos") or [],
        "ingested_at": manifest.get("ingested_at"),
        "page_count": manifest.get("page_count") or 0,
        "total_bytes": manifest.get("total_bytes") or 0,
        "tier_kind": manifest.get("tier_kind"),
        "tier_url": manifest.get("tier_url"),
        "run_id": manifest.get("run_id"),
    }


def newest_ingested_first(rows: list[dict]) -> list[dict]:
    """The library rows, the most recently ingested framework first."""
    return sorted(rows, key = lambda row: row.get("ingested_at") or 0, reverse = True)


# ---- titles ---------------------------------------------------------------------------------------------------------------
def titled_by_slug(entry: dict) -> bool:
    return (entry.get("title") or "") in ("", entry.get("slug") or "")


def title_cache_key(framework_slug: str, manifest: dict) -> tuple[str, str]:
    """What a manifest's worked-out titles are kept under: the framework and the run that wrote it."""
    return (framework_slug, str(manifest.get("run_id") or manifest.get("ingested_at") or ""))


def with_titles(manifest: dict, titles: dict[int, str]) -> dict:
    """The manifest with `titles` ({idx: title}) on the pages that were only named by their slug; a page without one keeps what
    it had."""
    return {
        **manifest,
        "entries": [
            {**e, "title": titles.get(int(e.get("idx", 0)), e.get("title", ""))} if titled_by_slug(e) else e
            for e in manifest.get("entries") or []
        ],
    }


def with_unique_titles(manifest: dict) -> dict:
    """The manifest, with titles that repeat told apart by the docs section of their page (post.domain.disambiguate_titles).
    Pure and cheap, so it is applied on every read — a library ingested earlier gets it without being re-ingested."""
    entries = manifest.get("entries") or []
    plain = domains.dd.ingestion.post.domain.strip_site_suffix([e.get("title") or "" for e in entries])
    titles = domains.dd.ingestion.post.domain.disambiguate_titles(
        [(title, e.get("source_path") or e.get("url") or "") for title, e in zip(plain, entries)],
    )
    if all(t == (e.get("title") or "") for t, e in zip(titles, entries)):
        return manifest
    return {**manifest, "entries": [{**e, "title": t} for t, e in zip(titles, entries)]}


# ---- snapshots ------------------------------------------------------------------------------------------------------------
def snapshot_id(stamp: str, label: str | None) -> str:
    """A snapshot's id: the time it was taken at, and its label if it has one."""
    return stamp + (f"-{label}" if label else "")


def outside_snapshots(object_keys: list[str]) -> list[str]:
    """The keys that are not themselves in the snapshots subtree."""
    return [k for k in object_keys if params.SNAPSHOTS_SKIP_SUBSTRING not in k]


def snapshot_taken(framework_slug: str, ts: str, files_copied: int, took_ms: int) -> dict:
    return {"framework_slug": framework_slug, "ts": ts, "files_copied": files_copied, "took_ms": took_ms}


def snapshot_restored(framework_slug: str, ts: str, deleted: int, copied: int, took_ms: int) -> dict:
    return {"framework_slug": framework_slug, "ts": ts, "deleted": deleted, "copied": copied, "took_ms": took_ms}


def snapshot_deleted(framework_slug: str, ts: str, deleted: int) -> dict:
    return {"framework_slug": framework_slug, "ts": ts, "deleted": deleted}


def newest_first(names: list[str]) -> list[str]:
    """Snapshot or backup ids, newest first (they are timestamps, so alphabetical is chronological)."""
    return sorted(names, reverse = True)
