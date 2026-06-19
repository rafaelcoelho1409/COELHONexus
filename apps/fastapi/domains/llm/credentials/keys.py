from __future__ import annotations


KEK_ENV = "KD_CREDS_KEY"

# Anything outside this tuple is rejected by domain.validate_managed —
# guards against arbitrary env-var exfiltration through set_key().
MANAGED_KEY_ENVS: tuple[str, ...] = (
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "CEREBRAS_API_KEY",
    "MISTRAL_API_KEY",
    "GOOGLE_API_KEY",
    "SAMBANOVA_API_KEY",
    "DEEPSEEK_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
)
