# Skill: arxiv query shaping

You are constructing the `query` + `categories` + `sort_by` for an
arxiv_search call. Goal: surface the **15-30 most relevant recent papers**
for the operator's topic.

## `query`

- **2-5 words**, NOT a sentence. arxiv's search treats the query like a
  bag of terms.
- **Use the operator's exact topic phrase if it fits** (e.g. `"deep agents"`).
- **Don't quote** unless the topic is genuinely multi-word and would lose
  meaning split (`"linear attention"` quoted is fine; `"new deep learning"`
  is just noise).
- **Avoid stopwords**. "papers about deep agents" → "deep agents".

## `categories` (optional)

Pass the operator's verticals here when they're arxiv categories. arxiv's
common ones for our radar:

| Vertical | arxiv category |
|---|---|
| ML | `cs.LG` |
| AI | `cs.AI` |
| NLP | `cs.CL` |
| Vision | `cs.CV` |
| Stats / ML theory | `stat.ML` |
| Optimization | `math.OC` |
| Quant finance | `q-fin.PR` (pricing), `q-fin.ST` (statistical) |
| Probability | `math.PR` |

If the operator's verticals are NOT arxiv categories (e.g. they're free-text
like "agents"), **omit `categories`** — the search query handles the topic.

## `sort_by`

**RR is a radar — it surfaces what's NEW. `sort_by` MUST default to
`"submittedDate"`. Do NOT use `"relevance"` unless the operator's topic
contains an EXPLICIT relevance signal.**

- **`"submittedDate"`** — newest first. **Use this for every scan unless
  the operator explicitly overrides.** Empirically, `"relevance"`
  surfaces papers from 2018-2020 ahead of papers from 2025 — wrong for
  a radar that exists to flag emerging work.
- **`"relevance"`** — best topical match first. ONLY use when the
  operator's topic contains one of these literal trigger words:
  `"best"`, `"most relevant"`, `"important"`, `"seminal"`, `"survey"`,
  `"foundational"`. Absent these triggers, default to `"submittedDate"`.

If you're unsure, the answer is `"submittedDate"`. The cost of being
wrong toward recency is "the operator sees 1 mediocre 2025 paper"; the
cost of being wrong toward relevance is "the operator sees a 2018 paper
they've already read instead of the 2025 paper that just dropped".

## `n_max`

`30` is the sweet spot. arxiv search is fast; more candidates → better
triage; but past 50 the long tail is noise.

## Failure modes

1. **Quoting a single word**. `query='"agents"'` returns less than
   `query='agents'`.
2. **Passing `categories=['Computer Science']`** — arxiv doesn't know
   that string. Use the abbreviation (`cs.LG`).
3. **`n_max=100`** — fine but wastes triage cycles on low-signal results.
