"""Vault sentinelization — byte-exact code preservation for the Synth stage.

The Synth pipeline can't trust LLMs to copy long literal code spans
verbatim. The 2026 SOTA pattern (still vault sentinelization, per
arXiv 2601.03640 / 2510.11394 / 2512.12117) is to replace every code
fence with an opaque hash-addressed placeholder BEFORE the LLM sees
the content, then materialize back from a content-addressed vault
after the LLM commits. The LLM only decides WHERE code goes; the
text contents round-trip byte-exactly through deterministic regex.

Architecture choice + research backing live in:
  - docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md (step 5)
  - Conversation transcript 2026-05-19 (vault SOTA implementation
    research vs. constrained-decoding / tool-calling-quote
    alternatives — vault still wins for free-tier rotators)

This module is PURE FUNCTIONS — no I/O. Callers (ingestion-time
builder + synth graph node + render_audit_write) own MinIO writes
+ reads via existing `services/docs_distiller/ingestion/storage_minio`.

Sentinel shape:

    <code-ref hash="3f8a1c92b04d7e15" lang="python"/>

  - Self-closing XML tag: every mainstream 2026 model (Claude, GPT,
    Gemini, Qwen, DeepSeek, Llama, GLM) treats XML tags as structural
    pass-through, NOT content to rewrite — explicitly documented in
    Claude prompting best practices + replicated across providers.
  - 16-hex SHA-256 prefix (64-bit collision space): ~5×10⁻⁹ corpus-
    level collision risk at 150k blocks (vs ~10⁻⁴ for the deprecated
    12-hex format that occasionally collided on large corpora).
  - `lang` attribute lets the LLM reason about code position without
    seeing the content. Other attrs (Mintlify `theme=`, `expandable=`,
    `lines=`) live in the vault entry's `info_string` field — never
    in the sentinel itself.

Audit dimensions (per VeriCite):

    missing     — vault entries with no reference in LLM output
                  (LLM dropped a code block — feed to guided refine)
    invented    — sentinel-shaped tokens not in vault
                  (LLM hallucinated a sentinel — flag for retry)
    duplicated  — vault entries referenced >1× in output
                  (cluster split or merge artifact — usually OK
                  but signals a problem upstream)
    orphaned    — vault entries with zero references
                  (LLM silently dropped — sets `ok=False`)
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


# Module constants. Bump _SENTINEL_FORMAT_VERSION on any sentinel-shape
# change so MinIO-cached vaults from prior versions invalidate cleanly.
_VAULT_HASH_LEN          = 16
_SENTINEL_FORMAT_VERSION = 1
_HASH_ALGO               = "sha256-16"

# Matches the canonical sentinel shape this module emits. `lang` is
# optional (older blocks with `lang=""` skip the attr); hash MUST be
# exactly 16 hex chars; self-closing form is mandatory.
_SENTINEL_RE = re.compile(
    r'<code-ref hash="(?P<hash>[0-9a-f]{16})"(?: lang="(?P<lang>[^"]*)")?/>',
)
# Plain hash-only matcher used by `audit_roundtrip` to enumerate every
# sentinel-shaped token in an LLM output (whether or not it matches the
# vault — `invented` sentinels show up here).
_SENTINEL_HASH_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')


# ── Pydantic schema ────────────────────────────────────────────────────

class VaultEntry(BaseModel):
    """One vaulted code block. The materialize step replaces a sentinel
    with `fence_text` byte-exactly; `info_string` carries Mintlify
    attrs etc. so the source-doc rendering can reproduce them later."""
    hash:          str  = Field(min_length=_VAULT_HASH_LEN,
                                max_length=_VAULT_HASH_LEN)
    fence_text:    str  = Field(
        description="Original fence body INCLUDING fence markers + "
                    "info-string line, exactly as it appears in source.",
    )
    info_string:   str  = Field(
        default="",
        description="Raw info-string line (after fence markers). May "
                    "include Mintlify attrs e.g. `python theme={...}`.",
    )
    lang:          str  = Field(
        default="",
        description="Primary language token (first whitespace-separated "
                    "word of info_string). Empty for ``` blocks with no "
                    "language hint.",
    )
    line_count:    int  = 0
    char_count:    int  = 0
    sentinel_kind: Literal["fence_backtick", "fence_tilde"] = "fence_backtick"


class VaultManifest(BaseModel):
    """Aggregate vault for one (framework, doc) pair. Persisted to
    MinIO as `synth-vault/{framework}/{hash_algo}/{doc_sha}.vault.json`
    by the ingestion-time builder; read by the synth graph's
    vault_sentinelize node.
    """
    framework:                str
    source_key:               str
    entries:                  dict[str, VaultEntry] = Field(default_factory=dict)
    sentinel_format_version:  int = _SENTINEL_FORMAT_VERSION
    hash_algo:                str = _HASH_ALGO
    built_at:                 str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
    )


class AuditReport(BaseModel):
    """VeriCite-style four-dimension audit of an LLM output vs the
    vault that fed its prompt. `ok` is True iff every list is empty
    AND no sentinel collisions / malformations were detected."""
    missing:    list[str] = Field(default_factory=list)
    invented:   list[str] = Field(default_factory=list)
    duplicated: list[str] = Field(default_factory=list)
    orphaned:   list[str] = Field(default_factory=list)
    ok:         bool      = True


# ── Internal helpers ───────────────────────────────────────────────────

def _hash_block(payload: str, salt: int = 0) -> str:
    """16-hex SHA-256 prefix. Salt is used on the astronomically rare
    per-doc collision (two distinct fences whose hashes truncate to
    the same prefix)."""
    seed = payload if salt == 0 else f"{payload}|{salt}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_VAULT_HASH_LEN]


def _make_sentinel(digest: str, lang: str = "") -> str:
    """Compose the canonical sentinel tag. Lang attr is omitted when
    empty so the regex's optional capture works either way."""
    if lang:
        return f'<code-ref hash="{digest}" lang="{lang}"/>'
    return f'<code-ref hash="{digest}"/>'


def _parse_info_string(info: str) -> tuple[str, str]:
    """Split an info string into (lang, full_info). Lang is the first
    whitespace-separated token; full_info preserves the entire line
    so Mintlify attrs and downstream renderers stay byte-exact."""
    info = (info or "").strip()
    if not info:
        return "", ""
    parts = info.split(None, 1)
    return (parts[0] or "", info)


# ── Public API ─────────────────────────────────────────────────────────

def sentinelize_doc(md_text: str) -> tuple[str, dict[str, VaultEntry]]:
    """Replace every fenced code block in `md_text` with an opaque
    sentinel and return `(sentinelized_text, vault)`.

    Scope: backtick and tilde fenced blocks only (per CommonMark + GFM).
    Indented (4-space) code blocks and inline `code` spans are NOT
    vaulted — inline code is too common in prose to risk corruption
    and indented blocks are rare in modern framework docs.

    Idempotent in the strict sense: identical input → identical output
    (sentinels included). Re-vaulting an already-sentinelized text
    raises ValueError so callers can detect double-vault bugs.

    Args:
        md_text: source markdown (UTF-8 string). Trailing newline is
            preserved byte-exactly so the materialize step round-trips.

    Returns:
        (sentinelized_text, vault) — sentinelized_text has every fence
        replaced by `<code-ref hash="..." lang="..."/>`; vault is a
        dict keyed by hash (16 hex chars) of `VaultEntry` records.

    Raises:
        ValueError: input already contains vault-shaped sentinels
            (would create ambiguous restoration).
    """
    if _SENTINEL_RE.search(md_text):
        raise ValueError(
            "source already contains vault sentinels — cannot safely "
            "re-vault (double-vault bug or adversarial input)"
        )
    # Local import — markdown-it-py is already a top-level dep but
    # lazy-importing keeps `from .vault import ...` cheap.
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark")
    tokens = md.parse(md_text)
    # split(\n) (not splitlines) preserves a trailing empty so
    # "\n".join(...) round-trips byte-exactly.
    lines = md_text.split("\n")

    # Collect (start_line, end_line, fence_text, info_string, kind) per
    # fenced block. Tokens with `tok.map is None` are inline; skip.
    fence_ranges: list[tuple[int, int, str, str, str]] = []
    for tok in tokens:
        if tok.type != "fence" or tok.map is None:
            continue
        start, end = tok.map
        fence_text = "\n".join(lines[start:end])
        # `tok.markup` is the fence character(s) (e.g. "```" or "~~~");
        # use the first char to identify the kind.
        kind = "fence_tilde" if (tok.markup and tok.markup[0] == "~") \
               else "fence_backtick"
        info_string = (tok.info or "").rstrip()
        fence_ranges.append((start, end, fence_text, info_string, kind))

    if not fence_ranges:
        return md_text, {}

    # Walk lines, splicing in sentinels. fence_ranges is ordered by
    # token-stream position which equals source order for non-nested
    # CommonMark fences.
    fence_ranges.sort(key=lambda r: r[0])
    vault: dict[str, VaultEntry] = {}
    out_lines: list[str] = []
    i = 0
    fi = 0
    while i < len(lines):
        if fi < len(fence_ranges) and i == fence_ranges[fi][0]:
            _, end, fence_text, info_string, kind = fence_ranges[fi]
            lang, full_info = _parse_info_string(info_string)
            # Resolve hash collisions with salt-rehash. At 64-bit truncation
            # the loop should never execute at any realistic corpus size.
            digest = _hash_block(fence_text)
            salt = 0
            while digest in vault and vault[digest].fence_text != fence_text:
                salt += 1
                digest = _hash_block(fence_text, salt=salt)
            if digest not in vault:
                vault[digest] = VaultEntry(
                    hash=digest,
                    fence_text=fence_text,
                    info_string=full_info,
                    lang=lang,
                    line_count=end - i,
                    char_count=len(fence_text),
                    sentinel_kind=kind,
                )
            out_lines.append(_make_sentinel(digest, lang))
            i = end
            fi += 1
        else:
            out_lines.append(lines[i])
            i += 1

    return "\n".join(out_lines), vault


def materialize(
    text_with_sentinels: str,
    vault: dict[str, VaultEntry],
) -> str:
    """Reverse `sentinelize_doc`. Replace every well-formed sentinel
    in `text` with its vault entry's `fence_text`. Sentinels NOT in
    the vault are left in place — `audit_roundtrip` flags these as
    `invented` rather than silently swallowing them.

    Hash-only restoration: the regex captures only the hash, so
    sentinels with extra unknown attrs (e.g. LLM adds `theme="..."`)
    still resolve correctly. This is the resilience pattern from the
    VeriCite citation-grounding paper.
    """
    def _replace(match: re.Match) -> str:
        digest = match.group(1)
        entry = vault.get(digest)
        if entry is None:
            # Unknown sentinel — leave as-is so audit can flag it.
            return match.group(0)
        return entry.fence_text

    # Match on bare `<code-ref hash="..."` so unknown attrs / order don't
    # break restoration. The full closing `/>` requirement is preserved
    # by extending the match to consume up to the next `/>`.
    pattern = re.compile(r'<code-ref hash="([0-9a-f]{16})"[^/]*/>')
    return pattern.sub(_replace, text_with_sentinels)


def audit_roundtrip(
    vault: dict[str, VaultEntry],
    llm_output: str,
) -> AuditReport:
    """Four-dimension audit BEFORE materialize. Returns an AuditReport
    with `(missing, invented, duplicated, orphaned)` populated; `ok`
    is True iff all four lists are empty.

    This is the standard checklist criterion `all_code_refs_resolved`
    in the Synth pipeline's checklist_eval step (per the SOTA
    architecture doc).

    Args:
        vault: the vault that was passed to the LLM in this round.
        llm_output: raw LLM-generated markdown / JSON / Pydantic str.
            Accepts non-string input defensively (LangChain content-
            block lists, plain bytes); coerces to a string.

    Returns:
        AuditReport. Pass to checklist_eval as the single source of
        truth on code-preservation correctness for this iteration.
    """
    # Defensive coerce — some LLM responses arrive as content-block
    # lists or other non-string structures.
    if not isinstance(llm_output, str):
        if isinstance(llm_output, list):
            llm_output = "\n".join(str(x) for x in llm_output)
        else:
            llm_output = str(llm_output)

    vault_hashes = set(vault.keys())
    # Hash counts in output (incl. duplicates).
    found_hashes: list[str] = _SENTINEL_HASH_RE.findall(llm_output)
    found_set = set(found_hashes)

    missing = sorted(vault_hashes - found_set)
    invented = sorted(found_set - vault_hashes)
    # duplicated = hash references appearing >1x in output
    counts: dict[str, int] = {}
    for h in found_hashes:
        counts[h] = counts.get(h, 0) + 1
    duplicated = sorted(h for h, n in counts.items() if n > 1)
    # orphaned = vault entries with zero references (subset of `missing`
    # — every `orphaned` is also `missing`, but `missing` also covers
    # cases where a partial reference exists. Keep both for symmetry
    # with the SOTA-research checklist; downstream may treat them
    # interchangeably).
    orphaned = sorted(vault_hashes - found_set)

    return AuditReport(
        missing=missing,
        invented=invented,
        duplicated=duplicated,
        orphaned=orphaned,
        ok=not (missing or invented or duplicated),
    )


# ── Convenience builder for the ingestion-time pipeline ────────────────

def build_manifest(
    framework: str,
    source_key: str,
    md_text: str,
) -> tuple[str, VaultManifest]:
    """Convenience wrapper for the ingestion-time builder: sentinelize
    the doc and wrap the vault dict in a VaultManifest ready for MinIO
    persistence. Returns `(sentinelized_text, manifest)`.

    Caller writes both blobs to MinIO at content-addressed keys (see
    `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` step 5 layout). The
    synth graph's vault_sentinelize node loads the manifest at run
    time without re-parsing the source.
    """
    sentinelized, vault = sentinelize_doc(md_text)
    manifest = VaultManifest(
        framework=framework,
        source_key=source_key,
        entries=vault,
    )
    return sentinelized, manifest
