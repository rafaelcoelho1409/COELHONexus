"""vault — Pydantic schemas (LLM/storage boundary validation)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .params import VAULT_HASH_LEN
from .versions import HASH_ALGO, SENTINEL_FORMAT_VERSION


class VaultEntry(BaseModel):
    """One vaulted code block. The materialize step replaces a sentinel
    with `fence_text` byte-exactly; `info_string` carries Mintlify attrs
    etc. so the source-doc rendering can reproduce them later."""
    hash:          str = Field(
        min_length = VAULT_HASH_LEN, max_length = VAULT_HASH_LEN,
    )
    fence_text:    str = Field(
        description = (
            "Original fence body INCLUDING fence markers + info-string "
            "line, exactly as it appears in source."
        ),
    )
    info_string:   str = Field(
        default = "",
        description = (
            "Raw info-string line (after fence markers). May include "
            "Mintlify attrs e.g. `python theme={...}`."
        ),
    )
    lang:          str = Field(
        default = "",
        description = (
            "Primary language token (first whitespace-separated word of "
            "info_string). Empty for ``` blocks with no language hint."
        ),
    )
    line_count:    int = 0
    char_count:    int = 0
    sentinel_kind: Literal["fence_backtick", "fence_tilde"] = "fence_backtick"


class VaultManifest(BaseModel):
    """Aggregate vault for one (framework, doc) pair; persisted at synth-vault/{framework}/{hash_algo}/{doc_sha}.vault.json."""
    framework:                str
    source_key:               str
    entries:                  dict[str, VaultEntry] = Field(
        default_factory = dict,
    )
    sentinel_format_version:  int = SENTINEL_FORMAT_VERSION
    hash_algo:                str = HASH_ALGO
    built_at:                 str = Field(
        default_factory = lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
    )


class AuditReport(BaseModel):
    """VeriCite-style four-dimension audit of an LLM output vs the vault
    that fed its prompt. `ok` is True iff every list is empty AND no
    sentinel collisions / malformations were detected."""
    missing:    list[str] = Field(default_factory = list)
    invented:   list[str] = Field(default_factory = list)
    duplicated: list[str] = Field(default_factory = list)
    orphaned:   list[str] = Field(default_factory = list)
    ok:         bool      = True
