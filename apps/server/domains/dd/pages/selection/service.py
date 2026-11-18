"""Select extracted pages while preserving the source Markdown and manifest identities."""
from __future__ import annotations
import domains

import asyncio
import json
import logging
import time
from typing import Any


logger = logging.getLogger(__name__)

STREAM_START_PAGES = 16
STREAM_BATCH_SIZE = 16
SELECTION_STATUSES = frozenset({"selected", "excluded", "needs_review"})


def _page_excerpt(body: str, max_chars: int) -> str:
    """Bound large prompts while retaining both the opening explanation and closing examples."""
    if len(body) <= max_chars:
        return body
    marker = "\n\n[…]\n\n"
    budget = max(0, max_chars - len(marker))
    head = budget // 2
    tail = budget - head
    return f"{body[:head]}{marker}{body[-tail:]}" if tail else f"{body[:budget]}{marker}"


def _stream_safe(entry) -> bool:
    """Only classify pages that tier post-processing cannot split or replace."""
    return entry.bytes < domains.dd.pages.post.params.OVERSIZED_SPLIT_BYTES


def _selection_counts(entries: list) -> tuple[int, int, int, int]:
    selected = sum(entry.page_selection.get("status") == "selected" for entry in entries)
    excluded = sum(entry.page_selection.get("status") == "excluded" for entry in entries)
    review = sum(entry.page_selection.get("status") == "needs_review" for entry in entries)
    return selected, excluded, review, selected + excluded + review


async def select_pages(
    store,
    progress,
    *,
    framework_name: str = "",
    entries: list | None = None,
    known_digests: dict[str, str] | None = None,
    parallel: bool = False,
    complete_stage: bool = True,
    progress_total: int | None = None,
) -> dict[str, int | str]:
    """Classify a set of final or extraction-stable page units and persist results by durable page identity.

    A model failure creates a ``needs_review`` outcome. Such a page is kept in storage for recovery, but is not shown in Pages
    and cannot enter Planner until an AI assessment approves it.
    """
    import ai

    page_cards = ai.dd.nodes.pipeline.planner.page_cards
    slug = store.framework_slug
    manifest_entries = store.manifest
    candidates = entries if entries is not None else [
        entry for entry in manifest_entries
        if entry.page_selection.get("status") not in SELECTION_STATUSES
    ]
    total = max(len(manifest_entries), progress_total or 0)
    if not parallel:
        await progress.start(tier="page_selection", total=total)

    if candidates:
        source_keys = [entry.key for entry in candidates]
        try:
            bodies = await domains.dd.pipeline.storage.service.read_all(store.minio, source_keys)
        except Exception as error:
            logger.warning(
                "[page_selection] %s: bulk read failed (%s); individual fallback will run",
                slug,
                type(error).__name__,
            )
            bodies = []
            for key in source_keys:
                try:
                    bodies.append(await store.minio.read_text(key))
                except Exception:
                    bodies.append(None)
        if len(bodies) != len(candidates):
            bodies = [None] * len(candidates)

        body_by_key: dict[str, str | None] = {
            entry.key: body for entry, body in zip(candidates, bodies)
        }
        entry_by_key = {entry.key: entry for entry in candidates}
        digest_by_key: dict[str, str] = {}
        keys_by_digest: dict[str, list[str]] = {}
        for entry in candidates:
            body = body_by_key.get(entry.key)
            digest = (
                page_cards.domain.content_digest(body)
                if body is not None
                else page_cards.domain.content_digest(f"unreadable:{entry.key}")
            )
            digest_by_key[entry.key] = digest
            keys_by_digest.setdefault(digest, []).append(entry.key)

        known = known_digests if known_digests is not None else {}
        prior_duplicates = {
            digest: canonical
            for digest, canonical in known.items()
            if digest in keys_by_digest
        }
        digests = [digest for digest in keys_by_digest if digest not in prior_duplicates]
        if digests:
            try:
                cached = await domains.dd.pipeline.storage.service.read_items(
                    store.minio,
                    slug,
                    page_cards.params.STEP,
                    page_cards.versions.PROMPT_VERSION,
                    digests,
                )
            except Exception as error:
                logger.warning("[page_selection] %s: cached cards unavailable (%s)", slug, type(error).__name__)
                cached = {}
        else:
            cached = {}

        cards_by_digest: dict[str, dict[str, Any]] = {
            digest: value["card"]
            for digest, value in cached.items()
            if isinstance(value, dict) and isinstance(value.get("card"), dict)
        }
        todo = [digest for digest in digests if digest not in cards_by_digest]
        semaphore = asyncio.Semaphore(page_cards.params.CONCURRENCY)
        role = None
        if any((body_by_key.get(keys_by_digest[digest][0]) or "").strip() for digest in todo):
            try:
                role = ai.dd.agents.pages.page_selector.agent.page_selector()
            except Exception as error:
                logger.warning("[page_selection] %s: selector setup failed (%s)", slug, type(error).__name__)

        selection_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}

        def record(entry, selection: dict[str, Any]) -> None:
            identity = domains.dd.pages.storage.domain.selection_identity(entry)
            selection_by_identity[identity] = selection

        for digest, canonical in prior_duplicates.items():
            for key in keys_by_digest[digest]:
                entry = entry_by_key[key]
                record(entry, {
                    "status": "excluded",
                    "reason": f"Exact duplicate of {canonical}.",
                    "needs_review": False,
                    "prompt_version": page_cards.versions.PROMPT_VERSION,
                    "content_digest": digest_by_key[key],
                    "duplicate_of": canonical,
                })

        async def classify(digest: str) -> None:
            keys = keys_by_digest[digest]
            canonical = keys[0]
            entry = entry_by_key[canonical]
            body = body_by_key.get(canonical)
            title = entry.title or entry.slug
            url = entry.source_path or entry.url
            fallback = False

            if digest in cards_by_digest:
                card = cards_by_digest[digest]
                fallback = bool(card.get("fallback"))
            elif body is None:
                card = page_cards.domain.fallback_card(key=canonical, title=title, body="", url=url)
                fallback = True
            elif not body.strip():
                card = page_cards.domain.fallback_card(key=canonical, title=title, body=body, url=url)
            elif role is None:
                card = page_cards.domain.fallback_card(key=canonical, title=title, body=body, url=url)
                fallback = True
            else:
                request = json.dumps({
                    "task": "Select this extracted page for the study learning set and create its semantic card.",
                    "framework": framework_name or slug,
                    "title": title,
                    "url": url,
                    "page": _page_excerpt(body, page_cards.params.BODY_CHARS),
                }, ensure_ascii=False)
                meta: dict[str, Any] = {}
                try:
                    async with semaphore:
                        answer = await ai.dd.agents.service.ask(
                            role,
                            ai.dd.agents.pages.page_selector.schemas.PageCard,
                            request,
                            attempts=page_cards.params.ATTEMPTS,
                            wait_base_s=page_cards.params.RETRY_WAIT_S,
                            meta=meta,
                        )
                    card = answer.model_dump()
                    card = page_cards.domain.normalize_reference_kind(card, key=canonical, title=title, url=url)
                    try:
                        await domains.dd.pipeline.storage.service.write_item(
                            store.minio,
                            slug,
                            page_cards.params.STEP,
                            page_cards.versions.PROMPT_VERSION,
                            digest,
                            {"card": card, "deployment": meta},
                        )
                    except Exception as error:
                        logger.warning("[page_selection] %s: card cache write failed (%s)", slug, type(error).__name__)
                except Exception as error:
                    card = page_cards.domain.fallback_card(key=canonical, title=title, body=body, url=url)
                    fallback = True
                    logger.warning(
                        "[page_selection] %s/%s: awaiting review after selector failure (%s)",
                        slug,
                        canonical,
                        type(error).__name__,
                    )

            card = page_cards.domain.normalize_reference_kind(card, key=canonical, title=title, url=url)
            if fallback:
                decision = "needs_review"
            else:
                decision = "selected" if card.get("keep", True) else "excluded"
            for position, key in enumerate(keys):
                sibling = entry_by_key[key]
                sibling_decision = decision
                sibling_card = card
                duplicate_of = None
                if position > 0:
                    duplicate_of = canonical
                    sibling_decision = "excluded"
                    sibling_card = {**card, "keep": False, "reason": f"Exact duplicate of {canonical}."}
                record(sibling, {
                    "status": sibling_decision,
                    "reason": (
                        "AI assessment is unavailable; this page is held for review."
                        if fallback and duplicate_of is None
                        else sibling_card.get("reason") or "No selection reason was returned."
                    ),
                    "needs_review": bool(fallback and duplicate_of is None),
                    "prompt_version": page_cards.versions.PROMPT_VERSION,
                    "content_digest": digest_by_key[key],
                    "duplicate_of": duplicate_of,
                })
        await asyncio.gather(*(classify(digest) for digest in digests))
        await store.record_page_selections(selection_by_identity, force=True)
        for digest, keys in keys_by_digest.items():
            known.setdefault(digest, keys[0])

    current_entries = store.manifest
    selected_count, excluded_count, review_count, processed_count = _selection_counts(current_entries)
    if parallel:
        await progress.update_parallel_stage(
            "selection",
            current=processed_count,
            total=max(total, len(current_entries)),
        )
    else:
        await progress.update(current=processed_count)

    if complete_stage:
        await progress.complete_stage(
            "selection",
            current=processed_count,
            total=max(total, len(current_entries)),
        )
    logger.info(
        "[page_selection] %s: %s selected, %s excluded, %s awaiting review out of %s",
        slug,
        selected_count,
        excluded_count,
        review_count,
        len(current_entries),
    )
    return {
        "selected": selected_count,
        "excluded": excluded_count,
        "needs_review": review_count,
        "pages": len(current_entries),
        "processed": processed_count,
    }


class PageSelectionStream:
    """Select stable extracted pages in batches while the tier continues writing later pages."""

    def __init__(self, store, progress, *, framework_name: str = "") -> None:
        self.store = store
        self.progress = progress
        self.framework_name = framework_name
        self.known_digests: dict[str, str] = {}
        self.started = False
        self.processing_duration_s = 0.0
        self.seen = 0
        self._processed = 0
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    def submit(self, entry) -> None:
        """Non-blocking hook called after an extracted page is durably stored."""
        self.seen += 1
        self._queue.put_nowait(entry)

    async def _process(self, entries: list) -> None:
        if not entries:
            return
        started = time.monotonic()
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns="dd",
                run_id=self.progress.run_id,
                node_id="page_selection",
                stage="pages",
            ):
                summary = await select_pages(
                    self.store,
                    self.progress,
                    framework_name=self.framework_name,
                    entries=entries,
                    known_digests=self.known_digests,
                    parallel=True,
                    complete_stage=False,
                    progress_total=self.seen,
                )
            self._processed = int(summary.get("processed") or 0)
        except Exception as error:
            # A streaming failure must not fail extraction; the final post-split pass retries every unassessed page.
            logger.exception("[page_selection] %s: streaming batch deferred to final pass (%s)", self.store.framework_slug, type(error).__name__)
        finally:
            self.processing_duration_s += max(0.0, time.monotonic() - started)

    async def _run(self) -> None:
        pending: list = []
        batch: list = []
        while True:
            entry = await self._queue.get()
            if entry is None:
                break
            if not self.started:
                pending.append(entry)
                if len(pending) < STREAM_START_PAGES:
                    continue
                self.started = True
                await self.progress.start_parallel_stage("selection", total=self.seen)
                batch.extend(item for item in pending if _stream_safe(item))
                pending.clear()
                if batch:
                    ready, batch = batch, []
                    await self._process(ready)
            elif _stream_safe(entry):
                batch.append(entry)

            while len(batch) >= STREAM_BATCH_SIZE:
                ready, batch = batch[:STREAM_BATCH_SIZE], batch[STREAM_BATCH_SIZE:]
                await self._process(ready)

        if self.started:
            while batch:
                ready, batch = batch[:STREAM_BATCH_SIZE], batch[STREAM_BATCH_SIZE:]
                await self._process(ready)
            await self.progress.update_parallel_stage(
                "selection",
                current=self._processed,
                total=max(self.seen, len(self.store.manifest)),
            )

    async def finish(self) -> None:
        """Drain all extraction-time batches before post-processing replaces page units."""
        self._queue.put_nowait(None)
        await self._task

    async def cancel(self) -> None:
        """Stop any outstanding model work when extraction itself is cancelled or fails."""
        if self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.warning("[page_selection] streaming task stopped (%s)", type(error).__name__)
