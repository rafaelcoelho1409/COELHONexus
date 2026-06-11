from __future__ import annotations


KEK_ENV = "KD_CREDS_KEY"

# Whitelist of env-var names this credential store will accept. Anything
# outside this tuple is rejected by domain.validate_managed.
#
# Originally the rotator's PROVIDERS[*].key_env mirror. Extended 2026-06-10
# to also include FastMCP tool API keys — same Fernet-encrypted MinIO file
# (`llm/credentials.enc`), same KEK, same sync resolver. Tool keys are
# OPTIONAL: tools degrade gracefully when unset (slower rate-limit, no key
# header). See apps/fastapi/api/v1/rr/tool_credentials/.
MANAGED_KEY_ENVS: tuple[str, ...] = (
    # LLM rotator providers
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "CEREBRAS_API_KEY",
    "MISTRAL_API_KEY",
    "GOOGLE_API_KEY",
    "SAMBANOVA_API_KEY",
    "DEEPSEEK_API_KEY",
    # FastMCP Research Radar tool keys (apps/fastmcp/domains/rr/tools)
    "SEMANTIC_SCHOLAR_API_KEY",
)
