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
  - 16-hex SHA-256 prefix (64-bit collision space): ~5x10^-9 corpus-
    level collision risk at 150k blocks (vs ~10^-4 for the deprecated
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
    duplicated  — vault entries referenced >1x in output
                  (cluster split or merge artifact — usually OK
                  but signals a problem upstream)
    orphaned    — vault entries with zero references
                  (LLM silently dropped — sets `ok=False`)
"""
from __future__ import annotations

import hashlib
import re

from .params import VAULT_HASH_LEN
from .patterns import SENTINEL_HASH_RE, SENTINEL_RE
from .schemas import AuditReport, VaultEntry, VaultManifest


def _hash_block(payload: str, salt: int = 0) -> str:
    """16-hex SHA-256 prefix. Salt is used on the astronomically rare
    per-doc collision (two distinct fences whose hashes truncate to
    the same prefix)."""
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
    whitespace-separated token; full_info preserves the entire line
    so Mintlify attrs and downstream renderers stay byte-exact."""
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
    found_hashes: list[str] = SENTINEL_HASH_RE.findall(llm_output)
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


#
# Original vault design hid code from the LLM by replacing fences with
# opaque hash sentinels. Empirical failure mode (FastMCP chapter 1): the
# LLM cannot pick the canonical example among opaque hash IDs, so it cites
# zero → final chapter = pure prose. See
# docs/KD-CODE-FIRST-SOTA-2026-05-24.md.
#
# Fix per arXiv 2601.03640 / Yeung 2025 ("Deterministic Quoting"): give
# the LLM full visibility into the code WHILE preserving byte-perfect
# render-time substitution via the same hash-keyed vault. The LLM plans
# (which hashes to cite) with informed picks; render emits verbatim from
# vault[hash] regardless of what the LLM would have reproduced.


def format_entry_for_prompt(
    entry: "VaultEntry", *, max_chars: int | None = None,
) -> str:
    """Render a vault entry for inclusion in an LLM prompt as a visible
    code envelope showing the FULL code body. Hash + lang + LOC travel
    with the envelope so the LLM can correlate this entry with
    `code_refs[*].hash` in its output.

    Per `feedback_kd_quality_over_speed` (tokens are free, quality > speed)
    and the visible-vault SOTA (the whole point is the LLM sees the actual
    code), this defaults to NO TRUNCATION. The optional `max_chars` is
    kept as a safety valve for pathological cases (e.g., a single
    auto-generated 50K-line dump that would otherwise blow past every
    free-tier context window). Set it only when you need it.

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
    entries: dict[str, "VaultEntry"], *, hashes: list[str] | None = None,
    max_chars_per_entry: int | None = None,
    max_total_chars: int | None = None,
) -> str:
    """Format a set of vault entries as a sequence of visible envelopes
    for the LLM. If `hashes` is provided, render only those (in order);
    otherwise render every entry in `entries`.

    Defaults to NO TRUNCATION on either per-entry or total size — the
    LLM sees the FULL code, per `feedback_kd_quality_over_speed`. The
    bandit handles context-window variance across arms: a prompt that
    overflows Mistral-Large-2 (128K) cascades to Llama-4-maverick (1M)
    or Gemini-2.5-flash (1M).

    `max_chars_per_entry` / `max_total_chars` remain as opt-in safety
    valves for pathological corpora; leave them as None for normal use.
    """
    keys = hashes if hashes is not None else list(entries.keys())
    out: list[str] = []
    running = 0
    for h in keys:
        entry = entries.get(h)
        if entry is None:
            out.append(f'<code id="{h}" missing="true"/>')
            continue
        rendered = format_entry_for_prompt(entry, max_chars=max_chars_per_entry)
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


#
# Lightweight scorer applied to vault entries to surface the most
# pedagogically valuable code blocks per section. Used by the SAWC writer
# prompt to order allowed_hashes by priority so the LLM picks the canonical
# example first when it has to cap citations. No new LangGraph node —
# pure-Python heuristics layered on the existing vault.
#
# Score components (weighted, max ≈ 3.0):
#   - LOC sweet spot (5-30 lines): +1.0   (short = pedagogical)
#   - has_imports (self-contained): +0.3
#   - has_function_or_class (named API): +0.5
#   - lang is python/js/ts/typescript (mainstream): +0.2
#   - is_canonical_size (10-25 lines): +0.5 (best teaching size)
#   - is_oneliner (≤2 lines): -0.5 (usually trivial — penalty)
#
# This is NOT a learned ranker. It's a heuristic baseline that beats
# random hash order. SOTA learned ranking (e.g., AdaRubric exemplar-
# based scoring) is deferred to a future ship.

_PEDAGOGY_LANGS = frozenset({
    "python", "py", "javascript", "js", "typescript", "ts", "go",
    "rust", "java", "c", "cpp", "c++", "ruby", "php", "shell", "bash",
})


def score_entry_pedagogy(entry: "VaultEntry") -> float:
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
        "def ", "class ", "function ", "fn ", "func ", "export ", "interface ",
    )):
        score += 0.5
    # Mainstream language
    if lang in _PEDAGOGY_LANGS:
        score += 0.2
    return round(score, 3)


def rank_hashes_by_pedagogy(
    hashes: list[str], vault: dict[str, "VaultEntry"],
) -> list[str]:
    """Reorder `hashes` by descending pedagogical score. Stable secondary
    sort by hash so re-ranks across identical inputs are deterministic."""
    scored = [
        (score_entry_pedagogy(vault.get(h)), h)
        for h in hashes
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [h for _, h in scored]


# CRITICAL FIX (2026-05-24 evening) — read-time vault provisioning
# Root-cause discovery: ingestion produces per-page markdown files at
# `ingestion/{slug}/pages/{idx}-{slug}.md` but the vault builder only ran
# on the consolidated `llms-full.txt` crawl, producing exactly ONE
# `synth-vault/{slug}/pages/0000-gofastmcp-com-llms-full.vault.json` for
# all 335 fastmcp pages. When digest_construct calls extract_vault_hashes
# on individual ingestion pages they have NO sentinels → digest LLM emits
# empty code_refs → sawc has zero allowed_hashes per section → final
# chapter has zero code blocks.
#
# Surgical fix: lazy per-source sentinelization. When a per-source vault
# file doesn't exist on MinIO, run sentinelize_doc on the raw ingestion
# page at read time. This populates the runtime vault for downstream
# nodes WITHOUT requiring an ingestion-pipeline rebuild.


async def get_or_build_source_vault(
    minio, slug: str, source_key: str,
) -> tuple[str, dict[str, "VaultEntry"]]:
    """Return (sentinelized_text, vault_entries) for one source page.

    Resolution order:
      1. Pre-built per-source artifacts (`synth-vault/{slug}/pages/...
         {basename}.sentinelized.md` + `.vault.json`) — preferred path,
         used when the ingestion-time builder ran per-page.
      2. Runtime sentinelization of `ingestion/{slug}/pages/...` raw
         markdown — fallback when the per-page artifacts are missing
         (e.g., the consolidated `llms-full` crawl populated a single
         mega-vault instead of per-page vaults).

    Always returns sentinelized text so downstream nodes see
    `<code-ref hash="..."/>` placeholders in source bodies; the vault
    dict maps each hash to its VaultEntry (with the original fence_text
    body that render_audit_write materializes at the end of synth).
    """
    import json as _json
    # Compute the expected per-page vault path. Mirror render's transform
    # (we can't import render here without creating a circular dep).
    basename = source_key.rstrip("/").rsplit("/", 1)[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    vault_key = f"synth-vault/{slug}/pages/{basename}.vault.json"
    sentinel_key = f"synth-vault/{slug}/pages/{basename}.sentinelized.md"

    # 1. Try pre-built artifacts.
    if await minio.exists(vault_key) and await minio.exists(sentinel_key):
        try:
            manifest = _json.loads(await minio.read_text(vault_key))
            sentinelized = await minio.read_text(sentinel_key)
            entries: dict[str, VaultEntry] = {}
            for h, d in (manifest.get("entries") or {}).items():
                if isinstance(d, dict):
                    try:
                        entries[h] = VaultEntry(**d)
                    except Exception:
                        # Tolerate schema drift — fall back to a minimal
                        # entry so downstream gets the body at least.
                        if d.get("fence_text"):
                            entries[h] = VaultEntry(
                                hash=h,
                                fence_text=d.get("fence_text", ""),
                                info_string=d.get("info_string", ""),
                                lang=d.get("lang", ""),
                                line_count=int(d.get("line_count") or 0),
                                char_count=int(d.get("char_count") or 0),
                                sentinel_kind=d.get(
                                    "sentinel_kind", "fence_backtick",
                                ),
                            )
            return sentinelized, entries
        except Exception:
            # Fall through to runtime path if pre-built artifacts are
            # corrupted.
            pass

    # 2. Runtime sentinelization of the raw ingestion page.
    try:
        raw = await minio.read_text(source_key)
    except Exception:
        return "", {}
    if "<code-ref hash=" in raw:
        # Already sentinelized at source (shouldn't normally happen for
        # ingestion pages, but defensive).
        return raw, {}
    try:
        return sentinelize_doc(raw)
    except Exception:
        return raw, {}


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
