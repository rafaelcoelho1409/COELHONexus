from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class StorageLayout:
    credentials: str = "llm/credentials.enc"
    kek:         str = "llm/kek.key"
    settings:    str = "llm/settings.json"


STORAGE = StorageLayout()
