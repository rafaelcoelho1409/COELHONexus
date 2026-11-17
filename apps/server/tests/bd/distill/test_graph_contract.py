from __future__ import annotations

import unittest

import ai, domains


class DistillGraphContractTests(unittest.TestCase):
    def test_graph_compiles_with_typed_context_and_declared_node_order(self) -> None:
        graph = ai.bd.graphs.distill.graphs.distill_graph()

        self.assertEqual(
            tuple(node for node in domains.bd.distill.params.GRAPH_NODES if node in graph.get_graph().nodes),
            domains.bd.distill.params.GRAPH_NODES,
        )
        self.assertIn("extract_unit", graph.get_graph().nodes)
        self.assertIn("publish_report", graph.get_graph().nodes)


if __name__ == "__main__":
    unittest.main()
