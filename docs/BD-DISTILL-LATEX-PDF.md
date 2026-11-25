# BD Distill LaTeX PDF renderer

The Learning Guide PDF endpoint renders the validated `StudyGuideDocument` as a
semantic Pandoc document, writes an intermediate `.tex` file in a private
temporary directory, then compiles it twice with LuaLaTeX so the table of
contents and references settle. The existing HTML report remains an independent
export.

## Report content

The LaTeX report includes the book thesis, decision brief, external research,
whole-book takeaways and connections, every chapter's lessons and exercises,
book figures, generated diagrams and charts, timelines, maps, equations,
glossary, source-page evidence, review notes, and uncovered-page information.
Text enters Pandoc as structured plain-text nodes. Equations use an allowlisted
math subset; unsupported TeX is included as literal text. Existing vector
visuals are converted to PDF, and validated book figures remain PNG assets.

## Runtime requirements

The server image contains the pinned Pandoc binary, LuaLaTeX, recommended TeX
packages and fonts, and `rsvg-convert`. PDF generation is lazy: Distill does not
spend time compiling a PDF unless the user requests one. The first request after
a renderer upgrade regenerates a PDF under a renderer-versioned object key;
the key includes a fingerprint of Pandoc, LuaLaTeX, librsvg, and the installed
TeX packages. Later requests return the integrity-checked cached artifact. Bump
`rendering/latex/versions.py` when the output-building code changes.

The compiler runs without shell escape, uses only local report assets, runs in a
private temporary directory, and has input, asset, output, concurrency, and
time limits. Pandoc is pinned with architecture-specific SHA-256 checks in
`Dockerfile.server`.

This PDF is not currently advertised as PDF/UA or tagged-accessible output.
