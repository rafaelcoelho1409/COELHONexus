"""settings router — tunables + lookup tables."""
from __future__ import annotations


PROVIDER_META: dict[str, dict] = {
    "groq":      {"name": "Groq",          "kind": "free", "key_url": "https://console.groq.com/keys"},
    "nim":       {"name": "NVIDIA NIM",    "kind": "free", "key_url": "https://build.nvidia.com"},
    "cerebras":  {"name": "Cerebras",      "kind": "free", "key_url": "https://cloud.cerebras.ai"},
    "mistral":   {"name": "Mistral",       "kind": "free", "key_url": "https://console.mistral.ai/api-keys"},
    "gemini":    {"name": "Google Gemini", "kind": "free", "key_url": "https://aistudio.google.com/apikey"},
    "sambanova": {"name": "SambaNova",     "kind": "free", "key_url": "https://cloud.sambanova.ai"},
    "deepseek":  {"name": "DeepSeek",      "kind": "free", "key_url": "https://platform.deepseek.com/api_keys"},
}
