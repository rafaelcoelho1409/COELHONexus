# Knowledge Distiller — Classical-Algorithm Optimization Plan

**Date:** 2026-04-25
**Scope:** Every step in the KD pipeline, classified by current implementation, with concrete 2026 SOTA classical replacements (or improvements where already classical).

## Hard constraints (non-negotiable)

1. **Quality-first.** Never sacrifice material quality for speed. If a step requires real LLM judgment to maintain quality, KEEP THE LLM. This document explicitly flags steps where classical replacement WOULD degrade output.
2. **100% free / open-source.** No paid APIs, no commercial models. Permissive licenses only (MIT / Apache-2.0 / BSD). Self-hostable on a single workstation.
3. **Production-ready.** Mature libraries with active 2025-2026 maintenance.

## How "classical algorithm" is defined here

Mathematical/statistical methods (BM25, KMeans, MinHash, c-TF-IDF, AST parsing, NLI classifiers, sentence embeddings) — versus generative LLM calls. Sentence-transformer / cross-encoder / NLI models (small fine-tuned encoders, ~0.1-2B params) count as "classical" in this taxonomy because they are **deterministic discriminative classifiers**, not generative models, and run cheaply on local hardware without rate limits.

---

## Part A — Inventory: every KD step + current implementation

| # | Step | Node / Module | Current implementation | Type |
|---|---|---|---|---|
| 1 | Resolver scope gate | `services/knowledge/scope.py` | Groq llama-3.1-8b LLM "is this a code framework?" classifier | **LLM-only** |
| 2 | Tier 1 ingester | `llms_full_ingest.py` | httpx GET + manifest detection (OP-50) | Classical |
| 3 | Tier 2 ingester | `llms_txt_ingest.py` | parallel httpx + Trafilatura extraction | Classical |
| 4 | Tier 3 ingester | `sitemap_ingest.py` | sitemap.xml + httpx + Trafilatura | Classical |
| 5 | Tier 4 ingester | `ingestion.py` | Crawl4AI Playwright | Classical |
| 6 | Noise filter | `helpers.py::_filter_noise_files` | Regex slug patterns + content-length heuristics | Classical |
| 7 | Code-vault extraction | `helpers.py::_vault_code_blocks` | markdown-it-py AST + sha256[:12] | Classical |
| 8 | Monolith splitter | `helpers.py::_maybe_split_monolith` | top-level heading split | Classical |
| 9 | BM25F file ranking | `helpers.py::_rank_files_by_bm25` | rank-bm25 with 2-field tokenizer (prose 1.0, code 0.3) | Classical |
| 10 | MinHash dedup | `helpers.py::_dedup_chapter_files` | hand-rolled prose Jaccard + code-hash sets | Classical |
| 11 | MAP shard labeling | `distiller.py::planner` (MAP loop) | LLM per-shard "what topics in these 40 files?" | **LLM-only** |
| 12 | REDUCE embeddings | `services/knowledge/embeddings.py` | NIM `nvidia/llama-nemotron-embed-1b-v2` API | **API call (classical-style usage)** |
| 13 | REDUCE PCA pre-reduction | `graphs/knowledge/reduce_cluster.py` | sklearn PCA(128) | Classical |
| 14 | REDUCE UMAP | `reduce_cluster.py` | umap-learn UMAP(5d) | Classical |
| 15 | REDUCE clustering | `reduce_cluster.py` | KMeansConstrained (Clio v2) | Classical |
| 16 | REDUCE meta-cluster names | `reduce_cluster.py` | LLM call per cluster (META_LABEL_PROMPT) | **LLM-only** |
| 17 | REDUCE order | `reduce_cluster.py` | LLM call (ORDER_PROMPT) | **LLM-only** |
| 18 | Token-budget packing | `helpers.py` | tiktoken cl100k_base | Classical |
| 19 | Synth (chapter prose) | `distiller.py::synthesize_chapter` | LLM with Self-Refine (3-7 iters via OP-18) | **LLM-only — KEEP** |
| 20 | Self-Refine adjustment gen | `helpers.py::_generate_adjustment` | LLM call (ADJUSTMENT_PROMPT) | **LLM-only (mostly KEEP)** |
| 21 | Vault sentinel round-trip audit | `helpers.py::_audit_sentinel_roundtrip` | regex + sha256 match | Classical |
| 22 | Structured-output audit | `helpers.py::_audit_structured_output_refs` | hash distribution + thin-section + zero-citation | Classical |
| 23 | Assembled-markdown scrubber (OP-22/36/37) | `helpers.py::_scrub_assembled_markdown` | 5-pass regex pipeline | Classical |
| 24 | Curator | `distiller.py::curator` | LLM per-chapter style normalization w/ vault preservation | **LLM-only — partially keep** |
| 25 | Critic citation_coverage | `distiller.py::critic` | regex `# docs:` against research/raw/* | Classical |
| 26 | Critic faithfulness | `distiller.py::critic` | LLM judges "claim verifiable against source?" | **LLM-only** |
| 27 | Critic code_syntax_valid | `distiller.py::critic` | LLM judges "does this code parse?" | **LLM-only — REPLACE** |
| 28 | Critic deterministic linter | `helpers.py::_deterministic_linter` | heading-depth + code-density + stub-marker checks | Classical |
| 29 | Critic hallucinated-fence scan | `helpers.py::_scan_hallucinated_fences` | sha256[:12] match against vault keys | Classical |
| 30 | Assembler summary.md narrative | `distiller.py::assembler` | LLM call (ASSEMBLER_PROMPT) | **LLM-only — KEEP** |
| 31 | Assembler DEBT.md | `distiller.py::assembler` | per-chapter `debt` flag aggregation | Classical |
| 32 | Grader | `helpers.py::_grade_attempt` | LLM 8-dim eval + audit signals (OP-17) | **LLM-only — KEEP** |
| 33 | Deterministic grader pre-gates | `helpers.py::_deterministic_grader_gates` | length/citation/code-density floors | Classical |

**Summary:** Of 33 steps, **23 are already classical**, **10 use LLM calls**. Of those 10, after this analysis: **3 should be replaced fully**, **3 hybrid** (classical pre-filter + LLM only on borderline), **4 keep LLM** (genuinely irreplaceable).

---

## Part B — Replacement opportunities (ranked by quality-preserving impact)

### Tier 1 — Replace fully (zero quality loss, strict-better outcome)

#### B-1. Critic `code_syntax_valid` — REPLACE (HIGHEST PRIORITY)

**Current:** LLM judges whether each code block parses in its language.

**Replacement:** **`tree-sitter-language-pack`** (PyPI, MIT) — pre-compiled wheels for **305 languages**, unified API. Plus per-language linters for semantic checks.

| Language | Parser (syntax) | Linter (semantics, optional) |
|---|---|---|
| Python | tree-sitter + `ast` | `ruff` (Rust, 100× faster than pylint) |
| JavaScript/TypeScript | tree-sitter | `eslint` |
| Dockerfile | tree-sitter | `hadolint` |
| YAML | tree-sitter | `yamllint` |
| Bash | tree-sitter | `shellcheck` |
| SQL | tree-sitter | `sqlfluff` |
| JSON | `json.loads` | `jsonschema` |
| Go | tree-sitter | `gofmt -e` |
| Rust | tree-sitter | `syn` via subprocess |

**Quality assessment:** A parser is **strictly more reliable** than an LLM at "does this parse?" — the LLM literally cannot do better than the parser; only worse (false positives, hallucinated errors). **Net positive.**

**Code:**
```python
# pip install tree-sitter tree-sitter-language-pack
from tree_sitter_language_pack import get_parser

def is_syntactically_valid(code: str, lang: str) -> bool:
    try:
        parser = get_parser(lang)
        tree = parser.parse(code.encode("utf-8"))
        return not tree.root_node.has_error
    except Exception:
        return False
```

**Effort:** 6-10h, ~150 LoC. Replace `code_syntax_valid` field computation in `_critic_one_chapter` (currently part of OP-45 per-chapter LLM call) with deterministic parse-rate.

---

#### B-2. BM25F retrieval performance — UPGRADE LIBRARY

**Current:** `rank-bm25` with custom 2-field tokenizer.

**Replacement:** **`bm25s`** (Apache-2.0) — Numpy/Numba backed, **100-500× faster** than rank-bm25 on BEIR benchmarks, identical scores. Drop-in for current implementation if you implement field-weighting via score-fusion.

**Quality assessment:** Pure performance upgrade — same scores, much faster. Zero quality risk.

**Code:**
```python
# pip install bm25s
import bm25s
retriever = bm25s.BM25(k1=1.5, b=0.75)
retriever.index(bm25s.tokenize(corpus, stopwords="en"))
results, scores = retriever.retrieve(bm25s.tokenize([query]), k=50)
```

**Effort:** 4h, ~60 LoC.

---

#### B-3. UMAP → PaCMAP swap (REDUCE step)

**Current:** umap-learn UMAP for 128d → 5d projection before KMeansConstrained.

**Replacement:** **`pacmap`** (MIT). Per Nature Sci Reports 2025 benchmark, PaCMAP wins on global structure preservation and is robust to PCA-preprocessing dimensionality (UMAP and t-SNE are not).

**Quality assessment:** PaCMAP preserves cluster boundaries **better** than UMAP for dense-cluster topologies — KMeansConstrained will produce tighter clusters. Same speed. **Net positive.**

**Code:**
```python
# pip install pacmap
import pacmap
reducer = pacmap.PaCMAP(n_components=5, n_neighbors=10, MN_ratio=0.5,
                        FP_ratio=2.0, random_state=42)
reduced = reducer.fit_transform(pca128)
```

**Effort:** 4h, ~10 LoC change in `reduce_cluster.py`.

---

#### B-4. NIM embeddings API → local Qwen3-Embedding-0.6B

**Current:** NIM `nvidia/llama-nemotron-embed-1b-v2` API (cascaded with fastembed fallback).

**Replacement:** **`Qwen/Qwen3-Embedding-0.6B`** (Apache-2.0, June 2025). MTEB Code score **74**. Matryoshka representation lets you output 256-d directly, eliminating the PCA(128) step in REDUCE.

**Quality assessment:** Roughly equivalent or better on technical docs (Qwen3 8B variant scores **80.68** on MTEB Code, the 0.6B is a strong distillation). Eliminates network round-trips and rate limits in REDUCE. **No quality loss.**

**Code:**
```python
# pip install sentence-transformers>=3.3.0
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device="cuda",
                            model_kwargs={"torch_dtype": "float16"})
# Matryoshka — request 256 dims directly, can skip PCA
embeddings = model.encode(docs, prompt_name="retrieval.passage",
                          truncate_dim=256, batch_size=32)
```

**Effort:** 4h, ~30 LoC in `services/knowledge/embeddings.py`.

**Hardware note:** 0.6B model fits in 2GB VRAM. RTX 4060 16GB or any consumer GPU is more than sufficient. CPU also viable (~5× slower).

---

#### B-5. Resolver scope gate — REPLACE (using SetFit)

**Current:** Groq llama-3.1-8b LLM classifier for "is this a code framework?".

**Replacement:** **`huggingface/setfit`** (Apache-2.0) — 8-16 labeled examples per class, **outperforms GPT-3 at 1600× smaller**. Built on Sentence Transformers + LogisticRegression head.

**Quality assessment:** For binary classification with clean signal, SetFit ≥ small-LLM. The Groq 8B is overkill — its true value is structured-output prompt enforcement, not raw classification. Train on 50 historical resolver decisions.

**Code:**
```python
# pip install setfit
from setfit import SetFitModel, Trainer, TrainingArguments
from datasets import Dataset

train_ds = Dataset.from_dict({"text": queries, "label": labels})
model = SetFitModel.from_pretrained("BAAI/bge-small-en-v1.5")
trainer = Trainer(model=model, args=TrainingArguments(num_iterations=20),
                  train_dataset=train_ds)
trainer.train()
# inference
prediction = model.predict(["LangGraph"])  # -> "framework"
```

**Quality tradeoff:** Indistinguishable for 90%+ inputs after labeling 30-50 examples. Falls back to LLM only on ambiguous edge cases (unknown outputs from `predict_proba` < 0.7). **Net safe.**

**Effort:** 4-6h including labeling, ~80 LoC.

---

### Tier 2 — Replace with hybrid (classical pre-filter + LLM only on borderline)

#### B-6. MAP shard labeling — HYBRID via BERTopic

**Current:** LLM per-shard call producing `ShardLabels(clusters=[...], unused_shard_slugs=[...])`.

**Replacement strategy:** **`BERTopic`** with `KeyBERTInspired` representation + c-TF-IDF + MMR for the shard *labels*; LLM only when topic-coherence (`c_v` score) < 0.4.

**Why hybrid:** For shard labels (5-7 keywords describing 40 files), BERTopic matches or beats Groq-8B's output and is fully deterministic. For meta-cluster *narrative names* (`"FastAPI dependency injection patterns"`), LLM still wins on coherent phrasing.

**Quality tradeoff:** ~5-10% noise on micro-cluster boundaries (KMeans groups by semantic similarity, LLM blends pedagogy + semantics). **REDUCE re-clusters globally anyway → noise gets absorbed.** Acceptable, validated by Clio v2 already using this pattern at REDUCE scale.

**Code:**
```python
# pip install bertopic>=0.16.4 keybert
from bertopic import BERTopic
from bertopic.representation import KeyBERTInspired, MaximalMarginalRelevance
from sentence_transformers import SentenceTransformer

emb_model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
representation = [KeyBERTInspired(top_n_words=10),
                  MaximalMarginalRelevance(diversity=0.3)]
topic_model = BERTopic(embedding_model=emb_model,
                       representation_model=representation,
                       min_topic_size=3, calculate_probabilities=False)
topics, _ = topic_model.fit_transform(shard_summaries)
labels = topic_model.get_topic_info()["Name"].tolist()
```

**Effort:** 8-12h, ~120 LoC. Integrate into `_label_shard` with LLM fallback when `c_v < 0.4`.

---

#### B-7. Critic faithfulness — HYBRID via DeBERTa-v3-NLI

**Current:** LLM judges "is each claim verifiable against cited source?"

**Replacement strategy:** **`MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli`** (MIT) NLI classifier as a pre-filter; LLM only on borderline.

**Brutal honesty (this is the hardest call):**
- DeBERTa-v3-NLI gets ~80-85% agreement with GPT-4 judges on FEVER, but **drops sharply on technical/code claims** because NLI training data is news/Wikipedia.
- BERTScore measures *similarity*, not *entailment* — cannot distinguish "this code uses `await`" vs "this code does not use `await`" if surface words match.
- **DO NOT FULLY REPLACE the LLM critic for faithfulness.**

**Hybrid (the right answer):**
1. Run **DeBERTa-v3-NLI** on every (claim, source) pair as pre-filter
2. Pass-through claims with `entailment > 0.85` (no LLM call)
3. Send only `entailment < 0.85` OR `contradiction > 0.3` to LLM critic
4. Expected savings: 60-75% of LLM critic calls on high-quality syntheses

**Quality tradeoff:** Strict pre-filter at high-confidence end → only borderline cases reach LLM, where LLM judgment is most needed. **Net safe + faster.**

**Code:**
```python
# pip install transformers
from transformers import pipeline
nli = pipeline("text-classification",
               model="MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli",
               device=0, return_all_scores=True)
results = nli([{"text": src, "text_pair": claim} for claim in claims])
needs_llm = [c for c, r in zip(claims, results)
             if r["entailment"] < 0.85 or r["contradiction"] > 0.3]
```

**Threshold tuning:** Calibrate on 100 hand-labeled (claim, source, verdict) tuples from past KD runs. Use F1 on the "needs review" class.

**Effort:** 8h + 2h labeling, ~100 LoC.

---

#### B-8. BM25F + Reranking — ADD CROSS-ENCODER RERANK

**Current:** BM25F top-N → directly to dedup → token-budget packing.

**Add:** **`BAAI/bge-reranker-v2-m3`** (568M params, MIT) cross-encoder for top-50 rerank before dedup. Or **`mxbai-rerank-base-v2`** (Apache-2.0) — current OSS SOTA, faster.

**Quality assessment:** Pure precision gain. Cross-encoder relevance scoring catches paraphrased queries that BM25's lexical matching misses. **Net positive.**

**Code:**
```python
# pip install sentence-transformers
from sentence_transformers import CrossEncoder
reranker = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=2048)
pairs = [(query, doc) for doc in top_50_docs]
scores = reranker.predict(pairs)
top_20 = sorted(zip(top_50_docs, scores), key=lambda x: -x[1])[:20]
```

**Effort:** 4h, ~40 LoC. Add to `_rank_files_by_bm25` as optional second pass.

---

### Tier 3 — KEEP LLM (genuinely irreplaceable for quality)

#### B-9. Synth (chapter prose generation) — KEEP

This is the entire product. No 2026 classical method generates pedagogical multi-page prose from heterogeneous sources. The closest classical alternative — TextRank extractive summarization — produces flat sentence-pasting that reads like a TOC, not orientation prose. **`preview.py` already implements this** as a 5-min sketch path; it's not a substitute for synthesis.

#### B-10. Self-Refine adjustment generation — KEEP (mostly)

Requires reasoning over grader feedback. Classical "rule-rewriting" doesn't exist for this task. The `_format_structured_output_feedback` function is ALREADY deterministic; the LLM `_generate_adjustment` is a thin polish on top.

**Possible micro-optimization:** when grader's `specific_issues` are all of one type (all "thin section"), use pure template; only call LLM when issues are heterogeneous. Saves ~30% of adjustment-gen calls. ~30 LoC. Marginal but free.

#### B-11. Curator (style normalization, prose-level) — KEEP

LLM-only for tone unification. Classical text-style-transfer models (e.g., styleformer) are noticeably worse.

**Hybrid recommendation:** Run **`mdformat`** (pure-Python, MIT) **AFTER** the LLM curator, not instead. mdformat handles syntax-level normalization (heading levels, list markers, code-fence languages, link normalization, line-wrap consistency) for free. Curator no longer needs "ensure all bullets use `-`" instructions — strip that from CURATOR_PROMPT, simplifying the prompt and making the LLM call faster + more focused.

**Code:**
```python
# pip install mdformat mdformat-gfm mdformat-frontmatter
import mdformat
formatted = mdformat.text(chapter_md, options={"wrap": 100},
                          extensions={"gfm", "frontmatter", "tables"})
```

**Effort:** 3h, ~40 LoC + prompt simplification.

#### B-12. Assembler summary.md narrative — KEEP

Multi-document orientation prose; no classical equivalent produces the "study-level introduction + chapter graph + cross-reference signposting" narrative. **DO NOT REPLACE.** Maybe 1 LLM call per study — not worth the quality loss.

If you insist: pyTextRank for a "key concepts" appendix is fine, **additive** to the LLM narrative.

---

## Part C — Improvements to currently-classical steps

#### C-1. Sentence segmentation — UPGRADE to blingfire

If you ever need sentence-level processing (required for B-7 NLI faithfulness), **`blingfire`** (MIT, Microsoft Research) is the SOTA: 0.19ms/sentence vs spaCy's 1.24ms, 87% accuracy on GENIA (vs spaCy's 76%). Better handles technical-doc edge cases (numbered lists, abbreviations).

For technical docs with code blocks, the right pattern is:
1. **Strip code blocks first** (already done by `_vault_code_blocks`)
2. Run **blingfire** on remaining prose
3. Re-stitch

```python
# pip install blingfire
from blingfire import text_to_sentences
sentences = text_to_sentences(prose_only_text).split("\n")
```

#### C-2. Token counting — ADD autotiktokenizer

**Current:** tiktoken (OpenAI, MIT) for cl100k_base. Probably **undercounting tokens for non-OpenAI models** (NIM Llama, Qwen, Mistral all use different BPE) by 10-15%.

**Add:** **`autotiktokenizer`** (MIT) — wraps any HF tokenizer.json into tiktoken format, universal counting at tiktoken speed.

```python
# pip install autotiktokenizer
from autotiktokenizer import AutoTikTokenizer
tok = AutoTikTokenizer.from_pretrained("Qwen/Qwen3-32B")
n_tokens = len(tok.encode(text))  # accurate for Qwen target
```

**Effort:** 2h, integrate per-model in `services/llm_chain.py`.

#### C-3. Trafilatura quality scoring — VERIFY current usage

Check that `Trafilatura(favor_precision=True)` is enabled in ingestion config. Add `jusText` (Apache-2.0) fallback for pages where trafilatura returns low-quality output (the OP-51/52/53 batch already addresses this).

#### C-4. Bisecting K-Means (optional, free hierarchical taxonomy)

Sklearn ≥1.1's **BisectingKMeans** produces a hierarchical tree as a side-effect of clustering. Could enable Clio-style multi-level taxonomies in REDUCE for free. Speed parity with KMeans, deterministic.

```python
from sklearn.cluster import BisectingKMeans
bk = BisectingKMeans(n_clusters=k, bisecting_strategy="largest_cluster",
                     random_state=42).fit(reduced)
# tree available via bk._bisecting_tree
```

Optional — only valuable if you want a hierarchical taxonomy layer in the future.

---

## Part D — Implementation order (ranked by quality preservation × ease)

| Order | OP-# | Step | Type | Effort | Quality risk |
|---|---|---|---|---|---|
| 1 | **OP-58** | bm25s migration (B-2) | Replace | 4h | Zero |
| 2 | **OP-59** | tree-sitter code_syntax_valid (B-1) | Replace | 8h | **Strict-better** (parser > LLM) |
| 3 | **OP-60** | mdformat post-curator pass (B-11 hybrid) | Hybrid add | 3h | Zero (additive) |
| 4 | **OP-61** | UMAP → PaCMAP (B-3) | Replace | 4h | Net positive |
| 5 | **OP-62** | bge-reranker-v2-m3 after BM25F (B-8) | Add | 4h | Net positive |
| 6 | **OP-63** | SetFit scope gate (B-5) | Replace | 6h | Minimal (after labeling) |
| 7 | **OP-64** | Local Qwen3-Embedding-0.6B (B-4) | Replace | 4h | Zero/positive |
| 8 | **OP-65** | DeBERTa-v3-NLI faithfulness pre-filter (B-7) | Hybrid | 10h | **Threshold-tuning critical** |
| 9 | **OP-66** | BERTopic shard labeling with LLM fallback (B-6) | Hybrid | 12h | ~5-10% noise (REDUCE absorbs) |
| 10 | **OP-67** | autotiktokenizer for non-OpenAI models (C-2) | Add | 2h | Zero |
| 11 | **OP-68** | blingfire sentence segmentation (C-1) | Add | 2h | Zero |
| 12 | (defer) | BisectingKMeans hierarchical taxonomy (C-4) | Optional add | 4h | Zero |

**Total Tier 1 + Tier 2:** ~73 hours of work, ~700 LoC.

**Net LLM call savings per study run:**
- OP-59: 1 LLM call/chapter eliminated → 8-12 calls/run
- OP-63: 1 LLM call/run eliminated
- OP-65: 60-75% of faithfulness calls eliminated → 5-10 calls/run
- OP-66: N_shards × 1 call eliminated → 20-50 calls/run

**Total: ~35-75 LLM calls eliminated per study, concentrating remaining LLM work on creative/reasoning tasks where it actually adds value (synth, curator prose, assembler narrative, faithfulness borderline).**

---

## Part E — Steps to LEAVE ALONE (irreplaceable for quality)

Strict no-touch list:

| Step | Why no replacement |
|---|---|
| **#19 Synth** | The entire product. No classical method produces pedagogical prose from heterogeneous sources. |
| **#20 Self-Refine adjustment** (mostly) | Reasoning over grader feedback. Possible micro-opt: template-only when issues are homogeneous. |
| **#24 Curator prose normalization** | Tone unification needs LLM. mdformat handles syntax (B-11 hybrid) but NOT prose tone. |
| **#26 Critic faithfulness** (LLM-only verdict) | Hybrid pre-filter (B-7) eliminates 60-75%; final judgment on borderline stays LLM. |
| **#30 Assembler summary.md narrative** | Multi-document orientation prose. ONE call per study. Quality > savings. |
| **#32 Grader** | 8-dimensional weighted evaluation requires real LLM judgment + audit signal integration. |

---

## Part F — Hardware considerations

All Tier 1 + Tier 2 replacements run on **CPU only** OR a single consumer GPU:

| Step | CPU-only viable? | Best with GPU |
|---|---|---|
| tree-sitter parsing (OP-59) | ✅ Pure CPU, milliseconds/block | n/a |
| bm25s (OP-58) | ✅ Pure CPU, NumPy-backed | n/a |
| mdformat (OP-60) | ✅ Pure CPU | n/a |
| PaCMAP (OP-61) | ✅ Pure CPU, 30s typical | optional |
| bge-reranker-v2-m3 (OP-62) | ✅ Slower (~50ms/pair), batched OK | RTX 4060 16GB → 5ms |
| SetFit (OP-63) | ✅ Training in 2 min CPU | optional |
| Qwen3-Embedding-0.6B (OP-64) | ✅ ~5× slower, still viable | RTX 4060 16GB → ideal |
| DeBERTa-v3-NLI (OP-65) | ✅ ~100ms/pair, batched | RTX 4060 16GB → 10ms |
| BERTopic + Qwen embed (OP-66) | ✅ Slow but works | RTX 4060 16GB → fast |

**Recommendation:** Single RTX 4060 16GB ($300-500 used) or RTX 5090 32GB ($2K) makes the entire optimization stack 5-20× faster than CPU. Strictly optional — everything works on CPU.

---

## Part G — Quality preservation justification (the core promise)

For every Tier 1 + Tier 2 replacement, the quality-preservation story is:

1. **OP-58 bm25s:** Bit-exact same scores as rank-bm25, just faster. Zero quality risk.
2. **OP-59 tree-sitter:** Parser output is ground truth. LLM was approximating ground truth. Replacement is **strictly more accurate**.
3. **OP-60 mdformat:** Additive post-pass; no LLM functionality removed. Pure improvement on syntax-level consistency.
4. **OP-61 PaCMAP:** Better global structure preservation per peer-reviewed 2025 benchmark. Net positive.
5. **OP-62 bge-reranker:** Cross-encoder relevance is more accurate than lexical BM25 alone. Pre-existing best practice in modern RAG.
6. **OP-63 SetFit:** Matches/beats small LLM on binary classification per published results. Falls back to LLM on low-confidence.
7. **OP-64 Qwen3-Embedding:** Higher MTEB Code score than current embedding. Eliminates rate limits.
8. **OP-65 DeBERTa-NLI:** Pre-filter ONLY — borderline still goes to LLM. High-confidence cases are easy regardless of judge.
9. **OP-66 BERTopic:** Acknowledged ~5-10% noise on shard boundaries; REDUCE re-clusters globally so noise does not propagate to chapter plan.

**The non-negotiable rule:** For ANY replacement, if real-world output quality measurably degrades (verified by chapter review), we revert that specific OP. The OPs are independent and reversible.

---

## Sources (cited from research agent's deep-dive, 2025-2026)

**Embeddings / MTEB:**
- [Embedding Model Leaderboard MTEB March 2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/)
- [Best Open-Source Embedding Models 2026 (BentoML)](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models)
- [Qwen3 Embedding paper arXiv:2506.05176](https://arxiv.org/abs/2506.05176)
- [NV-Embed paper arXiv:2405.17428](https://arxiv.org/html/2405.17428v3)

**Clustering / Dimensionality Reduction:**
- [Anthropic Clio paper arXiv:2412.13678](https://arxiv.org/html/2412.13678v1)
- [Comparing Python Clustering Algorithms (HDBSCAN docs)](https://hdbscan.readthedocs.io/en/latest/comparing_clustering_algorithms.html)
- [Dimensionality reduction benchmark Nature Sci Reports 2025](https://www.nature.com/articles/s41598-025-12021-7)
- [PaCMAP repo](https://github.com/YingfanWang/PaCMAP)
- [Understanding t-SNE/UMAP/TriMap/PaCMAP JMLR](https://jmlr.org/papers/volume22/20-1061/20-1061.pdf)

**Topic Labeling:**
- [BERTopic GitHub](https://github.com/MaartenGr/BERTopic)
- [BERTopic Representation Models](https://maartengr.github.io/BERTopic/getting_started/representation/representation.html)

**Retrieval / Reranking:**
- [BM25S paper arXiv:2407.03618](https://arxiv.org/html/2407.03618v1)
- [BM25S benchmarks repo](https://github.com/xhluca/bm25-benchmarks)
- [Open-source alternatives to Cohere Rerank 2026 (ZeroEntropy)](https://zeroentropy.dev/articles/open-source-alternatives-to-cohere-rerank/)
- [BGE-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)

**Faithfulness / NLI:**
- [DeBERTa-v3-base-mnli-fever-anli](https://huggingface.co/MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli)
- [RAG Benchmarking RAGAS/BERTScore (Giskard)](https://www.giskard.ai/knowledge/rag-benchmarking-for-ai-evaluation)
- [RAGAS issue #1555 BERTScore](https://github.com/explodinggradients/ragas/issues/1555)

**Code parsing:**
- [tree-sitter-language-pack PyPI](https://pypi.org/project/tree-sitter-language-pack/)
- [tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter)
- [Ruff vs PyLint/Flake8 (PythonSpeed)](https://pythonspeed.com/articles/pylint-flake8-ruff/)

**Style normalization:**
- [mdformat repo](https://github.com/hukkin/mdformat)
- [mdformat-gfm plugin](https://pypi.org/project/mdformat-gfm/)

**Classification:**
- [SetFit repo](https://github.com/huggingface/setfit)
- [SetFit blog post](https://huggingface.co/blog/setfit)

**Sentence segmentation:**
- [PySBD paper](https://aclanthology.org/2020.nlposs-1.15.pdf)
- [blingfire benchmarks](https://github.com/livekit/agents/issues/1811)

**Boilerplate:**
- [Trafilatura evaluation](https://trafilatura.readthedocs.io/en/latest/evaluation.html)
- [jusText repo](https://github.com/miso-belica/jusText)

**Tokenization:**
- [tiktoken vs HuggingFace tokenizers benchmark](https://machinelearningplus.com/gen-ai/tiktoken-vs-huggingface-tokenizers/)

**Deduplication:**
- [text-dedup repo](https://github.com/ChenghaoMou/text-dedup)
- [MinHash LSH in Milvus](https://milvus.io/blog/minhash-lsh-in-milvus-the-secret-weapon-for-fighting-duplicates-in-llm-training-data.md)
