"""settings router — tunables + lookup tables."""
from __future__ import annotations


PROVIDER_META: dict[str, dict] = {
    "groq":      {"name": "Groq",          "kind": "free"},
    "nim":       {"name": "NVIDIA NIM",    "kind": "free"},
    "cerebras":  {"name": "Cerebras",      "kind": "free"},
    "mistral":   {"name": "Mistral",       "kind": "free"},
    "gemini":    {"name": "Google Gemini", "kind": "free"},
    "sambanova": {"name": "SambaNova",     "kind": "free"},
    "deepseek":  {"name": "DeepSeek",      "kind": "free"},
}
