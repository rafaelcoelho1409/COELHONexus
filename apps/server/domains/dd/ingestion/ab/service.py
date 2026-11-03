"""A/B — I/O: read a capture as the inputs every arm is handed, run an arm on it, keep each arm's pages and scorecard on disk to compare."""
from __future__ import annotations
import domains, infra
from . import domain, entities, params

import asyncio
import importlib
import json
import time
from dataclasses import asdict
from pathlib import Path


async def prepare(minio: infra.minio.service.MinIOStorage, framework_slug: str) -> entities.Inputs:
    """The inputs of a framework's A/B, from its capture. The bundle is cut to the catalog's `path_filter` here, once, for every arm: which
    pages are in scope is the catalog's decision, not a thing an arm formats or fails at."""
    ingestion = domains.dd.ingestion
    capture = await ingestion.capture.service.load(minio, framework_slug)
    if capture is None:
        raise LookupError(f"{framework_slug}: no capture under ingestion/raw/ (capture it first)")
    if capture.tier != ingestion.tiers.tier1.params.TIER_KIND:
        raise NotImplementedError(f"{framework_slug}: the A/B is built for {ingestion.tiers.tier1.params.TIER_KIND} captures so far, not {capture.tier}")
    page = ingestion.capture.domain.file_named(capture, ingestion.tiers.tier1.params.BUNDLE_FILE)
    if page is None:
        raise LookupError(f"{framework_slug}: the capture has no {ingestion.tiers.tier1.params.BUNDLE_FILE}")
    # The bundle, or the parts of a paginated one in order, as one text (`tier1.domain.join_parts`, the same join a run makes).
    parts = [await ingestion.capture.service.read_text(minio, capture, f.name) for f in ingestion.capture.domain.files_with_role(capture, ingestion.capture.params.ROLE_PAGE)]
    raw = ingestion.tiers.tier1.domain.join_parts(parts)
    nav_text = await ingestion.capture.service.read_text(minio, capture, ingestion.tiers.tier1.params.NAV_FILE)
    text, _kept, _total = ingestion.tiers.tier1.domain.apply_path_filter(raw, capture.params.get("path_filter"))
    return entities.Inputs(
        framework_slug = framework_slug, tier = capture.tier, url = capture.source_url, text = text, nav_text = nav_text,
        nav_links = ingestion.tiers.tier1.domain.nav_links(nav_text, capture.params.get("nav_url") or ""),
        params = capture.params, capture_sha256 = ingestion.capture.domain.sha256_text(raw),
    )


def arm_module(name: str):
    """The module of the arm called `name` (`params.ARM_MODULES`): `async def transform(inputs) -> list[Page]`."""
    if name not in params.ARM_MODULES:
        raise KeyError(f"no arm called {name!r}; known: {', '.join(sorted(params.ARM_MODULES))}")
    return importlib.import_module(params.ARM_MODULES[name])


async def run_arm(name: str, inputs: entities.Inputs) -> tuple[list, float]:
    """The pages the arm makes of `inputs`, and the seconds it took."""
    started = time.monotonic()
    pages = await arm_module(name).transform(inputs)
    return pages, time.monotonic() - started


async def score(inputs: entities.Inputs, pages: list, *, reference: entities.Analysis | None = None) -> dict:
    """The scorecard of `pages` (reading ten megabytes of Markdown takes seconds: off the event loop)."""
    return await asyncio.to_thread(domain.scorecard, inputs, pages, reference = reference)


# ---- what an arm made, on disk -------------------------------------------------------------------------------------------------
def _arm_dir(out_dir: Path, framework_slug: str, arm: str) -> Path:
    return out_dir / framework_slug / arm


def write_result(out_dir: Path, framework_slug: str, arm: str, pages: list, card: dict, seconds: float) -> Path:
    """`{out}/{slug}/{arm}/`: `pages/NNNN.md`, `pages.json` (everything but the bodies), `card.json`."""
    target = _arm_dir(out_dir, framework_slug, arm)
    (target / "pages").mkdir(parents = True, exist_ok = True)
    for old in (target / "pages").glob("*.md"):
        old.unlink()
    for idx, page in enumerate(pages):
        (target / "pages" / f"{idx:04d}.md").write_text(page.body, encoding = "utf-8")
    (target / "pages.json").write_text(json.dumps([{**asdict(p), "body": None} for p in pages], ensure_ascii = False, indent = 1))
    (target / "card.json").write_text(json.dumps({**card, "seconds": round(seconds, 1)}, ensure_ascii = False, indent = 1))
    return target


def read_card(out_dir: Path, framework_slug: str, arm: str) -> dict | None:
    path = _arm_dir(out_dir, framework_slug, arm) / "card.json"
    return json.loads(path.read_text()) if path.exists() else None


def read_pages(out_dir: Path, framework_slug: str, arm: str) -> list:
    """The pages an arm made, read back."""
    target = _arm_dir(out_dir, framework_slug, arm)
    meta = json.loads((target / "pages.json").read_text())
    entity = domains.dd.ingestion.transform.entities.Page
    return [entity(**{**m, "body": (target / "pages" / f"{i:04d}.md").read_text(encoding = "utf-8")}) for i, m in enumerate(meta)]
