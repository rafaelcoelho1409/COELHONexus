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

- **`"submittedDate"`** (default) — newest first. Right answer when
  topic contains "recent", "new", "latest", or the operator didn't specify.
- **`"relevance"`** — best topical match first. Use when the operator
  explicitly says "best", "relevant", "important".

## `n_max`

`30` is the sweet spot. arxiv search is fast; more candidates → better
triage; but past 50 the long tail is noise.

## Failure modes

1. **Quoting a single word**. `query='"agents"'` returns less than
   `query='agents'`.
2. **Passing `categories=['Computer Science']`** — arxiv doesn't know
   that string. Use the abbreviation (`cs.LG`).
3. **`n_max=100`** — fine but wastes triage cycles on low-signal results.
