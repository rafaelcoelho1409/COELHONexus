from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.documents import Document

from ai.ycs.agents.rag.graph_extractor.entities import GraphExtraction
from domains.ycs.rag.ai.entities import GraphExtractorRuntime
from domains.ycs.rag.ai import service


class YcsDoclingGraphServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_typed_facts_and_preserves_exact_evidence(self) -> None:
        excerpt = "Alice founded Acme in 2020."
        document = Document(page_content=excerpt, metadata={"video_id": "video-1"})
        extraction = GraphExtraction(
            entities=[
                {"id": "Alice", "type": "Person"},
                {"id": "Acme", "type": "Organization"},
            ],
            relationships=[{
                "source_id": "Alice",
                "source_type": "Person",
                "target_id": "Acme",
                "target_type": "Organization",
                "type": "FOUNDED",
                "properties": {"evidence": excerpt},
            }],
            graph=None,
            provenance=None,
        )

        with patch.object(
            service.graph_extraction,
            "aextract_text",
            new=AsyncMock(return_value=extraction),
        ):
            result = await service.extract_graph_document(
                GraphExtractorRuntime(model=object()),
                document,
            )

        self.assertEqual(result.source, document)
        self.assertEqual(len(result.nodes), 2)
        self.assertEqual(result.nodes[0].properties["evidence"], excerpt)
        self.assertEqual(len(result.relationships), 1)
        self.assertEqual(result.relationships[0].type, "FOUNDED")
        self.assertEqual(result.relationships[0].properties["evidence"], excerpt)

    async def test_retries_relationships_without_verbatim_source_evidence(self) -> None:
        excerpt = "Alice founded Acme in 2020."
        document = Document(page_content=excerpt)

        def extraction(evidence: str) -> GraphExtraction:
            return GraphExtraction(
                entities=[
                    {"id": "Alice", "type": "Person"},
                    {"id": "Acme", "type": "Organization"},
                ],
                relationships=[{
                    "source_id": "Alice",
                    "source_type": "Person",
                    "target_id": "Acme",
                    "target_type": "Organization",
                    "type": "FOUNDED",
                    "properties": {"evidence": evidence},
                }],
                graph=None,
                provenance=None,
            )

        with patch.object(
            service.graph_extraction,
            "aextract_text",
            new=AsyncMock(side_effect=[
                extraction("Alice started the company."),
                extraction(excerpt),
            ]),
        ) as extract_text:
            result = await service.extract_graph_document(
                GraphExtractorRuntime(model=object()),
                document,
            )

        self.assertEqual(extract_text.await_count, 2)
        self.assertEqual(len(result.relationships), 1)
        self.assertEqual(result.relationships[0].properties["evidence"], excerpt)

    async def test_retry_failure_keeps_first_pass_facts_that_pass_validation(self) -> None:
        excerpt = "Alice founded Acme in 2020."
        document = Document(page_content=excerpt)
        extraction = GraphExtraction(
            entities=[
                {"id": "Alice", "type": "Person"},
                {"id": "Acme", "type": "Organization"},
                {"id": "Globex", "type": "Organization"},
            ],
            relationships=[
                {
                    "source_id": "Alice",
                    "source_type": "Person",
                    "target_id": "Acme",
                    "target_type": "Organization",
                    "type": "FOUNDED",
                    "properties": {"evidence": excerpt},
                },
                {
                    "source_id": "Alice",
                    "source_type": "Person",
                    "target_id": "Globex",
                    "target_type": "Organization",
                    "type": "ACQUIRED",
                    "properties": {"evidence": "Alice acquired Globex."},
                },
            ],
            graph=None,
            provenance=None,
        )

        with patch.object(
            service.graph_extraction,
            "aextract_text",
            new=AsyncMock(side_effect=[extraction, RuntimeError("response length limit")]),
        ):
            result = await service.extract_graph_document(
                GraphExtractorRuntime(model=object()),
                document,
            )

        self.assertEqual(len(result.nodes), 2)
        self.assertEqual(len(result.relationships), 1)
        self.assertEqual(result.relationships[0].type, "FOUNDED")


if __name__ == "__main__":
    unittest.main()
