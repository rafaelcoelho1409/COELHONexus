# Knowledge Distiller — Whole Docs + Variable Tone Design Principle

> **Status:** Design Principle — **validated 2026-04-19** against 2026 SOTA research. See `KNOWLEDGE-DISTILLER-ARCHITECTURE.md` for the canonical implementation.
>
> **Validation:** Nature (Mar 2026) "grade-specific teachers" study and 2026 adaptive-learning research confirm that same-content + variable-presentation beats level-based content-skipping for retention and learning outcomes.

## Core Principle

**Same Content Base, Variable Presentation**

- **NEVER** skip content based on user "level"
- **ALWAYS** ingest the complete official documentation
- **SIMPLY** adjust how that content is presented based on user profile

---

## What Stays Constant

| Aspect | Implementation |
|--------|---------------|
| **Discovery Phase** | Crawl ALL docs URLs via sidebar/sitemap — no skipping sections |
| **Fetcher Phase** | Fetch every page, save to `research/raw/<slug>.md` |
| **Planner Phase** | Assign ALL content to chapters (8-chapter structure) |
| **Source Truth** | Official docs + Context7 + DeepWiki for gaps |

---

## What Varies by User Profile

### Senior Profile (Rafael's Default)

| Dimension | Setting |
|-----------|---------|
| **Code Density** | 70%+ code, minimal prose |
| **Assumptions** | Skip container basics for K8s expert, skip Python patterns for FastAPI veteran |
| **Examples** | Production patterns, edge cases, gotchas |
| **Explanations** | "Use X when Y" not "X is a tool that..." |
| **Market Context** | UAE G42/Stargate deployment specifics, Singapore DBS/Grab patterns |
| **Portfolio Links** | Connects to COELHO RealTime, COELHO Agents, GraphRAG projects |

### Junior Profile

| Dimension | Setting |
|-----------|---------|
| **Code Density** | 40% code, more context |
| **Assumptions** | Explain prerequisites, slower ramp |
| **Examples** | Hello world → progressive complexity |
| **Explanations** | "Here's why this framework exists..." |
| **Market Context** | General best practices, broader patterns |
| **Portfolio Links** | Relate concepts to known projects |

---

## The Compression Source

**4:1 compression (400 pages → 100 pages) comes from:**

1. **Removing redundancy** — Merges 3 explanations of the same concept
2. **Code-first presentation** — 1 code block replaces 5 paragraphs
3. **Smart linking** — "See Chapter 3" instead of repeating
4. **Stripping ceremony** — No "Welcome", "Summary", "What's Next"
5. **Citation-over-explanation** — `# docs: API reference` instead of re-explaining

**NOT from:**
- Skipping topics deemed "too basic"
- Omitting API coverage "seniors already know"

---

## Adaptive Grader Dimensions for Tone

The 8-dimensional evaluation adjusts presentation, not coverage:

| Dimension | Tone Impact |
|-----------|-------------|
| `signal_to_noise` | Remove intros/summaries (senior), keep scaffolding (junior) |
| `assumption_match` | Skip Docker basics (senior), explain containers (junior) |
| `job_alignment` | UAE-specific examples (senior), general production (junior) |
| `citation_integrity` | Same requirement — always cite |
| `code_density` | Target 70% (senior) vs 40% (junior) |
| `portfolio_synergy` | Deep integration (senior), surface connection (junior) |
| `complexity_appropriate` | Calibrate to framework, not user |
| `market_analysis` | Real monetization paths (senior), general money projects (junior) |

---

## Application in Architecture

```python
# Discovery — SAME for all users
manifest = await discovery_agent.crawl_sidebar(url)

# Synthesizer — DIFFERENT prompt per profile
if user_profile.level == "senior":
    synthesizer_prompt += SENIOR_TONE_INSTRUCTIONS
else:
    synthesizer_prompt += JUNIOR_TONE_INSTRUCTIONS

# Grader — Validates presentation quality, not coverage
evaluation = await grader.evaluate(
    synthesis=synthesis,
    target_coverage=ALL_SOURCE_FILES,  # Full manifest required
    tone_requirements=user_profile.tone_spec
)
```

---

## Execution Plan

Deferred to the canonical architecture doc. See [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md) § "Implementation phases" for the step-by-step plan. Tone is encoded in `UserProfile.level` and passed to the synthesizer prompt; grader dimension `signal_to_noise` validates tone adherence without touching coverage.

---

## Key Validation

**Question:** "Does this skip content for junior users?"
**Answer:** No — it explains the same content differently. Coverage is constant.

**Question:** "Does senior mode skip 'basic' APIs?"
**Answer:** No — it presents them with production context, not tutorial exposition.

---

*Document:* `docs/KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md`
*Created:* 2026-04-19
*Validated:* 2026-04-19 against April 2026 SOTA
*Canonical arch:* [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md)
