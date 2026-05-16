#!/usr/bin/env python3
"""
Gemini 1+N Experiment — standalone proof-of-concept for the Hybrid Pivot.

WHAT THIS IS
============
A 2-hour experiment to answer ONE question:

  Does a single long-context Gemini 2.5 Flash call (the OUTLINE pass)
  followed by N parallel per-chapter expansion calls produce materially
  better docs-distilled output than the current ~4000-LoC hierarchical
  pipeline (the one that landed at mean coherence 0.388, 1 RED + 6 YELLOW,
  0 GREEN chapters on LiteLLM)?

WHAT THIS IS NOT
================
A production system. This script deliberately bypasses everything in
`apps/fastapi`, `apps/fasthtml`, `services/`, the rotator, MinIO, Celery,
LangGraph, and the observability stack. No imports from the existing
project. Pure standalone — so the architectural comparison is clean.

ARCHITECTURE
============
   ┌─ Fetch llms-full.txt (httpx)
   └─ Split on H2 boundaries → sections[]

  Call 1 — OUTLINE  (Gemini 2.5 Flash, ~150K-token corpus + section index)
   └─ Returns: ChapterOutline{ chapters[{title, goal, key_concepts, section_ids[]}] }

  Calls 2..N+1 — EXPANSION  (parallel, sem=5, Gemini 2.5 Flash)
   └─ For each chapter: assigned-section content → README + challenges + flashcards

  Diagnostic
   └─ Embed chapter titles + per-chapter content via text-embedding-004
   └─ Mean cos(title, files) per chapter == same metric as v3 baseline
   └─ Compare: v3 mean=0.388, 1 RED + 6 YEL → ?

USAGE
=====
  export GEMINI_API_KEY="..."          # https://aistudio.google.com (free, no card)
  python run.py                        # defaults: LiteLLM
  python run.py --framework Pydantic --url https://pydantic.dev/docs/validation/llms-full.txt
  python run.py --out ./out-litellm-v1

OUTPUT (under --out, default ./out)
====================================
  outline.json                     full Gemini outline response
  chapter01/README.md
  chapter01/challenges.md
  chapter01/flashcards.json
  ...
  summary.json                     timing, token usage, mean coherence,
                                   per-chapter coherence vs threshold flags
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
from pydantic import BaseModel, Field


# =============================================================================
# Config
# =============================================================================
# Per-provider configuration. Both options offer 1M-token context on free
# tier, but with different quotas + SDK shapes. Pick via --provider.
#
# Observed limits (May 2026):
#   gemini : 1M ctx, ~20 RPD on a fresh free account (much tighter than
#            the 1,500 RPD third-party trackers claim); retry-after ~55s.
#   nim    : 1M ctx (DeepSeek V4 Flash verified via NIM API reference),
#            40 RPM, no documented hard RPD cap — far more headroom for
#            iteration. Output cap ~16K tokens per call.
PROVIDER_CONFIG: dict = {
    "gemini": {
        "llm_model": "gemini-2.5-flash",
        "embed_model": "text-embedding-004",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": None,                       # native google-genai SDK
        "max_output_tokens": 16384,
    },
    "nim": {
        "llm_model": "deepseek-ai/deepseek-v4-flash",
        "embed_model": "nvidia/llama-nemotron-embed-1b-v2",
        "api_key_env": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "max_output_tokens": 16384,
    },
}

PARALLEL_EXPANSIONS = 1                     # serialize for tight per-provider limits
EXPANSION_PACING_S = 4.0                    # pause between expansion calls
RETRY_429_MAX = 4                           # max attempts per call when 429d
RETRY_429_DEFAULT_DELAY_S = 60.0            # fallback if retryDelay not present
HTTP_TIMEOUT_S = 60
LLM_TIMEOUT_S = 180
DEFAULT_FRAMEWORK = "LiteLLM"
DEFAULT_URL = "https://docs.litellm.ai/llms-full.txt"
DEFAULT_PROVIDER = "gemini"

# v3 baseline for comparison
V3_BASELINE = {
    "mean_coherence": 0.388,
    "red_count": 1,
    "yel_count": 6,
    "grn_count": 0,
    "n_chapters": 8,
    "threshold_red": 0.35,
    "threshold_yellow": 0.50,
}


# =============================================================================
# Pydantic schemas — used as Gemini response_schema for structured output
# =============================================================================
class ChapterPlan(BaseModel):
    number: int = Field(description="1-indexed chapter number")
    title: str = Field(description="Specific, descriptive title. Avoid generic words like 'Overview' or 'General'")
    goal: str = Field(description="One-sentence learning objective for the chapter")
    key_concepts: list[str] = Field(description="3-7 concrete concepts/skills this chapter teaches")
    section_ids: list[int] = Field(description="List of section IDs (from the provided section index) assigned to this chapter")


class ChapterOutline(BaseModel):
    chapters: list[ChapterPlan] = Field(description="4-12 ordered chapters spanning the full documentation")
    reasoning: str = Field(description="Brief explanation of the curriculum design choices")


class Flashcard(BaseModel):
    front: str
    back: str


class ChapterContent(BaseModel):
    readme_md: str = Field(description="Markdown content of the chapter README (1500-3000 words). Preserve code blocks verbatim from the docs. Use proper markdown headings.")
    challenges: list[str] = Field(description="5-10 challenge scenarios that test the chapter's content")
    flashcards: list[Flashcard] = Field(description="8-15 flashcards for spaced repetition")


# =============================================================================
# Internal types
# =============================================================================
@dataclass
class Section:
    idx: int
    heading: str
    body: str        # full body including the heading line
    char_start: int  # offset into the original corpus


@dataclass
class StudyMetrics:
    fetch_ms: int = 0
    split_ms: int = 0
    outline_ms: int = 0
    expansion_ms: int = 0
    coherence_ms: int = 0
    total_ms: int = 0
    outline_input_tokens: int = 0
    outline_output_tokens: int = 0
    expansion_input_tokens: int = 0
    expansion_output_tokens: int = 0
    n_calls: int = 0


# =============================================================================
# Corpus utilities (deterministic, no LLM)
# =============================================================================
async def fetch_corpus(url: str) -> str:
    """Pull the framework's llms-full.txt. Plain HTTP, single request."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        return r.text


_H2_RE = re.compile(r"^(#{1,2})\s+(.+?)$", re.MULTILINE)


def split_by_headings(corpus: str) -> list[Section]:
    """Split corpus into sections on H1/H2 boundaries.

    Deterministic; same shape the existing post_ingest.split_monolith_if_needed
    produces, but inline (no MinIO, no markdown-it-py needed for the simple
    case — regex is good enough for the experiment).
    """
    matches = list(_H2_RE.finditer(corpus))
    if not matches:
        # No headings found; treat whole corpus as one section
        return [Section(idx=0, heading="(no headings)", body=corpus, char_start=0)]
    sections: list[Section] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(corpus)
        body = corpus[start:end].strip()
        heading = m.group(2).strip()
        # Drop trivially short sections (heading-only, no body)
        if len(body) < 64:
            continue
        sections.append(Section(idx=i, heading=heading, body=body, char_start=start))
    # Renumber idx after filtering so they're contiguous 1..N
    return [Section(idx=i + 1, heading=s.heading, body=s.body, char_start=s.char_start)
            for i, s in enumerate(sections)]


def build_section_index(sections: list[Section]) -> str:
    """Compact one-line-per-section index sent in the outline prompt.

    Format: `[ID] <heading>  (NNN chars)`
    Lets the outline LLM assign section IDs to chapters without needing
    to see full bodies; saves tokens.
    """
    lines = []
    for s in sections:
        lines.append(f"[{s.idx:04d}] {s.heading}  ({len(s.body):,} chars)")
    return "\n".join(lines)


def chapter_content_slice(chapter: ChapterPlan, sections: list[Section]) -> str:
    """Concatenate section bodies for one chapter, in document order."""
    by_id = {s.idx: s for s in sections}
    parts: list[str] = []
    for sid in chapter.section_ids:
        s = by_id.get(int(sid))
        if s is None:
            continue
        parts.append(s.body)
    return "\n\n".join(parts)


# =============================================================================
# Gemini wrappers
# =============================================================================
def _parse_retry_delay(exc) -> float:
    """Pull retryDelay (seconds) from a Gemini 429 ClientError.

    The API returns retry hints in two places:
      - response.error.details[].retryDelay = "55s"
      - the message text includes "Please retry in 55.152568734s"
    Falls back to RETRY_429_DEFAULT_DELAY_S if neither is present.
    """
    try:
        # Most direct path: the structured details in response_json
        details = []
        body = getattr(exc, "response_json", None) or getattr(exc, "details", None)
        if isinstance(body, dict):
            err = body.get("error") or body
            details = err.get("details") or []
        elif isinstance(body, list):
            details = body
        for d in details:
            if not isinstance(d, dict):
                continue
            if d.get("@type", "").endswith("RetryInfo") and "retryDelay" in d:
                raw = d["retryDelay"]
                # format is e.g. "55s" or "55.15s"
                return float(str(raw).rstrip("s")) + 2.0
    except Exception:
        pass
    # Fallback: regex the message
    try:
        msg = str(exc)
        m = re.search(r"retry in ([\d.]+)s", msg)
        if m:
            return float(m.group(1)) + 2.0
    except Exception:
        pass
    return RETRY_429_DEFAULT_DELAY_S


async def _aretry_on_429(coro_fn, *, label: str):
    """Wrap a Gemini call in 429-aware retry. Uses the retryDelay the API
    returns; falls back to a 60s sleep. Re-raises non-429 errors.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_429_MAX + 1):
        try:
            return await coro_fn()
        except Exception as e:
            msg = str(e)
            is_429 = ("429" in msg or "RESOURCE_EXHAUSTED" in msg
                      or "rate" in msg.lower() and "limit" in msg.lower())
            if not is_429 or attempt == RETRY_429_MAX:
                raise
            delay = _parse_retry_delay(e)
            print(f"      ⚠ {label}: 429 (attempt {attempt}/{RETRY_429_MAX}) — "
                  f"sleeping {delay:.0f}s before retry")
            last_exc = e
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")  # pragma: no cover


def _provider_cfg(provider: str) -> dict:
    cfg = PROVIDER_CONFIG.get(provider)
    if cfg is None:
        print(f"ERROR: unknown --provider {provider!r}; pick one of {list(PROVIDER_CONFIG)}")
        sys.exit(2)
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        if provider == "gemini":
            hint = "Get a free key at https://aistudio.google.com"
        else:
            hint = "Get a free key at https://build.nvidia.com"
        print(f"ERROR: {cfg['api_key_env']} env var not set. {hint}")
        sys.exit(2)
    return cfg


def _make_gemini():
    """Lazy import the google-genai SDK."""
    try:
        from google import genai
    except ImportError:
        print("ERROR: pip install google-genai")
        sys.exit(2)
    return genai


def _make_openai_async(base_url: str, api_key_env: str):
    """Lazy AsyncOpenAI client for OpenAI-compatible endpoints (NIM)."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(2)
    return AsyncOpenAI(
        base_url=base_url,
        api_key=os.environ[api_key_env],
        timeout=LLM_TIMEOUT_S,
    )


def _build_outline_messages(framework: str, corpus: str, section_index: str,
                            n_chapters_target: int, schema_json: str) -> list[dict]:
    """OpenAI-compatible messages shape for the outline call (used by --provider nim)."""
    sys_msg = (
        "You are a senior documentation engineer designing learning curricula. "
        "You MUST return ONLY a JSON object matching the provided schema — no "
        "prose, no markdown fences. Do not invent section IDs that aren't in "
        "the provided section index."
    )
    user_msg = f"""Design a {n_chapters_target}-chapter curriculum for {framework} (4-12 chapters allowed).
Audience: a senior software engineer who knows programming but is new to {framework}.

For each chapter:
- Specific descriptive title (avoid "Overview", "General", "Documentation", "Features", "Updates")
- One-sentence learning goal
- 3-7 key concepts
- List of SECTION IDS (from the index below) belonging to this chapter

Quality criteria:
- One coherent topic per chapter
- Foundational → advanced order
- Each section ID in AT MOST ONE chapter
- Cover the framework end-to-end
- Title MUST be supported by the assigned sections' content
- Skip clearly off-topic sections (release-notes, contact-us, broken-link fragments)

Section index:
---
{section_index}
---

Full documentation:
---
{corpus}
---

Return JSON matching this schema:
{schema_json}
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]


def _build_expansion_messages(framework: str, chapter: "ChapterPlan",
                              chapter_content: str, schema_json: str) -> list[dict]:
    sys_msg = (
        "You write pedagogically excellent learning material from documentation. "
        "Preserve all code blocks verbatim. Return ONLY a JSON object matching "
        "the schema — no extra prose, no markdown fences around the JSON."
    )
    user_msg = f"""Write Chapter {chapter.number}: "{chapter.title}" for a {framework} learning curriculum.

Learning goal: {chapter.goal}
Key concepts: {", ".join(chapter.key_concepts)}

Source docs for THIS chapter (use as ground truth; do not invent):
---
{chapter_content}
---

Produce:
- readme_md: a 1500-3000 word chapter in markdown. Use proper headings, code
  fences with language tags, and a pedagogical flow (motivation → concept →
  example → callout). Preserve all code blocks verbatim from the source docs.
  Cite using `(see: §<heading>)`.
- challenges: 5-10 challenge scenarios where the reader applies what they
  learned (one paragraph each).
- flashcards: 8-15 cards. front = specific concept/term/code-pattern; back =
  precise answer with a short code example when relevant.

Return JSON matching this schema:
{schema_json}
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]


def _parse_pydantic_from_json(model_cls, raw_text: str):
    """Defensively parse a JSON string into a Pydantic model.

    Some OpenAI-compatible providers wrap the JSON in markdown fences despite
    being told not to. Strip them before validation.
    """
    s = raw_text.strip()
    if s.startswith("```"):
        # remove first line (fence) and trailing fence
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3].rstrip()
    return model_cls.model_validate_json(s)


async def run_outline(
    *,
    provider: str,
    framework: str,
    corpus: str,
    sections: list[Section],
    n_chapters_target: int,
    metrics: StudyMetrics,
) -> ChapterOutline:
    """Call 1 — single long-context call that sees the whole corpus.

    Provider-aware: dispatches to native Gemini SDK or OpenAI-compatible
    SDK (for NIM). Both paths produce the same ChapterOutline shape.
    """
    cfg = _provider_cfg(provider)
    section_index = build_section_index(sections)

    t0 = time.monotonic()
    if provider == "gemini":
        genai = _make_gemini()
        from google.genai import types
        client = genai.Client(api_key=os.environ[cfg["api_key_env"]])

        prompt = f"""You are designing a learning curriculum for {framework}.

You have full access to the framework's documentation. Produce a {n_chapters_target}-chapter
curriculum (4-12 chapters) optimized for a senior software engineer who knows
programming but is new to {framework}.

For each chapter:
- A specific, descriptive title (avoid generic words like "Overview", "General",
  "Documentation", "Features", "Updates")
- A 1-sentence learning goal
- 3-7 key concepts the chapter teaches
- The list of SECTION IDS from the index below that belong to this chapter

Quality criteria:
- Each chapter should teach ONE coherent topic
- Order chapters from foundational to advanced
- A section ID should appear in AT MOST ONE chapter
- Cover the framework end-to-end
- The chapter title MUST be supported by the assigned sections' content
- Skip clearly off-topic sections (release-notes, contact-us, broken-link fragments)

Section index (use these IDs):
---
{section_index}
---

Full documentation corpus:
---
{corpus}
---

Return the curriculum as JSON matching the schema."""

        async def _do_call():
            return await client.aio.models.generate_content(
                model=cfg["llm_model"],
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ChapterOutline,
                    temperature=0.3,
                ),
            )

        response = await _aretry_on_429(_do_call, label="outline")
        metrics.outline_ms = int((time.monotonic() - t0) * 1000)
        metrics.n_calls += 1
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            metrics.outline_input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            metrics.outline_output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        parsed = response.parsed
        if parsed is None:
            raise RuntimeError(f"Outline returned no parsed JSON; raw:\n{response.text[:500]}")
        return parsed

    elif provider == "nim":
        client = _make_openai_async(cfg["base_url"], cfg["api_key_env"])
        schema_json = json.dumps(ChapterOutline.model_json_schema(), indent=2)
        messages = _build_outline_messages(
            framework=framework, corpus=corpus, section_index=section_index,
            n_chapters_target=n_chapters_target, schema_json=schema_json,
        )

        async def _do_call():
            return await client.chat.completions.create(
                model=cfg["llm_model"],
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=cfg["max_output_tokens"],
            )

        resp = await _aretry_on_429(_do_call, label="outline")
        metrics.outline_ms = int((time.monotonic() - t0) * 1000)
        metrics.n_calls += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            metrics.outline_input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            metrics.outline_output_tokens = getattr(usage, "completion_tokens", 0) or 0
        raw_text = resp.choices[0].message.content
        if not raw_text:
            raise RuntimeError("Outline returned empty content from NIM")
        return _parse_pydantic_from_json(ChapterOutline, raw_text)

    else:
        raise RuntimeError(f"Unknown provider: {provider}")


async def run_chapter_expansion(
    *,
    provider: str,
    framework: str,
    chapter: ChapterPlan,
    chapter_content: str,
    sem: asyncio.Semaphore,
    metrics: StudyMetrics,
) -> ChapterContent:
    """Call 2..N+1 — per-chapter expansion. Provider-aware; same schema."""
    cfg = _provider_cfg(provider)

    async with sem:
        t0 = time.monotonic()

        if provider == "gemini":
            genai = _make_gemini()
            from google.genai import types
            client = genai.Client(api_key=os.environ[cfg["api_key_env"]])

            prompt = f"""Write Chapter {chapter.number}: "{chapter.title}" for a {framework} learning curriculum.

Learning goal:
  {chapter.goal}

Key concepts the chapter must teach:
{chr(10).join(f"  - {c}" for c in chapter.key_concepts)}

Use the documentation excerpts below as the source-of-truth content. Preserve
all code blocks verbatim — do not rewrite or paraphrase code. When you cite
docs, use the format `(see: §<heading>)`.

Produce:
- A well-structured README.md teaching the chapter (1500-3000 words). Use
  proper markdown headings, code fences with language tags, and a
  pedagogical flow (motivation → concept → example → callout).
- 5-10 challenge scenarios — realistic situations where the reader must
  apply what they learned. Each challenge is one paragraph.
- 8-15 flashcards for spaced repetition. Front: a specific concept, term,
  or code-pattern question. Back: a precise answer, with one short code
  example when relevant.

Source docs for THIS chapter:
---
{chapter_content}
---

Return as JSON matching the schema."""

            async def _do_call():
                return await client.aio.models.generate_content(
                    model=cfg["llm_model"],
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ChapterContent,
                        temperature=0.4,
                    ),
                )

            response = await _aretry_on_429(
                _do_call, label=f"ch{chapter.number:02d} expansion",
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                metrics.expansion_input_tokens += getattr(usage, "prompt_token_count", 0) or 0
                metrics.expansion_output_tokens += getattr(usage, "candidates_token_count", 0) or 0
            parsed = response.parsed
            if parsed is None:
                raise RuntimeError(
                    f"Ch{chapter.number} expansion empty; raw:\n{response.text[:500]}"
                )

        elif provider == "nim":
            client = _make_openai_async(cfg["base_url"], cfg["api_key_env"])
            schema_json = json.dumps(ChapterContent.model_json_schema(), indent=2)
            messages = _build_expansion_messages(
                framework=framework, chapter=chapter,
                chapter_content=chapter_content, schema_json=schema_json,
            )

            async def _do_call():
                return await client.chat.completions.create(
                    model=cfg["llm_model"],
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.4,
                    max_tokens=cfg["max_output_tokens"],
                )

            resp = await _aretry_on_429(
                _do_call, label=f"ch{chapter.number:02d} expansion",
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                metrics.expansion_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                metrics.expansion_output_tokens += getattr(usage, "completion_tokens", 0) or 0
            raw_text = resp.choices[0].message.content
            if not raw_text:
                raise RuntimeError(f"Ch{chapter.number} expansion empty content from NIM")
            parsed = _parse_pydantic_from_json(ChapterContent, raw_text)

        else:
            raise RuntimeError(f"Unknown provider: {provider}")

    metrics.expansion_ms = max(metrics.expansion_ms, elapsed_ms)
    metrics.n_calls += 1
    return parsed


# =============================================================================
# Coherence diagnostic — same metric as v3 (mean cos(title_emb, file_emb))
# =============================================================================
async def compute_coherence(
    provider: str,
    framework: str,
    outline: ChapterOutline,
    sections: list[Section],
) -> list[tuple[ChapterPlan, float, list[tuple[str, float]]]]:
    """For each chapter compute mean cos(title_emb, file_emb) over its
    assigned sections. Returns (chapter, mean_score, per_file_scores).

    Provider-aware: uses gemini text-embedding-004 OR nim
    nvidia/llama-nemotron-embed-1b-v2 depending on --provider."""
    cfg = _provider_cfg(provider)

    chapter_titles = [
        f"Chapter on {framework}: {ch.title}. {ch.goal}"
        for ch in outline.chapters
    ]
    section_texts = [s.body[:2000] for s in sections]
    section_by_idx = {s.idx: i for i, s in enumerate(sections)}

    all_texts = chapter_titles + section_texts

    if provider == "gemini":
        genai = _make_gemini()
        client = genai.Client(api_key=os.environ[cfg["api_key_env"]])
        response = await client.aio.models.embed_content(
            model=cfg["embed_model"],
            contents=all_texts,
        )
        vecs = np.array([e.values for e in response.embeddings], dtype=np.float32)
    elif provider == "nim":
        # NIM's OpenAI-compatible embed endpoint typically caps inputs at
        # ~50-100 per request, so batch defensively.
        client = _make_openai_async(cfg["base_url"], cfg["api_key_env"])
        BATCH = 50
        all_vecs: list[list[float]] = []
        for i in range(0, len(all_texts), BATCH):
            batch = all_texts[i : i + BATCH]

            async def _do_call():
                return await client.embeddings.create(
                    model=cfg["embed_model"],
                    input=batch,
                    encoding_format="float",
                    extra_body={"input_type": "passage", "truncate": "END"},
                )

            resp = await _aretry_on_429(
                _do_call, label=f"embed batch {i // BATCH + 1}",
            )
            for item in resp.data:
                all_vecs.append(item.embedding)
        vecs = np.array(all_vecs, dtype=np.float32)
    else:
        raise RuntimeError(f"Unknown provider: {provider}")

    title_vecs = vecs[: len(chapter_titles)]
    section_vecs = vecs[len(chapter_titles):]

    title_norms = np.linalg.norm(title_vecs, axis=1)
    section_norms = np.linalg.norm(section_vecs, axis=1)

    results: list[tuple[ChapterPlan, float, list[tuple[str, float]]]] = []
    for i, ch in enumerate(outline.chapters):
        per_file: list[tuple[str, float]] = []
        for sid in ch.section_ids:
            sidx = section_by_idx.get(int(sid))
            if sidx is None:
                continue
            sv = section_vecs[sidx]
            sn = float(section_norms[sidx])
            tv = title_vecs[i]
            tn = float(title_norms[i])
            denom = max(tn * sn, 1e-12)
            cos = float((tv @ sv) / denom)
            per_file.append((sections[sidx].heading, cos))
        mean = sum(c for _, c in per_file) / max(1, len(per_file))
        results.append((ch, mean, per_file))
    return results


# =============================================================================
# Output writers
# =============================================================================
def write_chapter(out_dir: Path, ch: ChapterPlan, content: ChapterContent) -> None:
    cdir = out_dir / f"chapter{ch.number:02d}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "README.md").write_text(content.readme_md, encoding="utf-8")
    (cdir / "challenges.md").write_text(
        "\n\n".join(f"{i+1}. {c}" for i, c in enumerate(content.challenges)),
        encoding="utf-8",
    )
    (cdir / "flashcards.json").write_text(
        json.dumps([fc.model_dump() for fc in content.flashcards], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_outline(out_dir: Path, outline: ChapterOutline) -> None:
    (out_dir / "outline.json").write_text(
        outline.model_dump_json(indent=2),
        encoding="utf-8",
    )


def write_summary(
    out_dir: Path,
    *,
    framework: str,
    url: str,
    metrics: StudyMetrics,
    coherence: list[tuple[ChapterPlan, float, list[tuple[str, float]]]],
) -> dict:
    red = sum(1 for _, score, _ in coherence if score < 0.35)
    yel = sum(1 for _, score, _ in coherence if 0.35 <= score < 0.50)
    grn = sum(1 for _, score, _ in coherence if score >= 0.50)
    mean = sum(score for _, score, _ in coherence) / max(1, len(coherence))

    summary = {
        "framework": framework,
        "url": url,
        "n_chapters": len(coherence),
        "coherence": {
            "mean": round(mean, 4),
            "red_count": red,
            "yel_count": yel,
            "grn_count": grn,
            "per_chapter": [
                {"number": ch.number, "title": ch.title, "score": round(score, 4),
                 "files": len(ch.section_ids),
                 "low_coherence_files": sorted(per_file, key=lambda kv: kv[1])[:3]}
                for ch, score, per_file in coherence
            ],
        },
        "v3_baseline": V3_BASELINE,
        "delta_vs_v3": {
            "mean_coherence": round(mean - V3_BASELINE["mean_coherence"], 4),
            "red_delta": red - V3_BASELINE["red_count"],
            "yel_delta": yel - V3_BASELINE["yel_count"],
            "grn_delta": grn - V3_BASELINE["grn_count"],
        },
        "timing": {
            "fetch_ms": metrics.fetch_ms,
            "split_ms": metrics.split_ms,
            "outline_ms": metrics.outline_ms,
            "expansion_ms_max_in_parallel": metrics.expansion_ms,
            "coherence_ms": metrics.coherence_ms,
            "total_ms": metrics.total_ms,
        },
        "tokens": {
            "outline_input": metrics.outline_input_tokens,
            "outline_output": metrics.outline_output_tokens,
            "expansion_input_total": metrics.expansion_input_tokens,
            "expansion_output_total": metrics.expansion_output_tokens,
            "grand_total": (
                metrics.outline_input_tokens + metrics.outline_output_tokens
                + metrics.expansion_input_tokens + metrics.expansion_output_tokens
            ),
        },
        "free_tier_consumption": {
            "n_calls": metrics.n_calls,
            "rpd_budget": 1500,
            "calls_remaining_today_estimate": max(0, 1500 - metrics.n_calls),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


# =============================================================================
# Main
# =============================================================================
async def main() -> int:
    parser = argparse.ArgumentParser(description="1+N standalone experiment (Gemini or NIM)")
    parser.add_argument("--provider", choices=list(PROVIDER_CONFIG),
                        default=DEFAULT_PROVIDER,
                        help=(f"LLM provider (default: {DEFAULT_PROVIDER}). "
                              f"'gemini' uses Gemini 2.5 Flash on AI Studio (free, 1M ctx, ~20 RPD). "
                              f"'nim' uses DeepSeek V4 Flash on NVIDIA NIM (free, 1M ctx, 40 RPM)."))
    parser.add_argument("--framework", default=DEFAULT_FRAMEWORK,
                        help=f"Framework name (default: {DEFAULT_FRAMEWORK})")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"llms-full.txt URL (default: {DEFAULT_URL})")
    parser.add_argument("--out", default="./out",
                        help="Output directory (default: ./out)")
    parser.add_argument("--n-chapters", type=int, default=8,
                        help="Target chapter count (the LLM may pick 4-12)")
    parser.add_argument("--skip-coherence", action="store_true",
                        help="Skip the coherence diagnostic (saves ~5-10s)")
    args = parser.parse_args()

    cfg = _provider_cfg(args.provider)
    print(f"=== Provider: {args.provider}  ({cfg['llm_model']}, "
          f"embed: {cfg['embed_model']}) ===")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = StudyMetrics()
    t_total = time.monotonic()

    # 1. Fetch corpus
    print(f"[1/4] Fetching {args.url} …")
    t0 = time.monotonic()
    corpus = await fetch_corpus(args.url)
    metrics.fetch_ms = int((time.monotonic() - t0) * 1000)
    print(f"      ↳ {len(corpus):,} chars in {metrics.fetch_ms:,} ms")

    # 2. Split
    print(f"[2/4] Splitting on H1/H2 …")
    t0 = time.monotonic()
    sections = split_by_headings(corpus)
    metrics.split_ms = int((time.monotonic() - t0) * 1000)
    if not sections:
        print("ERROR: zero sections after split. Is the corpus an HTML page instead of markdown?")
        return 1
    print(f"      ↳ {len(sections)} sections in {metrics.split_ms:,} ms")
    print(f"      ↳ sample: [{sections[0].idx}] {sections[0].heading!r}, "
          f"[{sections[-1].idx}] {sections[-1].heading!r}")

    # 3. OUTLINE — single Gemini call that sees the whole corpus
    # Resume from disk if outline.json already exists. The outline call is the
    # most expensive single request (≥200K input tokens); on a 20 RPD account,
    # never recompute it gratuitously. Delete out/outline.json to force a fresh
    # outline.
    outline_path = out_dir / "outline.json"
    if outline_path.exists():
        print(f"[3/4] Outline cache HIT at {outline_path} — skipping Gemini call.")
        outline = ChapterOutline.model_validate_json(outline_path.read_text())
    else:
        print(f"[3/4] Outline call to {cfg['llm_model']} "
              f"(sees full {len(corpus):,}-char corpus) …")
        outline = await run_outline(
            provider=args.provider, framework=args.framework,
            corpus=corpus, sections=sections,
            n_chapters_target=args.n_chapters, metrics=metrics,
        )
        write_outline(out_dir, outline)
        print(f"      ↳ {len(outline.chapters)} chapters in "
              f"{metrics.outline_ms / 1000:.1f}s "
              f"({metrics.outline_input_tokens:,} in → "
              f"{metrics.outline_output_tokens:,} out tokens)")
    for ch in outline.chapters:
        print(f"        - ch{ch.number:02d}: {ch.title!r} ({len(ch.section_ids)} sections)")

    # 4. EXPANSION — sequential per-chapter with pacing + resume-on-disk
    # Each completed chapter is written immediately; if the run dies mid-way
    # (429 retry exhausted, network glitch), re-running picks up where it
    # stopped. This matters on a 20 RPD account where partial progress is
    # expensive to redo.
    print(f"[4/4] Per-chapter expansion ({len(outline.chapters)} chapters, "
          f"sem={PARALLEL_EXPANSIONS}, pacing {EXPANSION_PACING_S:.0f}s) …")
    sem = asyncio.Semaphore(PARALLEL_EXPANSIONS)
    t0 = time.monotonic()
    results: list[tuple[ChapterPlan, ChapterContent]] = []
    for i, ch in enumerate(outline.chapters):
        cdir = out_dir / f"chapter{ch.number:02d}"
        readme_path = cdir / "README.md"
        flashcards_path = cdir / "flashcards.json"
        # Resume: skip chapters already on disk
        if readme_path.exists() and flashcards_path.exists():
            print(f"      ✓ ch{ch.number:02d} already on disk — skipping")
            existing = ChapterContent(
                readme_md=readme_path.read_text(),
                challenges=[
                    line[3:].strip()
                    for line in (cdir / "challenges.md").read_text().splitlines()
                    if line.strip() and line.lstrip()[:1].isdigit()
                ],
                flashcards=[Flashcard(**fc) for fc in
                            json.loads(flashcards_path.read_text())],
            )
            results.append((ch, existing))
            continue
        # Pace between live calls
        if i > 0:
            print(f"      … pacing {EXPANSION_PACING_S:.0f}s before next call")
            await asyncio.sleep(EXPANSION_PACING_S)
        content_text = chapter_content_slice(ch, sections)
        if not content_text.strip():
            print(f"      ! ch{ch.number:02d} has empty content slice; skipping")
            continue
        try:
            content = await run_chapter_expansion(
                provider=args.provider, framework=args.framework,
                chapter=ch, chapter_content=content_text,
                sem=sem, metrics=metrics,
            )
        except Exception as e:
            print(f"      ✗ ch{ch.number:02d} failed: {type(e).__name__}: {e}")
            print(f"      → progress preserved on disk; re-run to resume")
            metrics.expansion_ms = int((time.monotonic() - t0) * 1000)
            metrics.total_ms = int((time.monotonic() - t_total) * 1000)
            return 3  # partial; non-zero exit
        write_chapter(out_dir, ch, content)
        results.append((ch, content))
        print(f"      ↳ ch{ch.number:02d} ({len(content.readme_md):,} chars README, "
              f"{len(content.challenges)} challenges, {len(content.flashcards)} flashcards)")
    metrics.expansion_ms = int((time.monotonic() - t0) * 1000)

    # 5. COHERENCE diagnostic
    coherence: list[tuple[ChapterPlan, float, list[tuple[str, float]]]] = []
    if not args.skip_coherence:
        print(f"[+]  Coherence diagnostic ({cfg['embed_model']}, same metric as v3 baseline) …")
        t0 = time.monotonic()
        coherence = await compute_coherence(
            args.provider, args.framework, outline, sections,
        )
        metrics.coherence_ms = int((time.monotonic() - t0) * 1000)

    metrics.total_ms = int((time.monotonic() - t_total) * 1000)
    summary = write_summary(
        out_dir, framework=args.framework, url=args.url,
        metrics=metrics, coherence=coherence,
    )

    # =========================================================================
    # Final report — apples-to-apples vs v3 baseline
    # =========================================================================
    print()
    print("=" * 70)
    print(f"DONE — {args.framework}")
    print("=" * 70)
    print(f"Output:           {out_dir}")
    print(f"Chapters:         {summary['n_chapters']}")
    print(f"Total wall-clock: {metrics.total_ms / 1000:.1f}s")
    print(f"Gemini calls:     {metrics.n_calls}  (free-tier RPD budget: 1500)")
    print(f"Tokens (grand):   {summary['tokens']['grand_total']:,}")

    if coherence:
        c = summary["coherence"]
        d = summary["delta_vs_v3"]
        print()
        print(f"COHERENCE (apples-to-apples vs v3 baseline):")
        print(f"  v3 baseline:     mean=0.388, RED=1, YEL=6, GRN=0  (8 chapters)")
        print(f"  this run:        mean={c['mean']:.3f}, RED={c['red_count']}, "
              f"YEL={c['yel_count']}, GRN={c['grn_count']}  ({summary['n_chapters']} chapters)")
        print(f"  delta:           Δmean={d['mean_coherence']:+.3f}, "
              f"ΔRED={d['red_delta']:+d}, ΔYEL={d['yel_delta']:+d}, "
              f"ΔGRN={d['grn_delta']:+d}")
        print()
        print(f"  per-chapter:")
        for entry in c["per_chapter"]:
            flag = "RED " if entry["score"] < 0.35 else ("YEL " if entry["score"] < 0.50 else "GRN ")
            print(f"    {flag} ch{entry['number']:02d}  score={entry['score']:.3f}  "
                  f"files={entry['files']:>3}  {entry['title']!r}")

    print()
    print("VERDICT (eyeball the chapter READMEs in the output dir):")
    print(f"  - If Gemini coherence > v3 baseline + 0.05 AND RED count ≤ v3:")
    print(f"      → pivot is justified; commit to the hybrid refactor")
    print(f"  - If coherence ≈ v3 or worse:")
    print(f"      → current architecture is justified by quality; keep iterating §2.2 fixes")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[interrupted]")
        sys.exit(130)
