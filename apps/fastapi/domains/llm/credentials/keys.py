from __future__ import annotations


KEK_ENV = "KD_CREDS_KEY"

# Only names in this tuple accepted by set_key() — blocks env-var exfiltration.
MANAGED_KEY_ENVS: tuple[str, ...] = (
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "CEREBRAS_API_KEY",
    "MISTRAL_API_KEY",
    "GOOGLE_API_KEY",
    "SAMBANOVA_API_KEY",
    "DEEPSEEK_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    # API key for the LLM endpoint the Docs Distiller / YCS apps call. Blank
    # for the bundled in-cluster rotator (no auth); set when pointing at an
    # external OpenAI-compatible endpoint. See domains/llm/rotator/chain/service.py.
    "COELHO_LLM_API_KEY",
)
