"""Prompt builder: static rubric prefix before dynamic per-doc data for KV-cache reuse."""
from __future__ import annotations
from infra.langfuse.prompts import with_langfuse_override
from . import params



def to_fixed_key_prompt(base: str, n: int) -> str:
    """The same prompt with the OUTPUT block asking for `{"c0": .., "c1": .., ..., "c{n-1}": ..}`.
    ValueError when `base` (e.g. a LangFuse template) lacks the expected anchors."""
    a = base.index("OUTPUT — STRICT JSON:")
    b = base.index("== FILE:")
    new = (
        f"OUTPUT — STRICT JSON with EXACTLY {n} keys, one per chapter index: c0, c1, ..., c{n - 1} "
        f"(every key, 0.0 for unrelated chapters):\n"
        '{"c0": <float>, "c1": <float>, ..., "c' + str(n - 1) + '": <float>}\n\n'
        f"{HONEST}\n\n"
    )
    return base[:a] + new + base[b:]


HONEST = "Be honest — most docs only belong to 1-3 chapters."
# The re-ask for a page whose whole score vector came back 0.0 (langfuse 2026-10-14: 54 of 310 pages, ALL from
# nvidia/ising-calibration — a calibrated model that abstains; on the same pages gemma-4-31b never returned a zero
# vector and 33 of 54 flipped on a re-ask, 18 of the 54 even from ising once it was told to name the closest chapter).
CLOSEST_CHAPTER = (
    "Be honest — most docs only belong to 1-3 chapters. Every page was written for some topic: unless NO chapter "
    "shares its topic at all, give the closest chapter at least 0.5."
)


def closest_chapter(prompt: str) -> str:
    """The prompt with the honesty line replaced by CLOSEST_CHAPTER (appended when a template lacks the line)."""
    return prompt.replace(HONEST, CLOSEST_CHAPTER) if HONEST in prompt else f"{prompt}\n\n{CLOSEST_CHAPTER}"


# Shown only when the page's URL path is known: where the site's own authors filed the page is
# strong evidence of its topic (langfuse: `integrations/frameworks/*` pages scored 0.0 for the
# "Frameworks Integrations" chapter and 1.0 for a generic "Observability Features" one).
PATH_RUBRIC = (
    "Each file shows its PAGE URL PATH, the section of the documentation site where its authors filed "
    "it. Weigh it as evidence of the page's topic: pages of one section usually belong to the same chapter.\n\n"
)


@with_langfuse_override("dd.planner.chapter_assign")
def build_prompt(
    *,
    framework: str,
    source_key: str,
    doc_summary: str,
    doc_terms: list[str],
    doc_body: str,
    proposals: list[dict],
    doc_path: str = "",
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
        body_snip = (doc_body or "")[:params.BODY_CHARS]
        doc_block = f"BODY (truncated):\n{body_snip}"
    # Static prefix: chapter list + rubric first (KV-cacheable across 135+ calls); per-doc info last.
    return (
        # Static prefix — KV-cacheable across all corpus calls.
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
        f"{PATH_RUBRIC if doc_path else ''}"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"scores": [{{"chapter_idx": 0, "confidence": <float>}}, '
        f'{{"chapter_idx": 1, "confidence": <float>}}, ...]}}\n\n'
        f"Cover EVERY chapter (one entry per chapter index, including "
        f"0.0 scores). Be honest — most docs only belong to 1-3 "
        f"chapters.\n\n"
        # Dynamic suffix — only thing that varies per call.
        f"== FILE: {source_key} ==\n"
        f"{f'PAGE URL PATH: {doc_path}' + chr(10) if doc_path else ''}"
        f"{doc_block}"
    )
