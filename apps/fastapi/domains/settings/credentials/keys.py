from __future__ import annotations


KEK_ENV = "KD_CREDS_KEY"

# Only names in this tuple accepted by set_key() — blocks env-var exfiltration.
# The old multi-provider chat registry (Groq / NVIDIA / Cerebras / Mistral /
# Google / SambaNova / DeepSeek) was removed with the heuristically-routed
# gateway: chat and embeddings each have exactly ONE configured external
# endpoint now (Settings page), plus Research Radar's optional source tool keys.
MANAGED_KEY_ENVS: tuple[str, ...] = (
    # API key for the chat endpoint DD / YCS / RR call. Blank when the
    # endpoint needs no auth. See domains/settings/chat/service.py.
    "COELHO_LLM_API_KEY",
    # Same idea, independent endpoint — embeddings (YCS, RR) can point at
    # a different provider than chat. See domains/settings/embeddings/service.py.
    "COELHO_EMBEDDING_API_KEY",
    # Optional source tool keys (Research Radar) — unlock higher rate
    # limits on third-party data sources. See api/v1/rr/tool_credentials.
    "SEMANTIC_SCHOLAR_API_KEY",
)
