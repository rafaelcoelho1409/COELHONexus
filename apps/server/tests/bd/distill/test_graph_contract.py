from __future__ import annotations

import unittest

import ai, domains


class DistillGraphContractTests(unittest.TestCase):
    def test_graph_compiles_with_typed_context_and_declared_node_order(self) -> None:
        graph = ai.bd.graphs.distill.book_distillation.service.distill_graph()

        self.assertEqual(
            tuple(node for node in domains.bd.distill.ai.params.LANGGRAPH_NODES if node in graph.get_graph().nodes),
            domains.bd.distill.ai.params.LANGGRAPH_NODES,
        )
        self.assertIn("unit_extraction", graph.get_graph().nodes)
        self.assertIn("chapter_guides", graph.get_graph().nodes)
        self.assertIn("book_retrieval", graph.get_graph().nodes)
        self.assertIn("dispatch_parallel_stages", graph.get_graph().nodes)
        self.assertNotIn("report_pipeline", graph.get_graph().nodes)
        for node in (
            "synthesize_book",
            "research_context",
            "write_decision_brief",
        ):
            self.assertIn(node, graph.get_graph().nodes)
        self.assertIn("publish_report", graph.get_graph().nodes)


if __name__ == "__main__":
    unittest.main()
