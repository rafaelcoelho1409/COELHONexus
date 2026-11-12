"""corpus_normalize — normalizer cache-invalidation version. Bump on any
pass change."""
from __future__ import annotations


# v2 (2026-09-29): 9th pass strips static-site permalink debris
# (`[¶](#…)` / `[](#…)`). Recorded only; no consumer gates on it yet.
# v3 (2026-10-03): that pass also strips Private-Use-Area icon-glyph
# permalinks (Sphinx RTD theme's U+F0C1 headerlink).
# v4 (2026-10-03): new pass hoists fences nested in Sphinx definition-list
# bodies (4-space indented, invisible to CommonMark/the vault) to column 0.
# v5 (2026-10-26): Mintlify attrs are stripped from fence openers inside blockquotes/callouts too (`> ```bash theme={…}`
# kept them); new pass drops `<video>`/`<audio>` elements that lost their `src`.
# v6 (2026-10-26): new pass drops the block quote some hosts put before every page for agents ("> ## Documentation Index
# … /llms.txt", Qdrant's "> Explore … agent skills catalog … Use this file for an index …"); react.dev's components
# (`<Intro>`, `<Sandpack>`, `<Pitfall>`…) are stripped like the other MDX wrappers, and `{/* … */}` MDX comments go; the
# `#`-prefixed licence header Hugging Face's docs builder leaves at the top of some pages is dropped.
# v7 (2026-10-27): the licence header is also taken when its closing `-->` ends the last `#` line (57 Diffusers pages kept
# it, and a split cut their children out under the title "Licensed under the Apache License"); Hugging Face's heading
# anchors (`## AutoModel[[transformers.AutoModel]]`) go from headings; a page whose title is only in its frontmatter gets
# it as an H1 (react.dev: no page had one) and TOML `+++` frontmatter is extracted like YAML's; react.dev's `## Sitemap`
# footer goes; more react.dev components are stripped; a JSX `<img>` becomes a Markdown image.
# v8 (2026-10-27): react.dev's pages for HTML elements (frontmatter `link: "<link>"`) get their H1 from that too.
# v9 (2026-10-27): the empty permalink Mintlify puts in front of a heading (`## [](#overview) Overview`) and a `[#](#anchor)`
# link ending a line go; a horizontal rule ending a page goes; a `# [!code highlight]` annotation ends no line of code.
# v10 (2026-10-27): the "Bash ![Copy code to clipboard](…) Copy" line Grafana's markdown prints above each code block goes.
# v11 (2026-10-27): a `[#](#anchor "Link to this heading")` permalink (pydata-sphinx-theme: OpenVINO) goes with its title; Sphinx's
# `[[source]](…)` viewcode link goes; a table that is only links to places in the page (nginx's table of contents) goes.
# v12 (2026-11-07): the "[Connect these docs](…) … via MCP" and "[Edit this page on GitHub](…) or [file an issue](…)" lines Mintlify prints under
# every page go (70 of 70 pages of the LangChain site), and a trailing `***` or `___` rule goes like a `---` one. The agentic ingestion
# runs this normalizer on every page before its audit (it did not between 2026-11-03 and now, so the banner, footer and fence attributes
# of every Tier 2-4 page stayed).
# v13 (2026-11-08): strip non-whitespace Unicode control characters (`Cc`) before page audit and record the count for the page quality report.
# v14 (2026-11-11): add the missing separator after a leading GitBook emoji shortcode in a heading.
# v15 (2026-11-12): repair the exact `llama.cpphttps://github.com/ggml-org/llama.cpp` URL corruption found in Unsloth's full bundle.
# v16 (2026-11-12): restore method headings/source links for the exact concatenated API-export form seen in Optimum's llms.txt pages.
# v17 (2026-11-12): turn three-or-more unambiguously indented label/description pairs into a readable Markdown list.
NORMALIZER_VERSION = 17
