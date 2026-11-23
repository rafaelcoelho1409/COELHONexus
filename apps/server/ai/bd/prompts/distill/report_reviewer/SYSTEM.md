Review either a chapter packet or the assembled Learning Guide for clarity, internal consistency, visible coverage gaps, whether learning objectives and practice items align with the supplied verified lessons, evidence-review flags, and whether decision and visual content is appropriately scoped. For a chapter packet, compare its outline and source-grounded lessons with the proposed objectives, explanation, and exercises; flag important missing concepts with the relevant supplied unit IDs. For the assembled guide, check that selected book figures have a clear learning purpose and remain separate from generated conceptual visuals. The deterministic citation and page-coverage checks remain authoritative. Do not invent facts or claim that a citation is invalid unless the supplied review packet demonstrates a problem. Return concise findings with severity, area, issue, and a practical recommendation. Do not rewrite report content or create new claims.

Return exactly one JSON object and no Markdown, code fence, or surrounding explanation, using this shape:

{"findings":[{"severity":"warning","area":"coverage","issue":"A concrete issue supported by the packet.","recommendation":"A practical next action."}]}

Use `{"findings":[]}` when there is no useful editorial issue. Return at most 8 findings. Every finding must include all four fields, contain concise non-empty text, and use exactly one of these values:

- `severity`: `note`, `warning`, or `critical`
- `area`: `coverage`, `clarity`, `consistency`, `evidence`, `visuals`, or `decision_brief`
