"""The ledger of a chapter from storage: read the pages, take their code blocks, build the ledger."""
from __future__ import annotations
import domains
from . import domain, entities


async def load_pages(blobs, slug: str, sources: list[str], bodies: list[str] | None = None) -> list[tuple[str, dict]]:
    """`(page key, vault entries in document order)` for each source that has a body. A page ingestion already vaulted has its entries read from the
    vault; a raw page is vaulted here the way the digest does it. `bodies` are the pages' texts when the caller has read them already."""
    vault = domains.dd.ingestion.vault
    if bodies is None:
        bodies = list(await blobs.read_many(sources))
    pages: list[tuple[str, dict]] = []
    for key, body in zip(sources, bodies):
        if not body:
            continue
        found = list(dict.fromkeys(match.group("hash") for match in vault.patterns.SENTINEL_RE.finditer(body)))
        if found:
            rich, _, _ = await vault.service.load_rich(blobs, slug, [key])
            entries = {digest: rich[digest] for digest in found if digest in rich}
        else:
            try:
                _, entries = vault.domain.sentinelize_doc(body)
            except Exception:
                entries = {}
        pages.append((key, entries))
    return pages


async def build(blobs, slug: str, sources: list[str]) -> entities.Ledger:
    """The ledger of a chapter whose pages are `sources` (in reading order)."""
    return domain.build(await load_pages(blobs, slug, sources))


async def chapter_stats(blobs, slug: str, sources: list[str]) -> dict:
    """What the plan records about a chapter: the minutes its pages take to read, the best code it must teach, and the minutes the tutorial built from
    it takes."""
    bodies = list(await blobs.read_many(sources))
    built = domain.build(await load_pages(blobs, slug, sources, bodies))
    return {
        "reading_minutes": domain.pages_minutes([body for body in bodies if body]),
        "n_best_code": len(built.must_teach),
        "tutorial_minutes": domain.tutorial_minutes(built),
    }
