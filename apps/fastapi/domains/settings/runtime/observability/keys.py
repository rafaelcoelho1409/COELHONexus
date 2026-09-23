"""OTel GenAI semconv attribute/span names for the chat + embeddings
endpoint adapters — https://opentelemetry.io/docs/specs/semconv/gen-ai/

Mirrors the same attribute vocabulary already designed (dormant) on the
COELHOLLMRotator side (`domains/llm/rotator/observability/keys.py`) so a
trace that spans both services — Nexus's outbound call, then whatever the
Rotator eventually emits — reads consistently under the same names, even
though today only this Nexus-side span is live. `gen_ai.system` differs
(this identifies the CALLER, the Rotator's would identify itself), values
otherwise line up 1:1 with the semconv.
"""
from __future__ import annotations

GEN_AI_SYSTEM              = "gen_ai.system"
GEN_AI_OPERATION_NAME      = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL       = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS  = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL      = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS  = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Custom (not yet in gen_ai semconv), matches the Rotator's naming.
GEN_AI_REQUEST_INPUT_COUNT        = "gen_ai.request.input_count"
GEN_AI_RESPONSE_EMBEDDING_VECTORS = "gen_ai.response.embedding.vectors"

SPAN_NAME_CHAT  = "gen_ai.chat"
SPAN_NAME_EMBED = "gen_ai.embed"

OP_CHAT      = "chat"
OP_EMBEDDING = "embedding"

SYSTEM_NEXUS_CHAT_ENDPOINT      = "coelhonexus-chat-endpoint"
SYSTEM_NEXUS_EMBEDDING_ENDPOINT = "coelhonexus-embedding-endpoint"

# LangFuse observation typing — an explicit type always wins over ingest
# inference, so chat spans classify as `generation` (cost + Metrics tab)
# even when conventions shift under us.
LANGFUSE_OBSERVATION_TYPE   = "langfuse.observation.type"
OBSERVATION_TYPE_GENERATION = "generation"
OBSERVATION_TYPE_EMBEDDING  = "embedding"
