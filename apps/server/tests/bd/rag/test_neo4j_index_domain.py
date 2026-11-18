from __future__ import annotations

import unittest

import domains


class Neo4jIndexDomainTests(unittest.TestCase):
    def test_relationship_types_are_open_but_safe_for_cypher(self) -> None:
        normalize = domains.bd.rag.neo4j_index.domain.neo4j_relationship_type

        self.assertEqual(normalize("mentions"), "BD_REL_MENTIONS")
        self.assertEqual(normalize("supports / leads to"), "BD_REL_SUPPORTS_LEADS_TO")
        self.assertEqual(normalize("café influence"), "BD_REL_CAFE_INFLUENCE")
        self.assertEqual(normalize("contains_entity"), "BD_REL_CONTAINS_ENTITY")
        self.assertEqual(normalize("contains_entity", semantic = False), "BD_CONTAINS")
        self.assertEqual(normalize("!!!"), "BD_REL_RELATES")
        self.assertLessEqual(len(normalize("predicate " * 40)), 100)

    def test_supported_relationship_gets_addressable_page_evidence(self) -> None:
        domain = domains.bd.rag.neo4j_index.domain
        row = self._row("Ada designed the Analytical Engine.")

        nodes, edges = domain.build_graph(
            [row],
            [self._extraction(
                row,
                entities = [
                    {"id": "Ada", "type": "Person"},
                    {"id": "Analytical Engine", "type": "Machine"},
                ],
                relationships = [{
                    "source_id": "Ada",
                    "source_type": "Person",
                    "target_id": "Analytical Engine",
                    "target_type": "Machine",
                    "type": "DESIGNS",
                    "properties": {"evidence": "Ada designed the Analytical Engine."},
                }],
            )],
            book_id = "book-1",
            agentic_run_id = "run-1",
            revision = 1,
        )

        evidence_nodes = [node for node in nodes if node.kind == "relationship_evidence"]
        self.assertEqual(len(evidence_nodes), 1)
        self.assertEqual(evidence_nodes[0].properties["quote"], "Ada designed the Analytical Engine.")
        self.assertEqual(evidence_nodes[0].properties["processed_page_number"], 1)
        relation_edge = next(edge for edge in edges if edge.kind == "DESIGNS")
        self.assertTrue(relation_edge.semantic)
        self.assertEqual(len(relation_edge.properties["evidence_ids"]), 1)
        self.assertTrue(any(edge.kind == "has_evidence" for edge in edges))
        self.assertTrue(any(edge.kind == "evidence_target" for edge in edges))

    def test_relationship_without_sentence_local_source_support_is_dropped(self) -> None:
        domain = domains.bd.rag.neo4j_index.domain
        row = self._row("Ada Lovelace was a mathematician. The Analytical Engine was a machine.")

        nodes, edges = domain.build_graph(
            [row],
            [self._extraction(
                row,
                entities = [
                    {"id": "Ada Lovelace", "type": "Person"},
                    {"id": "Analytical Engine", "type": "Machine"},
                ],
                relationships = [{
                    "source_id": "Ada Lovelace",
                    "source_type": "Person",
                    "target_id": "Analytical Engine",
                    "target_type": "Machine",
                    "type": "DESIGNS",
                    "properties": {},
                }],
            )],
            book_id = "book-1",
            agentic_run_id = "run-1",
            revision = 1,
        )

        self.assertFalse(any(node.kind == "relationship_evidence" for node in nodes))
        self.assertFalse(any(edge.kind == "DESIGNS" for edge in edges))

    def test_same_name_with_different_types_remains_distinct_and_resolves_by_type(self) -> None:
        domain = domains.bd.rag.neo4j_index.domain
        row = self._row("Mercury orbits the Sun. Mercury is also a chemical element.")

        nodes, edges = domain.build_graph(
            [row],
            [self._extraction(
                row,
                entities = [
                    {"id": "Mercury", "type": "Planet"},
                    {"id": "Mercury", "type": "ChemicalElement"},
                    {"id": "Sun", "type": "Star"},
                ],
                relationships = [{
                    "source_id": "Mercury",
                    "source_type": "Planet",
                    "target_id": "Sun",
                    "target_type": "Star",
                    "type": "ORBITS",
                    "properties": {},
                }],
            )],
            book_id = "book-1",
            agentic_run_id = "run-1",
            revision = 1,
        )

        mercury_nodes = [node for node in nodes if node.kind == "entity" and node.name == "Mercury"]
        self.assertEqual(len(mercury_nodes), 2)
        planet_id = next(node.id for node in mercury_nodes if node.properties["entity_type"] == "Planet")
        orbit_edge = next(edge for edge in edges if edge.kind == "ORBITS")
        self.assertEqual(orbit_edge.source_id, planet_id)

    def test_ambiguous_name_without_endpoint_type_is_not_guessed(self) -> None:
        domain = domains.bd.rag.neo4j_index.domain
        row = self._row("Mercury orbits the Sun. Mercury is also a chemical element.")

        _, edges = domain.build_graph(
            [row],
            [self._extraction(
                row,
                entities = [
                    {"id": "Mercury", "type": "Planet"},
                    {"id": "Mercury", "type": "ChemicalElement"},
                    {"id": "Sun", "type": "Star"},
                ],
                relationships = [{
                    "source_id": "Mercury",
                    "target_id": "Sun",
                    "type": "ORBITS",
                    "properties": {},
                }],
            )],
            book_id = "book-1",
            agentic_run_id = "run-1",
            revision = 1,
        )

        self.assertFalse(any(edge.kind == "ORBITS" for edge in edges))

    @staticmethod
    def _row(content: str) -> dict:
        return {
            "processed_page_number": 1,
            "source_page_number": 4,
            "content_role": "body",
            "content_sha256": "a" * 64,
            "section_path": ["Chapter 1"],
            "content": content,
        }

    @staticmethod
    def _extraction(row: dict, *, entities: list[dict], relationships: list[dict]) -> dict:
        return {
            "section_title": "Chapter 1",
            "rows": [row],
            "extraction": {"entities": entities, "relationships": relationships},
        }


if __name__ == "__main__":
    unittest.main()
