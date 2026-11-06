# Book Distiller: how good are the "needs review" suggestions? (provisional labels, 2026-11-05)

Question: could the AI safely accept every suggestion left in "needs review", given that nobody reads the whole book? Method: the 51 distinct suggestions (page, target, replacement) that sat in review across the last three runs of *The Art of Cyberwarfare* (workflows 1.6, 1.7, 1.8), labeled **by me, one labeler, from the stored suggestion and the evidence, without opening every page**. Treat the numbers as provisional; the export script below produces the same sheet for you to label from any run.

Labels: **C** applying it improves the page without losing anything, **W** applying it would harm the page (wrong guess, deleted or invented content, broken Markdown), **U** unclear or only cosmetic, a judgement call that cannot be verified from the text.

## Result

| Group | Suggestions | C | W | U |
| --- | ---: | ---: | ---: | ---: |
| Already auto-applied by the current gate (space-only repairs) | 8 | 8 | 0 | 0 |
| Newly provable: a stray space and a `\_` escape inside a reference URL (workflow 1.10) | 5 | 5 | 0 | 0 |
| **Left in review** | **38** | **9** | **12** | **17** |

- If the AI accepted everything left, **12 of 38 (32%) would be wrong** and only 9 (24%) are clearly right. The unclear 17 would be applied on trust.
- Confidence does not separate them: **10 of the 12 wrong suggestions came with confidence of 0.9 or higher** (a deleted figure caption at 0.9, an invented page-break marker at 1.0, a fabricated table at 0.9, `ACKNOWLEDGEMENTS` with an added letter at 0.95).
- The wrong ones fall into a few kinds: a letter or word changed (`ACKNOWLEDGEMENTS`, `Russia` → `China`), real hyphens removed (`denialofservice`), a made-up URL (`infosec.institute.com`), content deleted (a caption, the quoted ". . ."), content invented (a table of descriptions, an image path, a `<!-- pagebreak -->`), and broken Markdown (`|---||`).

## Decision

Do **not** build the second-model checker yet, and do not let the AI accept the rest. At 24% clearly right and 32% wrong, no agreement rule between two models is likely to reach the 98% precision bar without throwing away most of the right ones, and the right ones that remain are digit, newline or case edits that no deterministic rule proves safe. What shipped instead:

1. The gate keeps auto-applying only provable edits, now including the URL-escape class above (workflow 1.10; on this data it also clears pages 240 and 251).
2. Everything else stays unapplied, visible on the page as "not applied", with the raw text preserved.
3. Re-open the question only with **your** labels on a fresh run: if the share of right, safely fixable suggestions in review turns out much higher than here, the checker is worth building then.

## Export a sheet to label

```
python3 scripts/server/export_bd_review_labels.py --book BOOK_ID --base-url http://localhost:8000 --sample 50 --out labels.csv
```

Read-only (GET requests only). Fill `label` with `correct`, `wrong` or `cosmetic`. The share of `correct` among the suggestions the gate would apply is the precision to hold against 98%.

## The 51 labeled suggestions

| # | Page | Change | Label |
| ---: | ---: | --- | :-: |
| 1 | 15 | `ACKNOWL EDGMENT S` → `ACKNOWLEDGEMENTS` (adds a letter) | W |
| 2 | 21 | `L ANDSCAPE` → `LANDSCAPE` | C (auto) |
| 3 | 28 | heading `## Russia` → `## China` | W |
| 4 | 37 | `cyber­ attacks` → `cyber-attacks` | C (auto) |
| 5 | 41 | deletes ". . ." from a quoted sentence | W |
| 6 | 48 | drops footnote marker `71` fused into prose | C |
| 7 | 56 | `denial-of-service` → `denialofservice` | W |
| 8 | 56 | `timerequired` → `time required` | C (auto) |
| 9 | 98 | deletes the caption `Figure 3-7: Ransom note comparison` | W |
| 10 | 127 | `trade­ craft` → `tradecraft` | C (auto) |
| 11 | 136 | `engineer- ing` → `engineering` | C (auto) |
| 12 | 138 | `NOT E` → `NOTE` | C (auto) |
| 13 | 148 | `Baddomain#2` → `Bad-domain#2` | U |
| 14 | 155 | heading level `##` → `###` | U |
| 15 | 155 | inserts `<!-- pagebreak -->` | W |
| 16 | 161 | `#1 2 Return-Path` → line break | U |
| 17 | 161 | wraps a header in a code fence and splits lines | U |
| 18 | 163 | caption → image link with a made-up path | W |
| 19 | 166 | caption → image link, drops folio | W |
| 20 | 169 | splits a flattened numbered code listing | U |
| 21 | 169 | deletes an `<!-- image -->` marker | U |
| 22 | 175 | `CVE2010-0249` → `CVE-2010-0249` | C |
| 23 | 189 | removes a stray `1` before `src=` in code | C |
| 24 | 189 | removes a trailing `1` after a URL | U |
| 25 | 195 | adds a blank line | U |
| 26 | 205 | WHOIS fields onto separate lines | C |
| 27 | 208 | `Fax..:` → `Fax:` (may be genuine registry output) | U |
| 28 | 211 | table padding change | U |
| 29 | 211 | table `\| 1 \|` normalisation | C |
| 30 | 211 | table normalisation with a broken `\|---\|\|` | W |
| 31 | 213 | `6` → `[6]` | W |
| 32 | 215 | heading level `###` → `##` | U |
| 33 | 217 | bullet list → table with invented descriptions | W |
| 34 | 222 | `173.194.121 .36` → `173.194.121.36` | C (auto) |
| 35 | 222 | IP list split one per line | U |
| 36 | 222 | moves folio `12` out of a caption | U |
| 37 | 239 | `## ENDNOT ES` → `## Endnotes` | C |
| 38–42 | 240, 241, 251 | stray space plus `\_` escape inside five reference URLs | C (new auto) |
| 43 | 254 | `offensesrelated-2016-election` → `offenses-related-…` | U |
| 44 | 256 | `infosec institute` → `infosec.institute` (wrong domain) | W |
| 45 | 260 | `https:// www.devontechnologies.com` | C (auto) |
| 46 | 260 | `wire shark` → `Wireshark` | C |
| 47 | 268 | index table → bullet list | U |
| 48 | 269 | `DomainTools, 162 162` → `162` | C |
| 49 | 271 | splits fused index entries | U |
| 50 | 272 | merges index table cells | U |
| 51 | 275 | drops a duplicated phrase on the back cover | C |

Run: the same book in all three runs, so repeats of one defect across runs are counted once.
