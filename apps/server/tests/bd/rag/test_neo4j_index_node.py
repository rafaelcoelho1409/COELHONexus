from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import domains
from ai.bd.nodes.ingestion.rag_indexing.main.neo4j import service


class Neo4jIndexNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_extractions_run_with_at_most_two_concurrent_calls(self) -> None:
        active_calls = 0
        max_active_calls = 0
        two_calls_started = asyncio.Event()

        async def extract(_extractor, _rows, *, section_title: str) -> dict:
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            if active_calls == 2:
                two_calls_started.set()
            try:
                await asyncio.wait_for(two_calls_started.wait(), timeout = 1)
                await asyncio.sleep(0)
                return {"entities": [], "relationships": []}
            finally:
                active_calls -= 1

        result, _, _ = await self._run_index(extract)

        self.assertEqual(max_active_calls, 2)
        self.assertEqual(result["neo4j_stage"], "complete")

    async def test_extraction_failure_cancels_sibling_calls_before_graph_writes(self) -> None:
        two_calls_started = asyncio.Event()
        sibling_cancelled = asyncio.Event()
        active_calls = 0

        async def extract(_extractor, _rows, *, section_title: str) -> dict:
            nonlocal active_calls
            active_calls += 1
            if active_calls == 2:
                two_calls_started.set()
            try:
                await two_calls_started.wait()
                if section_title == "fail":
                    raise RuntimeError("mock extraction failure")
                await asyncio.Future()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
            finally:
                active_calls -= 1

        _, delete_graph, write_graph = await self._run_index(
            extract,
            section_titles = ["fail", "wait", "queued"],
            expect_error = True,
        )

        self.assertTrue(sibling_cancelled.is_set())
        delete_graph.assert_not_awaited()
        write_graph.assert_not_awaited()

    async def _run_index(
        self,
        extract,
        *,
        section_titles: list[str] | None = None,
        expect_error: bool = False,
    ):
        rows = [
            {
                "processed_page_number": page_number,
                "source_page_number": page_number + 10,
                "content": f"Content for page {page_number}",
            }
            for page_number in range(1, 4)
        ]
        titles = section_titles or ["section"] * len(rows)
        batches = [
            (title, [row])
            for title, row in zip(titles, rows, strict = True)
        ]
        progress_callback = AsyncMock()
        context = SimpleNamespace(
            storage=object(),
            graph_extractor=object(),
            progress_callback=progress_callback,
            cancel_check=AsyncMock(return_value = False),
        )
        state = {
            "book_id": "book-1",
            "agentic_run_id": "run-1",
            "processed_book_revision": 1,
        }
        storage = domains.bd.rag.source.storage
        neo4j_index = domains.bd.rag.source.indexes.neo4j_index
        delete_graph = AsyncMock()
        write_graph = AsyncMock(return_value = (0, 0))

        with (
            patch.object(storage.service, "read_sources", new=AsyncMock(return_value = rows)),
            patch.object(neo4j_index.domain, "graph_batches", return_value = batches),
            patch.object(neo4j_index.domain, "build_graph", return_value = ([], [])),
            patch.object(neo4j_index.service, "delete_graph", new=delete_graph),
            patch.object(neo4j_index.service, "write_graph", new=write_graph),
            patch.object(service, "extract_graph_facts", new=extract),
        ):
            if expect_error:
                with self.assertRaisesRegex(RuntimeError, "mock extraction failure"):
                    await service.index_book_graph(state, context)
                result = None
            else:
                result = await service.index_book_graph(state, context)

        return result, delete_graph, write_graph


if __name__ == "__main__":
    unittest.main()
