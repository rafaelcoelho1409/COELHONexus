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
    # Saved search service credentials. These are write-only in Settings and
    # are sent only to the selected service during a connection check.
    "COELHO_SEARCH_API_KEY",
    "LINKUP_API_KEY",
    "YOUCOM_API_KEY",
    "PARALLEL_SEARCH_MCP_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
    "GEEKTFLARE_API_KEY",
    "TINYFISH_API_KEY",
    "FIRECRAWL_API_KEY",
    "SERPER_API_KEY",
    "JINA_API_KEY",
    "SERPAPI_API_KEY",
)
