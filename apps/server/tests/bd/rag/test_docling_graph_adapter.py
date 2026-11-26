from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.bd.agents.ingestion.graph_extractor import errors, schemas, service


class DoclingGraphServiceTests(unittest.TestCase):
    def test_provider_schema_keeps_strict_json_and_drops_graph_metadata(self) -> None:
        schema = schemas.KnowledgeGraph.model_json_schema()
        normalized = service._openai_schema(schema)
        entity = normalized["$defs"]["Entity"]
        fact = normalized["$defs"]["Fact"]

        self.assertEqual(normalized["required"], list(normalized["properties"]))
        self.assertFalse(normalized["additionalProperties"])
        self.assertFalse(entity["additionalProperties"])
        self.assertNotIn("edge_label", fact["properties"]["source"])
        self.assertNotIn("graph_reference", fact["properties"]["source"])
        self.assertEqual(
            schemas.Fact.model_fields["source"].json_schema_extra,
            {"edge_label": "SOURCE", "graph_reference": True},
        )

    def test_chat_model_client_binds_normalized_json_schema(self) -> None:
        response_formats = []

        class BoundModel:
            def __init__(self, response_format: dict | None = None) -> None:
                self.response_format = response_format

            def bind(self, **kwargs):
                response_formats.append(kwargs["response_format"])
                return BoundModel(kwargs["response_format"])

            def invoke(self, _messages):
                return SimpleNamespace(content='{"ok": true}')

        client = service.ChatModelJSONClient(BoundModel())
        response = client.get_json_response(
            {"system": "system", "user": "user"},
            json.dumps(schemas.KnowledgeGraph.model_json_schema()),
        )

        self.assertEqual(response, {"ok": True})
        response_schema = response_formats[0]["json_schema"]["schema"]
        self.assertFalse(response_schema["additionalProperties"])
        self.assertNotIn("edge_label", json.dumps(response_schema))

    def test_projection_deduplicates_entities_and_preserves_fact_evidence(self) -> None:
        alice = SimpleNamespace(name="Alice", entity_type="Person")
        acme = SimpleNamespace(name="Acme", entity_type="Organization")
        fact = SimpleNamespace(
            source=alice,
            target=acme,
            predicate="FOUNDED",
            evidence="Alice founded Acme in 2020.",
        )
        model = SimpleNamespace(entities=[alice, acme], facts=[fact])

        entities, relationships = service._project_models([model])

        self.assertEqual(len(entities), 2)
        self.assertEqual(relationships[0]["source_id"], "Alice")
        self.assertEqual(relationships[0]["properties"]["evidence"], fact.evidence)

    def test_pipeline_uses_custom_client_and_cleans_temporary_source(self) -> None:
        extracted = SimpleNamespace(entities=[], facts=[])
        context = SimpleNamespace(
            extracted_models=[extracted],
            knowledge_graph=object(),
            provenance=object(),
        )
        model = SimpleNamespace(model_name="fgts-va")

        with patch.object(service, "run_pipeline", return_value=context) as run_pipeline:
            result = service.extract_text("sample text", model, source_name="sample.md")

        config = run_pipeline.call_args.args[0]
        self.assertEqual(Path(config.source).suffix, ".md")
        self.assertIsNotNone(config.llm_client)
        self.assertEqual(config.model_override, "fgts-va")
        self.assertEqual(config.extraction_contract, "direct")
        self.assertFalse(config.use_chunking)
        self.assertFalse(config.gleaning_enabled)
        self.assertFalse(config.structured_sparse_check)
        self.assertFalse(Path(config.source).exists())
        self.assertEqual(result.entities, [])
        self.assertEqual(result.relationships, [])
        self.assertIs(result.graph, context.knowledge_graph)

    def test_pipeline_exposes_provider_output_limit_for_caller_splitting(self) -> None:
        class LengthFinishReasonError(RuntimeError):
            pass

        class BoundModel:
            def bind(self, **_kwargs):
                return self

            def invoke(self, _messages):
                raise LengthFinishReasonError("Could not parse response content as the length limit was reached")

        model = SimpleNamespace(model_name="fgts-va", bind=lambda **_kwargs: BoundModel())

        def run_pipeline(config):
            config.llm_client.get_json_response(
                "extract",
                json.dumps({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
            )

        with patch.object(service, "run_pipeline", side_effect=run_pipeline):
            with self.assertRaises(errors.GraphOutputLimitError):
                service.extract_text("sample text", model)

    def test_pipeline_preserves_provider_timeout_for_existing_retry_logic(self) -> None:
        class ProviderTimeout(RuntimeError):
            status_code = 504

        class BoundModel:
            def bind(self, **_kwargs):
                return self

            def invoke(self, _messages):
                raise ProviderTimeout("Rotator returned HTTP 504")

        model = SimpleNamespace(model_name="fgts-va", bind=lambda **_kwargs: BoundModel())

        def run_pipeline(config):
            config.llm_client.get_json_response(
                "extract",
                json.dumps({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
            )

        with patch.object(service, "run_pipeline", side_effect=run_pipeline):
            with self.assertRaises(ProviderTimeout):
                service.extract_text("sample text", model)


if __name__ == "__main__":
    unittest.main()
