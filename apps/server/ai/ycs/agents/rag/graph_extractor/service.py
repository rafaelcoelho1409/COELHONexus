"""YCS-owned Docling Graph adapter backed by the configured LangChain model."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

import json_repair
from docling_graph import PipelineConfig, run_pipeline
from docling_graph.llm_clients.schema_utils import normalize_schema_for_response_format
from langchain_core.messages import HumanMessage, SystemMessage

from . import entities, errors, schemas


logger = logging.getLogger(__name__)


class ChatModelJSONClient:
    """Implement Docling Graph's JSON client protocol over ChatOpenAI."""

    def __init__(
        self,
        model: Any,
        *,
        instructions: str = "",
    ) -> None:
        self.model = model
        self.instructions = instructions.strip()
        self.output_limit_hit = False
        self.last_model_error: Exception | None = None

    def get_json_response(
        self,
        prompt: str | Mapping[str, str],
        schema_json: str,
        structured_output: bool = True,
        response_top_level: Literal["object", "array"] = "object",
        response_schema_name: str = "extraction_result",
    ) -> dict[str, Any] | list[Any]:
        if isinstance(prompt, Mapping):
            system = str(prompt.get("system") or "").strip()
            user = str(prompt.get("user") or "").strip()
        else:
            system = ""
            user = str(prompt)

        if self.instructions:
            system = f"{system}\n\n{self.instructions}".strip()
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=user))

        request = self.model
        if structured_output:
            schema = _openai_schema(json.loads(schema_json))
            response_schema = normalize_schema_for_response_format(
                schema,
                top_level=response_top_level,
                name=response_schema_name,
            )
            request = request.bind(
                response_format={
                    "type": "json_schema",
                    "json_schema": response_schema,
                },
            )

        try:
            self.output_limit_hit = False
            self.last_model_error = None
            response = request.invoke(messages)
        except Exception as error:
            self.last_model_error = error
            error_description = f"{type(error).__name__}: {error}"
            if "LengthFinishReasonError" in error_description or "length limit was reached" in error_description:
                self.output_limit_hit = True
            raise
        content = getattr(response, "content", response)
        raw = _message_text(content)
        parsed = json_repair.loads(raw)
        if response_top_level == "object" and not isinstance(parsed, dict):
            raise ValueError("Docling Graph expected a JSON object from the configured chat model")
        if response_top_level == "array" and not isinstance(parsed, list):
            raise ValueError("Docling Graph expected a JSON array from the configured chat model")
        return parsed

    def get_json_response_stream(
        self,
        prompt: str | Mapping[str, str],
        schema_json: str,
        structured_output: bool = True,
        response_top_level: Literal["object", "array"] = "object",
        response_schema_name: str = "extraction_result",
    ) -> Iterator[dict[str, Any] | list[Any]]:
        """Yield the complete result for Docling Graph's streaming protocol."""
        yield self.get_json_response(
            prompt,
            schema_json,
            structured_output=structured_output,
            response_top_level=response_top_level,
            response_schema_name=response_schema_name,
        )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content or "")


def _openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize graph-annotated Pydantic schemas for strict JSON output."""
    normalized = json.loads(json.dumps(schema))

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("title", "examples", "default", "edge_label", "graph_reference"):
                value.pop(key, None)
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(normalized)
    return normalized


def _project_models(models: list[Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    entities_by_key: dict[tuple[str, str], dict[str, str]] = {}
    relationships_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_entity(value: Any) -> None:
        name = str(getattr(value, "name", "") or "").strip()
        entity_type = str(getattr(value, "entity_type", "") or "Entity").strip()
        key = (name.casefold(), entity_type.casefold())
        if name and key not in entities_by_key:
            entities_by_key[key] = {"id": name, "type": entity_type or "Entity"}

    for extraction in models:
        for entity in getattr(extraction, "entities", []) or []:
            add_entity(entity)
        for fact in getattr(extraction, "facts", []) or []:
            source = getattr(fact, "source", None)
            target = getattr(fact, "target", None)
            add_entity(source)
            add_entity(target)
            source_name = str(getattr(source, "name", "") or "").strip()
            target_name = str(getattr(target, "name", "") or "").strip()
            relation_type = str(getattr(fact, "predicate", "") or "").strip()
            if not source_name or not target_name or not relation_type:
                continue
            key = (source_name.casefold(), relation_type.casefold(), target_name.casefold())
            relationship = {
                "source_id": source_name,
                "target_id": target_name,
                "type": relation_type,
                "properties": {"evidence": str(getattr(fact, "evidence", "") or "").strip()},
                "source_type": str(getattr(source, "entity_type", "Entity") or "Entity").strip(),
                "target_type": str(getattr(target, "entity_type", "Entity") or "Entity").strip(),
            }
            existing = relationships_by_key.get(key)
            if existing is None:
                relationships_by_key[key] = relationship
            else:
                previous = str(existing["properties"].get("evidence") or "")
                incoming = str(relationship["properties"].get("evidence") or "")
                existing["properties"]["evidence"] = previous or incoming

    return list(entities_by_key.values()), list(relationships_by_key.values())


def extract_text(
    text: str,
    model: Any,
    *,
    instructions: str = "",
    source_name: str = "source.md",
) -> entities.GraphExtraction:
    """Run the complete Docling Graph pipeline on an in-memory text document."""
    if not text.strip():
        return entities.GraphExtraction(entities=[], relationships=[], graph=None, provenance=None)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=Path(source_name).suffix or ".md",
        encoding="utf-8",
        delete=False,
    ) as source_file:
        source_file.write(text)
        source_path = Path(source_file.name)

    try:
        client = ChatModelJSONClient(
            model,
            instructions=instructions,
        )
        config = PipelineConfig(
            source=source_path,
            template=schemas.KnowledgeGraph,
            backend="llm",
            inference="remote",
            provider_override="openai",
            model_override=str(
                getattr(model, "model_name", None)
                or getattr(model, "model", None)
                or "configured-chat-model"
            ),
            processing_mode="many-to-one",
            # BD and YCS already bound each source before calling this adapter;
            # a second chunker would repeat that work and initialize its default
            # Hugging Face tokenizer for every worker process.
            extraction_contract="direct",
            llm_input_format="markdown",
            llm_client=client,
            structured_output=True,
            # Strict structured output stays the contract; retrying sparse
            # responses in legacy prompt mode adds calls but does not improve
            # evidence-grounded relations under this provider's output cap.
            structured_sparse_check=False,
            use_chunking=False,
            # The configured Rotator can cap completions near 1K tokens;
            # an optional second-pass gleaning response often hits that cap
            # after a valid direct extraction and only adds latency.
            gleaning_enabled=False,
            parallel_workers=1,
            provenance="detailed",
            dump_to_disk=False,
        )
        try:
            context = run_pipeline(config)
        except Exception as error:
            if client.output_limit_hit:
                raise errors.GraphOutputLimitError(
                    "Docling Graph response exceeded the provider completion-token limit",
                ) from error
            if client.last_model_error is not None:
                raise client.last_model_error from error
            raise
        models = context.extracted_models or []
        extracted_entities, relationships = _project_models(models)
        logger.info(
            "Docling Graph extracted %d entities and %d relationships from %s",
            len(extracted_entities),
            len(relationships),
            source_name,
        )
        return entities.GraphExtraction(
            entities=extracted_entities,
            relationships=relationships,
            graph=context.knowledge_graph,
            provenance=context.provenance,
        )
    finally:
        source_path.unlink(missing_ok=True)


async def aextract_text(
    text: str,
    model: Any,
    *,
    instructions: str = "",
    source_name: str = "source.md",
) -> entities.GraphExtraction:
    """Run Docling Graph outside the event loop's thread."""
    return await asyncio.to_thread(
        extract_text,
        text,
        model,
        instructions=instructions,
        source_name=source_name,
    )
