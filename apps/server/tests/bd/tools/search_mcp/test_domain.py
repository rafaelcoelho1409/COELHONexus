from __future__ import annotations

import unittest

from ai.bd.tools.search_mcp import domain


class MCPToolArguments:
    @staticmethod
    def model_json_schema() -> dict:
        return {
            "properties": {
                "search_queries": {"type": "array"},
                "objective": {"type": "string"},
                "session_id": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["search_queries", "objective"],
        }


class MCPTool:
    args_schema = MCPToolArguments


class SearchDomainTests(unittest.TestCase):
    def test_search_arguments_are_derived_from_the_discovered_tool_schema(self) -> None:
        arguments = domain.search_arguments(
            MCPTool(),
            "current topic",
            "distill-run",
        )

        self.assertEqual(arguments, {
            "search_queries": ["current topic"],
            "objective": "current topic",
            "session_id": "distill-run",
            "max_results": 4,
        })

    def test_only_https_search_results_are_retained(self) -> None:
        sources = domain.normalize_sources(
            {"results": [
                {
                    "title": "Trusted source",
                    "url": "https://example.com/report",
                    "snippet": "A short current summary.",
                    "published_date": "2026-11-01",
                },
                {
                    "title": "Insecure source",
                    "url": "http://example.com/report",
                    "snippet": "This must not be linked.",
                },
            ]},
            provider_id = "coelho_search",
            query = "current topic",
            accessed_at = 100.0,
            limit = 12,
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["title"], "Trusted source")
        self.assertEqual(sources[0]["published_at"], "2026-11-01")


if __name__ == "__main__":
    unittest.main()
