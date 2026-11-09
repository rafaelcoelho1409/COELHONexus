You make one compact semantic card for one documentation page. Use only the supplied title, URL and page text.

Return a grounded card:
- `keep` is false only for repository furniture, empty navigation, marketing or unrelated material. Keep API/reference pages; they belong in an appendix.
- `kind` is concept, how_to, tutorial, reference or overview. Use `reference` only when the page is primarily a lookup source such as API signatures, exhaustive option tables, or provider/integration catalogs. Configuration guides, tutorials, feature explanations, protocols, and pages with runnable examples are learning content, even when they discuss APIs. An index page whose main purpose is listing links is overview.
- `topic` is a specific topic in at most eight words. Avoid generic words such as documentation, guide or overview.
- `summary` is one concise sentence about what this page teaches or documents.
- `teaches` has up to three concrete concepts, tasks, APIs or commands actually present in the page.
- `code_role` is none, illustrative, runnable_example or signature_only. Use runnable_example only when the page contains a complete example rather than a fragment.
- `prerequisites` has up to three topics explicitly required before this page makes sense. Do not infer prerequisites from a title alone.
- `reason` briefly explains the keep/drop decision.

Do not write a chapter plan. Do not invent APIs, usage advice, or content missing from the page. Keep every field short.
