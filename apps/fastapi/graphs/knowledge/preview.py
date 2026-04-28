"""
Tier 4 #16 (2026-04-24) — classical-only preview pipeline.

Runs WITHOUT any LLM call:
    ingest (already done upstream) → noise filter → dedup → cluster (k-means)
    → c-TF-IDF cluster labels → TextRank extractive summaries → write outputs.

Typical wall-clock: ~5 min end-to-end (dominated by embedding — which reuses
the same NVIDIA NIM / local fastembed path as the regular REDUCE step). Zero
LLM synthesis cost. Verbatim-by-construction: every line in the output came
directly from the ingested source.

Use cases per the roadmap:
  (a) sanity-check a topic before committing to a 30-min full run
  (b) fallback when every LLM provider is rate-limited / down
  (c) validation baseline — synth that deviates from preview's topic
      coverage signals hallucination

Dependencies: scikit-learn (already a dep for Clio REDUCE), numpy, regex.
NO new deps — TextRank implemented via numpy power-iteration (~30 LoC) so
we don't pull networkx just for this.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Iterable

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from graphs.knowledge.helpers import (
    _dedup_chapter_files,
    _filter_noise_files,
    _read_raw_prefix,
)
from services.knowledge.embeddings import embed_texts
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[\.!?])\s+(?=[A-Z])"  # end-of-sentence followed by capital
)
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
_PREVIEW_MIN_CHAPTERS = 3
_PREVIEW_MAX_CHAPTERS = 12
_PREVIEW_FILES_PER_CHAPTER_TARGET = 30
_TEXTRANK_MAX_SENTENCES = 8     # extractive summary length
_TEXTRANK_DAMPING = 0.85
_TEXTRANK_TOL = 1e-4
_TEXTRANK_MAX_ITER = 50
_CTFIDF_LABELS_PER_CLUSTER = 5


def _strip_fences(text: str) -> str:
    """Remove fenced code blocks for sentence-level summarization only."""
    return _CODE_FENCE_RE.sub("", text)


def _split_sentences(text: str) -> list[str]:
    """Cheap sentence tokenizer. Drops blanks and near-blanks."""
    stripped = _strip_fences(text).strip()
    if not stripped:
        return []
    # Flatten paragraphs first, then split on sentence boundaries
    flat = re.sub(r"\s+", " ", stripped)
    sents = _SENTENCE_SPLIT_RE.split(flat)
    return [s.strip() for s in sents if len(s.strip()) > 30]


def _textrank_top_sentences(
    sentences: list[str],
    top_k: int = _TEXTRANK_MAX_SENTENCES,
) -> list[str]:
    """
    Classical TextRank (Mihalcea & Tarau, 2004). Builds a sentence-similarity
    graph via TF-IDF cosine, runs PageRank power-iteration, returns the top-K
    sentences in ORIGINAL order (preserves flow).
    """
    if len(sentences) <= top_k:
        return sentences
    try:
        vec = TfidfVectorizer(stop_words = "english", max_df = 0.9, min_df = 1)
        mat = vec.fit_transform(sentences)
        # cosine similarity via normalized dot product
        normed = mat.multiply(1.0 / (np.sqrt(mat.multiply(mat).sum(axis = 1)) + 1e-9))
        sim = (normed @ normed.T).toarray()
        np.fill_diagonal(sim, 0.0)
        # Normalize rows → stochastic matrix
        row_sums = sim.sum(axis = 1, keepdims = True)
        row_sums[row_sums == 0] = 1.0
        trans = sim / row_sums
        n = len(sentences)
        # Power iteration
        pr = np.ones(n) / n
        teleport = (1 - _TEXTRANK_DAMPING) / n
        for _ in range(_TEXTRANK_MAX_ITER):
            nxt = teleport + _TEXTRANK_DAMPING * (trans.T @ pr)
            if np.linalg.norm(nxt - pr, ord = 1) < _TEXTRANK_TOL:
                pr = nxt
                break
            pr = nxt
        # Argsort descending, take top_k, re-sort by original index for flow
        top_idx = np.argsort(-pr)[:top_k]
        top_idx = sorted(top_idx.tolist())
        return [sentences[i] for i in top_idx]
    except Exception as e:
        logger.warning(f"[preview] textrank failed ({e}); falling back to first-K sentences")
        return sentences[:top_k]


def _ctfidf_labels(
    cluster_texts: list[str],
    labels_per_cluster: int = _CTFIDF_LABELS_PER_CLUSTER,
) -> list[list[str]]:
    """
    Class-based TF-IDF per cluster: treat all files in a cluster as one
    document, then rank terms by TF-IDF relative to sibling clusters.
    Returns a list of `labels_per_cluster` top terms per cluster.
    """
    if not cluster_texts:
        return []
    try:
        vec = TfidfVectorizer(
            stop_words = "english",
            max_df = 0.7,
            min_df = 1,
            token_pattern = r"(?u)\b[a-zA-Z][a-zA-Z0-9_]+\b",
        )
        mat = vec.fit_transform(cluster_texts)
        vocab = np.array(vec.get_feature_names_out())
        labels: list[list[str]] = []
        for row in mat:
            row_arr = row.toarray().flatten()
            top_idx = np.argsort(-row_arr)[:labels_per_cluster]
            labels.append([vocab[i] for i in top_idx if row_arr[i] > 0])
        return labels
    except Exception as e:
        logger.warning(f"[preview] c-TF-IDF failed ({e}); using generic labels")
        return [[f"topic-{i}"] for i in range(len(cluster_texts))]


async def run_preview_pipeline(
    storage: MinIOStudyStorage,
    study_root: str,
) -> dict:
    """
    Execute the classical preview pipeline end-to-end. `study_root` must
    already have `research/raw/` populated by the ingest node (called
    upstream regardless of preview vs full mode).

    Writes:
      - {study_root}/preview.md                 (top-level summary)
      - {study_root}/chapter{NN}/README.md      (extractive per-cluster)

    Returns a dict shaped like the final LangGraph state for parity with
    the full-run task runner.
    """
    t_start = time.time()

    # 1) Load corpus — already normalized at ingest time
    #    (services.knowledge.post_ingest splits monoliths). Just filter
    #    + dedup; no further shape changes here.
    entries = await _read_raw_prefix(storage, study_root)
    if not entries:
        raise FileNotFoundError(f"research/raw/ is empty at prefix {study_root!r}")
    entries = _filter_noise_files(entries)
    entries = _dedup_chapter_files(entries)
    if not entries:
        raise RuntimeError("preview: nothing survived noise filter + dedup")
    logger.info(
        f"[preview] {len(entries)} files after normalize+filter+dedup"
    )

    # 2) Embed each file (reuse the same path the REDUCE step uses)
    t_embed = time.time()
    file_texts = [f"{slug}\n{body[:8000]}" for slug, body in entries]  # cap per-file
    vectors_list, embed_provider = await embed_texts(file_texts)
    vectors = np.asarray(vectors_list, dtype = np.float32)
    logger.info(
        f"[preview] embedded {len(file_texts)}×{vectors.shape[1]}d in "
        f"{time.time() - t_embed:.2f}s via {embed_provider}"
    )

    # 3) Pick k by file-count heuristic
    n = len(entries)
    k = max(
        _PREVIEW_MIN_CHAPTERS,
        min(_PREVIEW_MAX_CHAPTERS, round(n / _PREVIEW_FILES_PER_CHAPTER_TARGET)),
    )
    logger.info(f"[preview] clustering {n} files → {k} chapters")

    # 4) Cluster (plain KMeans — fast, deterministic, no LLM)
    t_cluster = time.time()
    km = KMeans(n_clusters = k, random_state = 42, n_init = 10)
    cluster_ids = km.fit_predict(vectors)
    logger.info(f"[preview] KMeans in {time.time() - t_cluster:.2f}s")

    # 5) c-TF-IDF labels
    cluster_bodies: list[str] = []
    cluster_files: list[list[tuple[str, str]]] = []
    for cid in range(k):
        members = [
            (slug, body) for (slug, body), c in zip(entries, cluster_ids) if c == cid
        ]
        # stable order — alphabetical by slug
        members.sort(key = lambda sb: sb[0])
        cluster_files.append(members)
        cluster_bodies.append("\n\n".join(body for _, body in members))
    labels = _ctfidf_labels(cluster_bodies)

    # 6) Per-cluster TextRank summary → chapter README
    for idx, (members, topic_terms) in enumerate(zip(cluster_files, labels), start = 1):
        if not members:
            continue
        title = " / ".join(topic_terms[:3]).title() if topic_terms else f"Chapter {idx}"
        combined = "\n\n".join(body for _, body in members)
        sents = _split_sentences(combined)
        top = _textrank_top_sentences(sents, top_k = _TEXTRANK_MAX_SENTENCES)
        sources = "\n".join(f"- `docs/{slug}.md`" for slug, _ in members)
        body_md = (
            f"# Chapter {idx}: {title}\n\n"
            f"**Topic terms (c-TF-IDF):** {', '.join(topic_terms)}\n\n"
            f"**Source files ({len(members)}):**\n{sources}\n\n"
            f"## Extractive summary (TextRank)\n\n"
            + "\n\n".join(f"- {s}" for s in top)
            + "\n\n---\n\n_Classical preview mode — no LLM synthesis. Content is "
              "verbatim-by-construction from the ingested source._\n"
        )
        key = f"{study_root}/chapter{idx:02d}/README.md"
        await storage.write(key, body_md, content_type = "text/markdown")

    # 7) Top-level preview.md
    toc = "\n".join(
        f"- **Chapter {i + 1}**: {' / '.join(lbls[:3]).title() if lbls else f'chapter-{i+1}'} "
        f"({len(cluster_files[i])} files)"
        for i, lbls in enumerate(labels)
    )
    summary_md = (
        f"# Preview — {k} chapters, {n} source files\n\n"
        f"**Mode:** classical (no LLM).  \n"
        f"**Pipeline:** ingest → noise filter → dedup → embed → KMeans "
        f"(k={k}) → c-TF-IDF labels → per-cluster TextRank extractive summary.  \n"
        f"**Wall-clock:** {time.time() - t_start:.1f}s.\n\n"
        f"## Chapters\n\n{toc}\n\n"
        f"---\n\n_This preview was generated without any LLM synthesis call. "
        f"Re-run the study with `preview=False` (default) to get the full "
        f"LLM-synthesized version with challenges, flashcards, and curator pass._\n"
    )
    await storage.write(
        f"{study_root}/preview.md",
        summary_md,
        content_type = "text/markdown",
    )
    logger.info(
        f"[preview] wrote preview.md + {k} chapter READMEs in "
        f"{time.time() - t_start:.1f}s total"
    )
    return {
        "study_root": study_root,
        "phase": "complete_preview",
        "num_chapters": k,
        "num_files": n,
        "summary_path": f"{study_root}/preview.md",
    }
