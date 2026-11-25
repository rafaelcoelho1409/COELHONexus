from __future__ import annotations

import unittest
from types import SimpleNamespace

from ai.bd.nodes.rag.neo4j.index import service


class BdDoclingGraphExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_length_error_splits_and_deduplicates_facts(self) -> None:
        excerpt = "Alice founded Acme in 2020."
        extraction = SimpleNamespace(
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
        )
        calls = 0

        async def extract(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("LengthFinishReasonError: response length limit")
            return extraction

        rows = [{
            "processed_page_number": 1,
            "source_page_number": 1,
            "section_path": ["History"],
            "content": excerpt + (" More source text." * 200),
        }]
        result = await service.extract_graph_facts(
            extract,
            rows,
            section_title="History",
        )

        self.assertGreater(calls, 1)
        self.assertEqual(len(result["entities"]), 2)
        self.assertEqual(len(result["relationships"]), 1)
        self.assertEqual(result["relationships"][0]["type"], "FOUNDED")


if __name__ == "__main__":
    unittest.main()
