"""vault — byte-exact code preservation via hash-addressed sentinels (arXiv 2601.03640 / 2510.11394 / 2512.12117).
Replaces fenced blocks with `<code-ref hash="..." lang="..."/>` before LLM; materializes back byte-exactly post-LLM via deterministic regex. Pure module; I/O in service.py."""
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
    """Replace every fenced block with a hash sentinel; return (sentinelized_text, vault). Backtick/tilde fences only (inline code and indented blocks are not vaulted). Raises ValueError if already sentinelized."""
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
    """Reverse sentinelize_doc: replace sentinels with fence_text. Unknown sentinels left in place (audit_roundtrip flags them as invented). Hash-only regex so extra LLM-added attrs don't break resolution."""
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
    """Four-dimension audit (missing/invented/duplicated/orphaned) before materialize. ok=True iff all lists empty."""
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
    """Render one vault entry as a Visible Vault envelope (hash+lang+LOC+body) for the LLM. Defaults to NO truncation (quality > token budget). Output is still byte-perfect — renderer materializes from vault[hash] regardless of what the LLM echoes."""
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
    """Format vault entries as Visible Vault envelopes. Defaults to NO truncation; bandit handles context-window variance across arms."""
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


def score_entry_pedagogy(entry: VaultEntry) -> float:
    """Pedagogical priority for a vault entry; higher = more canonical. Typically in [0.0, 3.0]."""
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
