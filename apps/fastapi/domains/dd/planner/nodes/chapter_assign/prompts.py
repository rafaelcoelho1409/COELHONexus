"""chapter_assign prompt builder — STATIC PREFIX + DYNAMIC SUFFIX shape
for KV cache hits across providers."""
from __future__ import annotations

from .params import BODY_CHARS


def build_prompt(
    *,
    framework: str,
    source_key: str,
    doc_summary: str,
    doc_terms: list[str],
    doc_body: str,
    proposals: list[dict],
) -> str:
    chapters_block = "\n".join([
        f"[{i}] {p.get('title')!r}\n"
        f"    description: {p.get('description', '')}\n"
        f"    key_concepts: {', '.join((p.get('key_concepts') or [])[:10])}"
        for i, p in enumerate(proposals)
    ])
    if doc_summary:
        doc_block = (
            f"SUMMARY: {doc_summary}\n"
            f"KEY_TERMS: {', '.join(doc_terms[:8])}"
        )
    else:
        body_snip = (doc_body or "")[:BODY_CHARS]
        doc_block = f"BODY (truncated):\n{body_snip}"
    # prompt-prefix reordering for KV cache hits. The
    # chapter list + scoring rubric are IDENTICAL across all 135+ doc
    # calls in a single run, so they go FIRST as a cacheable prefix. The
    # per-doc file info (the only thing that varies) goes LAST. Providers
    # with prefix-KV-cache (Groq, Gemini implicit, DeepSeek, NIM) get
    # warm hits after the first call.
    return (
        # ── STATIC PREFIX (KV-cacheable across all docs in this corpus) ──
        f"You are assigning ONE documentation file to chapters in a "
        f"{framework} learning book. The file may belong to multiple "
        f"chapters (multi-assignment) or none.\n\n"
        f"== AVAILABLE CHAPTERS ==\n"
        f"{chapters_block}\n"
        f"== END CHAPTERS ==\n\n"
        f"For EACH chapter (in order), output a confidence score:\n"
        f"  0.0 → this doc is unrelated\n"
        f"  0.3 → tangential mention\n"
        f"  0.7 → primary supporting doc\n"
        f"  1.0 → canonical reference for this chapter\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"scores": [{{"chapter_idx": 0, "confidence": <float>}}, '
        f'{{"chapter_idx": 1, "confidence": <float>}}, ...]}}\n\n'
        f"Cover EVERY chapter (one entry per chapter index, including "
        f"0.0 scores). Be honest — most docs only belong to 1-3 "
        f"chapters.\n\n"
        # ── DYNAMIC SUFFIX (only thing that varies per call) ──
        f"== FILE: {source_key} ==\n"
        f"{doc_block}"
    )
