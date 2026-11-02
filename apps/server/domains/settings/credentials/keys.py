from __future__ import annotations


KEK_ENV = "KD_CREDS_KEY"

# Only names in this tuple accepted by set_key() — blocks env-var exfiltration.
# The old multi-provider chat registry (Groq / NVIDIA / Cerebras / Mistral /
# Google / SambaNova / DeepSeek) was removed with the heuristically-routed
# gateway: chat and embeddings each have exactly ONE configured external
# endpoint now (Settings page).
MANAGED_KEY_ENVS: tuple[str, ...] = (
    # API key for the chat endpoint DD / YCS call. Blank when the
    # endpoint needs no auth. See domains/settings/chat/service.py.
    # LLM_API_KEY is current; COELHO_LLM_API_KEY is the legacy name, kept
    # so an already-stored key under the old name stays readable/deletable.
    "LLM_API_KEY",
    "COELHO_LLM_API_KEY",
    # Same idea, independent endpoint — embeddings (YCS) can point at
    # a different provider than chat. See domains/settings/embeddings/service.py.
    "COELHO_EMBEDDING_API_KEY",
)
