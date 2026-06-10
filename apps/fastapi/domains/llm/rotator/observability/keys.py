from __future__ import annotations


# --------------------------------------------------------------------------- #
# OpenTelemetry GenAI semantic conventions
# https://opentelemetry.io/docs/specs/semconv/gen-ai/
#
# LangFuse v3's OTLP ingester maps these to its generation-observation model:
#   gen_ai.prompt           → input
#   gen_ai.completion       → output
#   gen_ai.request.model    → model
#   gen_ai.usage.*          → usage
#   gen_ai.request.temperature / .top_p / .max_tokens → modelParameters
# Everything else falls into metadata.
# --------------------------------------------------------------------------- #
GEN_AI_SYSTEM                  = "gen_ai.system"
GEN_AI_OPERATION_NAME          = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL           = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE     = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P           = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS      = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL          = "gen_ai.response.model"
GEN_AI_RESPONSE_ID             = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS      = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS     = "gen_ai.usage.output_tokens"
GEN_AI_PROMPT                  = "gen_ai.prompt"
GEN_AI_COMPLETION              = "gen_ai.completion"

# Custom — not in the gen_ai semconv yet but LangFuse + Tempo accept
# arbitrary attributes and surface them under metadata.
GEN_AI_REQUEST_INPUT_COUNT        = "gen_ai.request.input_count"
GEN_AI_REQUEST_INPUT_TYPE         = "gen_ai.request.input_type"
GEN_AI_RESPONSE_EMBEDDING_VECTORS = "gen_ai.response.embedding.vectors"
GEN_AI_RESPONSE_RERANK_COUNT      = "gen_ai.response.rerank.count"
GEN_AI_RESPONSE_RERANK_TOP_SCORE  = "gen_ai.response.rerank.top_score"

# --------------------------------------------------------------------------- #
# Rotator-specific telemetry (bandit cascade) — rides alongside gen_ai.* on
# the per-attempt span. Lets us slice LangFuse generations by arm, reward,
# error class, dd_process namespace.
# --------------------------------------------------------------------------- #
BANDIT_DEPLOYMENT_ID = "bandit.deployment_id"
BANDIT_PROVIDER      = "bandit.provider"
BANDIT_ATTEMPT       = "bandit.attempt"
BANDIT_LATENCY_S     = "bandit.latency_s"
BANDIT_REWARD        = "bandit.reward"
BANDIT_ERROR_CLASS   = "bandit.error_class"
BANDIT_SCHEMA_VALID  = "bandit.schema_valid"
BANDIT_DD_PROCESS    = "bandit.dd_process"
BANDIT_TOTAL_ATTEMPTS = "bandit.total_attempts"
BANDIT_FALLBACK      = "bandit.fallback"

# --------------------------------------------------------------------------- #
# Span names. Names with the `gen_ai.*` prefix are picked up by LangFuse as
# generation observations; others become generic spans.
# --------------------------------------------------------------------------- #
SPAN_NAME_CHAT           = "gen_ai.chat"
SPAN_NAME_EMBED          = "gen_ai.embed"
SPAN_NAME_RERANK         = "gen_ai.rerank"
SPAN_NAME_BANDIT_CASCADE = "rotator.bandit_cascade"
SPAN_NAME_BANDIT_ATTEMPT = "gen_ai.chat"

# --------------------------------------------------------------------------- #
# Operation names — populate gen_ai.operation.name.
# --------------------------------------------------------------------------- #
OP_CHAT      = "chat"
OP_EMBEDDING = "embedding"
OP_RERANK    = "rerank"

# --------------------------------------------------------------------------- #
# Default `gen_ai.system` when the deployment id doesn't carry a provider
# prefix (e.g., when LiteLLM's Router shuffles internally).
# --------------------------------------------------------------------------- #
SYSTEM_LITELLM_ROTATOR = "litellm-rotator"
