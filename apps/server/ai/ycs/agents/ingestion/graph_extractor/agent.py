"""Factory for the evidence-grounded YCS transcript graph extractor."""
from __future__ import annotations

import domains

from ... import schemas, service
from .... import prompts
from . import params


def graph_extractor(model = None):
    """Build a tool-free DeepAgent that proposes entities and relations from a transcript."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = params.MODEL_MAX_OUTPUT_TOKENS,
            temperature = 0.0,
            max_retries = params.MODEL_MAX_RETRIES,
            rotator_task = params.ROTATOR_TASK,
        )
    return service.build(
        model = model,
        name = "ycs_graph_extractor",
        system_prompt = prompts.ingestion.graph_extractor.prompts.SYSTEM_PROMPT,
        response_format = schemas.GraphExtraction,
        model_call_limit = params.MAX_MODEL_CALLS,
    )
