# Operator Profile

This file persists across scans. It captures the operator's interests +
ranking-weight overrides + accumulated "what I'm working on" context. Update
it manually as your focus evolves; the agent reads it at scan start.

## Identity

- **Operator**: Rafael Coelho
- **Background**: Math BSc (UFPR); 10+ years ML / data engineering
- **Current focus** (2026-06): DeepAgents + FastMCP mastery for monetizable
  AI agent products. Re-studying math to sharpen ML intuition.

## Active verticals

In rough order of priority — the higher up, the heavier the
`signal_score.vertical_fit` weighting:

1. **LLMOps + agent orchestration** — DeepAgents, FastMCP, langgraph,
   bandit routing, structured outputs. Anything that helps build
   production-grade agents on free-tier infra.
2. **Reasoning models + tool-calling reliability** — extended-thinking,
   reasoning_content handling, structured output enforcement.
3. **Quantitative finance + ML for trading** — q-fin.PR, q-fin.ST. Real
   trading or simulated; both count.
4. **Applied mathematics** — math.OC (optimization), math.PR (probability).
   Especially when paper has clear ML applicability.
5. **AI security + EASM (private back-pocket)** — passive OSINT, supply
   chain, agentic pentesting. Not the main builds but flag interesting
   work.

## Signal-weight overrides (vs default `SignalWeights`)

These tilt the radar's ranking from the defaults:

| Weight | Default | Override | Rationale |
|---|---|---|---|
| `relevance` | 0.30 | 0.40 | Embedding-vs-profile cosine should dominate once embeddings land. |
| `cross_tier_buzz` | 0.10 | 0.02 | HN upvotes ranked off-topic posts first; downweight aggressively. |
| `has_code` | 0.05 | 0.10 | Builder lens: code-having papers are more valuable for portfolio reuse. |

## Things I'm NOT interested in

- Pure benchmarks without methodological insight ("we beat X on
  GLUE by 0.3 points")
- Surveys that don't propose a new framework
- Application-only papers in domains unrelated to the verticals above
  (e.g. medical imaging, materials science) unless the methodology has
  clear cross-applicability.

## Open questions I'd like the radar to surface

- "Has anyone shipped a 4-bit quantized DeepAgents-style agent that
  beats a 16-bit single-shot LLM at agent benchmarks?"
- "Are there papers using Kalman filters / state-space models for
  market microstructure?"
- "Constrained decoding alternatives that don't blow up with tool-calling
  schemas (the current state-of-art chokes on `additionalProperties`)?"

## Themes I've already covered

- (Synthesis will populate this from `themes_seen.md` so the radar can
  flag genuinely NEW themes vs ones the operator has already digested.)
