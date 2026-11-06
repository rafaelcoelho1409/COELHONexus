---
name: analyze-documentation-section
description: Proposes the chapters one section of a framework's documentation supports, from the section's page summaries, covering how many chapters the section needs, where one topic ends and the next begins, when a catalog of same-kind pages is one chapter, and what to leave to the Foundations chapter. Use for every section analysis before the chapter architect merges the sections.
compatibility: Reads only the JSON in the request; needs no tools.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-05"
---

# Analyze a documentation section

You see one section of the site, not the whole corpus. Propose what THIS section teaches; the architect decides how sections combine.

## 1. Find the topics

1. Read every page's `title`, `summary` and `key_terms`. Group pages that teach the same thing: shared identifiers, shared parent page, consecutive pages of one guide.
2. Name the group by the specific feature, command or concept, never by the kind of page ("Tutorials", "API").
3. A group of fewer than 3 pages is not a chapter: attach it to the nearest group, or leave it out when it is an overview or cross-cutting page.

## 2. Size the section

- `target` is a hint from the corpus size, not a quota. A section with one real topic gets one chapter, however large the target.
- Fewer than 6 pages: one chapter.
- A chapter holding more than 30% of the section's pages, or mixing unrelated sub-themes, is two chapters.

## 3. Catalog sections

A section whose pages are all the same kind of page (one per provider, integration, plugin, loader or model) is ONE chapter named after the section. Splitting it by topic scatters pages a reader looks up by name.

## 4. Leave out

Pages whose `role` is `overview` or `cross_cutting`: platform support, debugging, performance, compatibility notes. The planner collects them into a Foundations chapter later. A chapter for them would pull the section's own topics apart.

## 5. Check before answering

- Every chapter has a distinct title (case-insensitive), 1 to 8 words, concrete.
- Every chapter has 3 to 15 concepts that appear in the page summaries, not invented.
- No chapter repeats another's topic.

## Boundary examples

- A section `guides/auth/` with pages on API keys, OAuth and sessions is one chapter "Authentication", unless OAuth alone has 8 pages with its own flows: then "API Keys and Sessions" and "OAuth Flows".
- A section `integrations/` with 12 one-per-vendor pages is one chapter "Integrations", not three chapters by vendor type.
- `faq.md` and `troubleshooting.md` are `cross_cutting`: no chapter.
