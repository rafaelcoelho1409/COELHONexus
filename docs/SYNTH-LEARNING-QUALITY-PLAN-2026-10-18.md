# Synth: from "accurate reference cards" to material you learn from (plan, 2026-10-18)

Status: PLAN ONLY. Do it after the reliability loop converges (see memory `project_synth_convergence_loop_2026_10_17`).

## Goal

Planner + Synth read hundreds of doc pages and must deliver "chewed" material that lets the reader learn fast **without losing learning quality**.
Two halves: (1) nothing important lost, little time spent; (2) the text actually teaches.

## Where we are (assessed 2026-10-18 on finished chapters)

Half (1) is largely met:
- every source is cited (fastmcp ch-01 18/18, asyncio ch-01 23/23, deepagents ch-05 8/8), code is verbatim, no duplicated blocks, 12 deterministic gates + 5 LLM criteria pass;
- 5 to 30 min per chapter (asyncio ch-04 277 s, fastmcp ch-01 16 min, deepagents ch-05 29 min).

Half (2) is not met. Read asyncio ch-02 and fastmcp ch-01: each section is **a one-paragraph description that mostly narrates the code, followed by the code block**.
Missing for a learner:
- the mental model / why it works that way;
- when to pick X over Y (e.g. `start_server` vs `create_server`, which client to install into);
- pitfalls and gotchas the docs mention in passing;
- order: prerequisites -> basics -> advanced; a learning path;
- a chapter overview and a "key takeaways" recap; optional check questions.

The metrics do not see this: the judge rubric is coherence, grounding, terminology, code-first, code introduced in prose. None measures teaching value, so "17/17" does not mean "best to learn from".

Known content losses (separate, planner-side): chapters with very many blocks lose most code silently. deepagents ch-05 holds 290 blocks in 8 pages and prints 57 (20%); 147 blocks were routed to no section (the digest model lists only some hashes of an 80-block page) and no gate counts them.

## Proposal: a grounded teaching layer (all of it derived ONLY from the source pages)

1. **Digest extracts teaching facts** (new, per source and section): `why` (rationale/mental model), `when` (choose X over Y), `pitfalls` (warnings, caveats, "note/warning/deprecated" callouts, version limits), `prerequisites`. Today the digest keeps only `summary` + `key_facts` + `code_refs`.
2. **Writer prompt** per subtopic: one sentence of purpose, then the code, then "what to watch out for" **only when a pitfall fact exists** for it. No invented guidance: every added sentence must cite a digest fact (existing grounding gate applies).
3. **Chapter frame** (new node or part of render): overview (what you can do after this chapter, prerequisites), a short learning path over the sections, and "key takeaways" (5 to 8 bullets) built from section facts.
4. **Learnability judge** (new LLM criteria, run with the second-opinion majority already in place): explains why not only what; says when to choose; flags pitfalls where the sources do; has a sensible order; recap present. Deterministic gates: overview present, takeaways present, no section that is only a code block.
5. **Heavy chapters**: split a planner chapter whose sources hold more blocks than its sections can print (blocks > about 12 sections x 16) instead of dropping code; route the digest's unlisted blocks (nearest-neighbour routing was simulated on deepagents ch-05 and piled 77 to 80 blocks onto two sections, so it only makes sense together with this split).

## How to know it works

- Pick 3 chapters (fastmcp ch-01, asyncio ch-03, deepagents ch-05). Blind A/B read of old vs new output: which would you rather learn from, and time to answer 5 questions per chapter from the text only.
- Keep the existing gates green (grounding, coverage, audit); add: every teaching sentence traces to a digest fact (spot-check 20 per chapter).
- Length budget: material may grow, but the recap must keep a chapter skimmable (takeaways at top and bottom).

## Risks

- Hallucinated advice. Mitigation: facts must come from the digest, judged by the grounding criterion; no fact -> no sentence.
- Longer prompts and more calls -> more 504s on the free-tier Rotator. Mitigation: extraction rides on the existing per-source digest call (one call), recap is one call per chapter.
- Judge noise (one model failed 5/5 on text others passed 5/5). Mitigation: majority of three already added (`_judge_second_opinion`).

## Order of work

1. Finish reliability convergence (two clean passes on fastmcp ch-01, asyncio ch-04, deepagents ch-05, plus the full asyncio study).
2. Item 1 + 2 (digest facts, writer) on one chapter, read it, iterate.
3. Item 3 + 4 (frame, judge), then the A/B read.
4. Item 5 (planner split) for heavy chapters.
