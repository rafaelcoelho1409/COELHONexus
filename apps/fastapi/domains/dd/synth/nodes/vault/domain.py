"""vault — pure functions (byte-exact code preservation for the Synth stage).

The Synth pipeline can't trust LLMs to copy long literal code spans
verbatim. The 2026 SOTA pattern (per arXiv 2601.03640 / 2510.11394 /
2512.12117) is to replace every code fence with an opaque hash-addressed
placeholder BEFORE the LLM sees the content, then materialize back from
a content-addressed vault after the LLM commits. The LLM only decides
WHERE code goes; the text contents round-trip byte-exactly through
deterministic regex.

Sentinel shape:

    <code-ref hash="3f8a1c92b04d7e15" lang="python"/>

  - Self-closing XML tag: every mainstream 2026 model (Claude, GPT,
    Gemini, Qwen, DeepSeek, Llama, GLM) treats XML tags as structural
    pass-through, NOT content to rewrite.
  - 16-hex SHA-256 prefix (64-bit collision space): ~5×10⁻⁹ corpus-level
    collision risk at 150k blocks (vs ~10⁻⁴ for the deprecated 12-hex
    format that occasionally collided on large corpora).
  - `lang` attribute lets the LLM reason about code position without
    seeing the content. Other attrs (Mintlify `theme=`, `expandable=`,
    `lines=`) live in the vault entry's `info_string` field — never in
    the sentinel itself.

Audit dimensions (per VeriCite):
    missing     — vault entries with no reference in LLM output
    invented    — sentinel-shaped tokens not in vault
    duplicated  — vault entries referenced >1× in output
    orphaned    — vault entries with zero references (sets `ok=False`)

This module is PURE. The I/O shell (`get_or_build_source_vault`) lives in
service.py.
"""
from __future__ import annotations

import hashlib
import re

from .params import PEDAGOGY_LANGS, VAULT_HASH_LEN
from .patterns import SENTINEL_ANY_RE, SENTINEL_HASH_RE, SENTINEL_RE
from .schemas import AuditReport, VaultEntry, VaultManifest


def _hash_block(payload: str, salt: int = 0) -> str:
    """16-hex SHA-256 prefix. Salt is used on the astronomically rare
    per-doc collision (two distinct fences whose hashes truncate to the
    same prefix)."""
    seed = payload if salt == 0 else f"{payload}|{salt}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:VAULT_HASH_LEN]


def _make_sentinel(digest: str, lang: str = "") -> str:
    """Compose the canonical sentinel tag. Lang attr is omitted when
    empty so the regex's optional capture works either way."""
    if lang:
        return f'<code-ref hash="{digest}" lang="{lang}"/>'
    return f'<code-ref hash="{digest}"/>'


def _parse_info_string(info: str) -> tuple[str, str]:
    """Split an info string into (lang, full_info). Lang is the first
    whitespace-separated token; full_info preserves the entire line so
    Mintlify attrs and downstream renderers stay byte-exact."""
    info = (info or "").strip()
    if not info:
        return "", ""
    parts = info.split(None, 1)
    return (parts[0] or "", info)


def sentinelize_doc(md_text: str) -> tuple[str, dict[str, VaultEntry]]:
    """Replace every fenced code block in `md_text` with an opaque
    sentinel and return `(sentinelized_text, vault)`.

    Scope: backtick and tilde fenced blocks only (per CommonMark + GFM).
    Indented (4-space) code blocks and inline `code` spans are NOT
    vaulted — inline code is too common in prose to risk corruption and
    indented blocks are rare in modern framework docs.

    Idempotent in the strict sense: identical input → identical output
    (sentinels included). Re-vaulting an already-sentinelized text
    raises ValueError so callers can detect double-vault bugs.
    """
    if SENTINEL_RE.search(md_text):
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

    fence_ranges: list[tuple[int, int, str, str, str]] = []
    for tok in tokens:
        if tok.type != "fence" or tok.map is None:
            continue
        start, end = tok.map
        fence_text = "\n".join(lines[start:end])
        kind = "fence_tilde" if (tok.markup and tok.markup[0] == "~") \
               else "fence_backtick"
        info_string = (tok.info or "").rstrip()
        fence_ranges.append((start, end, fence_text, info_string, kind))

    if not fence_ranges:
        return md_text, {}

    fence_ranges.sort(key = lambda r: r[0])
    vault: dict[str, VaultEntry] = {}
    out_lines: list[str] = []
    i = 0
    fi = 0
    while i < len(lines):
        if fi < len(fence_ranges) and i == fence_ranges[fi][0]:
            _, end, fence_text, info_string, kind = fence_ranges[fi]
            lang, full_info = _parse_info_string(info_string)
            # truncation the loop should never execute at any realistic
            # corpus size.
            digest = _hash_block(fence_text)
            salt = 0
            while (
                digest in vault
                and vault[digest].fence_text != fence_text
            ):
                salt += 1
                digest = _hash_block(fence_text, salt = salt)
            if digest not in vault:
                vault[digest] = VaultEntry(
                    hash = digest,
                    fence_text = fence_text,
                    info_string = full_info,
                    lang = lang,
                    line_count = end - i,
                    char_count = len(fence_text),
                    sentinel_kind = kind,
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
    """Reverse `sentinelize_doc`. Replace every well-formed sentinel in
    `text` with its vault entry's `fence_text`. Sentinels NOT in the
    vault are left in place — `audit_roundtrip` flags these as
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

    return SENTINEL_ANY_RE.sub(_replace, text_with_sentinels)


def audit_roundtrip(
    vault: dict[str, VaultEntry],
    llm_output: str,
) -> AuditReport:
    """Four-dimension audit BEFORE materialize. Returns an AuditReport
    with `(missing, invented, duplicated, orphaned)` populated; `ok` is
    True iff all four lists are empty."""
    # Defensive coerce — some LLM responses arrive as content-block
    # lists or other non-string structures.
    if not isinstance(llm_output, str):
        if isinstance(llm_output, list):
            llm_output = "\n".join(str(x) for x in llm_output)
        else:
            llm_output = str(llm_output)

    vault_hashes = set(vault.keys())
    found_hashes: list[str] = SENTINEL_HASH_RE.findall(llm_output)
    found_set = set(found_hashes)

    missing = sorted(vault_hashes - found_set)
    invented = sorted(found_set - vault_hashes)
    counts: dict[str, int] = {}
    for h in found_hashes:
        counts[h] = counts.get(h, 0) + 1
    duplicated = sorted(h for h, n in counts.items() if n > 1)
    orphaned = sorted(vault_hashes - found_set)

    return AuditReport(
        missing = missing,
        invented = invented,
        duplicated = duplicated,
        orphaned = orphaned,
        ok = not (missing or invented or duplicated),
    )


def format_entry_for_prompt(
    entry: VaultEntry, *, max_chars: int | None = None,
) -> str:
    """Render a vault entry for inclusion in an LLM prompt as a visible
    code envelope showing the FULL code body. Hash + lang + LOC travel
    with the envelope so the LLM can correlate this entry with
    `code_refs[*].hash` in its output.

    Per `feedback_dd_quality_over_speed` (tokens are free, quality >
    speed) and the visible-vault SOTA (the whole point is the LLM sees
    the actual code), this defaults to NO TRUNCATION. The optional
    `max_chars` is kept as a safety valve for pathological cases.

    The LLM's output schema does NOT change — it still emits hash refs
    in the typed `code_refs` field. The renderer materializes verbatim
    from vault[hash] at render time, so even if the LLM mangles the body
    in its prompt-side view, the final markdown is byte-perfect.
    """
    body = entry.fence_text or ""
    if max_chars is not None and len(body) > max_chars:
        body = body[:max_chars] + (
            f"\n... [{len(body) - max_chars} more chars — render-time "
            f"substitution will use the FULL body]"
        )
    lang = entry.lang or "text"
    return (
        f'<code id="{entry.hash}" lang="{lang}" loc="{entry.line_count}">\n'
        f"{body}\n"
        f"</code>"
    )


def format_entries_for_prompt(
    entries: dict[str, VaultEntry], *, hashes: list[str] | None = None,
    max_chars_per_entry: int | None = None,
    max_total_chars: int | None = None,
) -> str:
    """Format a set of vault entries as a sequence of visible envelopes
    for the LLM. If `hashes` is provided, render only those (in order);
    otherwise render every entry in `entries`.

    Defaults to NO TRUNCATION on either per-entry or total size — the
    LLM sees the FULL code. The bandit handles context-window variance
    across arms.
    """
    keys = hashes if hashes is not None else list(entries.keys())
    out: list[str] = []
    running = 0
    for h in keys:
        entry = entries.get(h)
        if entry is None:
            out.append(f'<code id="{h}" missing="true"/>')
            continue
        rendered = format_entry_for_prompt(
            entry, max_chars = max_chars_per_entry,
        )
        if (
            max_total_chars is not None
            and running + len(rendered) > max_total_chars
            and out
        ):
            out.append(
                f'<!-- code bank truncated at {running} chars; '
                f'{len(keys) - len(out)} more entries omitted -->'
            )
            break
        out.append(rendered)
        running += len(rendered)
    return "\n\n".join(out)


# Lightweight scorer applied to vault entries to surface the most
# pedagogically valuable code blocks per section. Used by the SAWC
# writer prompt to order allowed_hashes by priority so the LLM picks
# the canonical example first when it has to cap citations.

def score_entry_pedagogy(entry: VaultEntry) -> float:
    """Pedagogical priority for a single vault entry. Higher = more
    likely to be a canonical teaching example. Score is unbounded but
    typically falls in [0.0, 3.0]."""
    if entry is None:
        return 0.0
    body = entry.fence_text or ""
    loc = max(1, entry.line_count or len(body.splitlines()) or 1)
    lang = (entry.lang or "").lower().strip()

    score = 0.0
    # LOC sweet spot
    if 5 <= loc <= 30:
        score += 1.0
    elif 30 < loc <= 80:
        score += 0.4
    # Best teaching size
    if 10 <= loc <= 25:
        score += 0.5
    # Oneliner penalty
    if loc <= 2:
        score -= 0.5
    # Self-contained imports
    if any(
        body.lstrip().startswith(prefix)
        for prefix in ("import ", "from ", "use ", "require(", "package ")
    ):
        score += 0.3
    # Named API
    if any(kw in body for kw in (
        "def ", "class ", "function ", "fn ", "func ", "export ",
        "interface ",
    )):
        score += 0.5
    # Mainstream language
    if lang in PEDAGOGY_LANGS:
        score += 0.2
    return round(score, 3)


def rank_hashes_by_pedagogy(
    hashes: list[str], vault: dict[str, VaultEntry],
) -> list[str]:
    """Reorder `hashes` by descending pedagogical score. Stable secondary
    sort by hash so re-ranks across identical inputs are deterministic."""
    scored = [
        (score_entry_pedagogy(vault.get(h)), h)
        for h in hashes
    ]
    scored.sort(key = lambda x: (-x[0], x[1]))
    return [h for _, h in scored]


def build_manifest(
    framework: str,
    source_key: str,
    md_text: str,
) -> tuple[str, VaultManifest]:
    """Convenience wrapper for the ingestion-time builder: sentinelize
    the doc and wrap the vault dict in a VaultManifest ready for MinIO
    persistence. Returns `(sentinelized_text, manifest)`."""
    sentinelized, vault = sentinelize_doc(md_text)
    manifest = VaultManifest(
        framework = framework,
        source_key = source_key,
        entries = vault,
    )
    return sentinelized, manifest
