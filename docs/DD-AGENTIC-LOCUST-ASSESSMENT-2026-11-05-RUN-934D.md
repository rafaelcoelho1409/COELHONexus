# DD Agentic AI — Locust run 934d assessment (2026-11-05)

Run `934d8d19510a4c41ab095ec44aac2394`, slug `locust`, namespace `coelhonexus`.
Result: **completed all 24 pages**, in about 395 s (≈6.6 min): the first input was frozen at 21:04:31 and the manifest published at 21:11:06 UTC. The worker pod that ran it has been replaced, so there is no worker log; this report comes from the run's MinIO artifacts, their timestamps and the LLM usage snapshot. It follows [run 9064](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-RUN-9064.md).

## Run and LLM usage

| Measure | This run | Run `9064b4…` |
|---|---:|---:|
| Outcome | 24 / 24 | 24 / 24 |
| Duration | ≈395 s | 193.8 s |
| LLM calls (answered) | 79, all auditor | 51 (42 audit, 9 repair) |
| Calls per audited page | 3.3 | 1.75 |
| Input / output tokens | 511,262 / 18,170 | 236,680 / 9,928 |
| Errors | 4: one 504, one timeout, two 500s | 1 (504) |
| Share of audit calls served by `laguna-xs-2.1` | 50 of 79 (63%) | 5 of 42 (12%) |
| Audit / repair unavailable | 0 / 1 | 0 / 0 |

Audit calls by model: laguna-xs-2.1 50, gemma-4-31b 15, ising-calibration-1.5-31b 6, step-5-preview 3, xing4.0-29b 3, gpt-oss-120b 1, gemini-2.5-flash-lite 1.

The run took about twice as long as the last one for two reasons:
- **Pool draw.** Laguna served 63% of the audit calls (it is the model that answers with fenced JSON and reads the skill file more than once), so audits took 3.3 calls each instead of 1.75, and carried twice the input tokens.
- **A repair tail.** The audit phase ended around 21:08:02, and the run then stayed open about three more minutes for page 1's repair.

## The first live repair failure

Page 1 had one finding eligible for repair (`go to installation` with no link destination, confidence 0.9). Its repairer failed on all attempts with an upstream outage, not a bad answer:

```
OpenAIAPIError: Error code: 500 … Nvidia_nimException - Connection error … LiteLLM Retried: 18 times
```

The fix from the [first failed-run report](DD-AGENTIC-LOCUST-ASSESSMENT-2026-11-05-FAILED-RUN.md) held: the page kept its audited original, the saved record says `repair.status: unavailable` with the reason, and the other 23 pages were unaffected. Before that change this would have been a failed run. The cost was the time: the repairer spent its full three attempts, with backoff, on a provider that was down.

## Audit quality

6 findings on 2 pages (a seventh and an eighth were discarded because their evidence was not a source substring): 1 `markdown_formatting` at 0.9, 1 `malformed_heading` at 0.85, 4 `malformed_heading` at 0.8. Zero patches were proposed or applied.

- **Page 7, four findings: false positives.** They flag `#### NOTE` headings with the description "Headings should not be followed by a blank line", which contradicts the source (there is no blank line). The ids (`note_heading_1`) do not follow the id scheme either. They are at 0.8, so they never reached the repairer, but they show laguna ignoring the prompt rule about label headings and blank lines.
- **Page 1, two findings: converter artifacts.** The unlinked `go to installation` and the lost `history` heading are real defects of the source conversion, but neither has an unambiguous fix: the first needs a destination the repairer is told never to guess.
- **Titles: all 24 clean.** Page 21 now reads `aiolocust / asyncio`; the title-marker fix from the previous report is confirmed live.

## Changes applied

- **Fewer tries for a repair** (`page_repair/params.py: MAX_ATTEMPTS = 2`, used by `repair_page`): the repair is optional, so it no longer shares the audit's three attempts. A failing provider now holds the run open for two attempts instead of three. The offline check asserts both the new limit and that it is below the audit's.
- **Removed "Dismiss run" from the Documentation extraction card** (web). The button only dropped `?run=` from the address (`router.replace`); it changed nothing on the server. A finished run now stays on screen until you leave it, and the card keeps its status line ("Ingestion failed." and so on). Opening the Ingestion tab again or picking another framework clears the run, and Start, Retry and Wipe are unchanged. The props, the handler and the test step that clicked it are gone; the tests now assert the button is absent.
- **Checks**: `test_dd_page_agents_retry.py` passes (42 cases), the web type check, knip and the Ingestion and DD page tests pass.

## Not changed, follow-up

- **Which model answers is the pool's call.** Two of the last three runs were dominated by one model that produces most of the false positives and the extra tool calls. Nothing in this repository changes that.
- **Skill reading doubles the audit calls.** Each audit spends one call reading the skill file (laguna sometimes two). Putting the skill text in the prompt would cut calls roughly in half, but how the agents use skills is your design choice.
- **No per-call timing is kept.** The usage snapshot has counts and tokens but not durations, so the audit tail (page 4's audit alone took about three and a half minutes, 21:04:31 to 21:08:02, while most others finished within two) cannot be explained from artifacts. Recording the call's wall time per page would settle it.
- **Next run:** check that a failing repair provider now costs at most two attempts, and compare calls per page against the 1.75 of run 9064 under a different pool draw.
