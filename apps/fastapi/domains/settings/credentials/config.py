from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class StorageLayout:
    # MinIO object paths. Unchanged from the previous layout on
    # purpose — renaming the code package must not orphan already-persisted
    # `credentials.enc` / `kek.key` / `settings.json` blobs.
    credentials: str = "llm/credentials.enc"
    kek:         str = "llm/kek.key"
    settings:    str = "llm/settings.json"


STORAGE = StorageLayout()
