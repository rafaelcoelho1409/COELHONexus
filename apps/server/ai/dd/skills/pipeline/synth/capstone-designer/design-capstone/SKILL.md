---
name: design-capstone
description: Chooses and shapes the capstone application that ends a tutorial chapter by combining the code its steps taught, then checks it with the check_code tool. Use for every capstone-designer run, after the chapter's steps are written.
compatibility: Needs the check_code tool. Reads only the JSON in the request.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-07"
---

# Design the capstone

The capstone is the proof that the chapter taught something usable: the reader ends with one program that runs and uses what each step showed. A capstone that uses one step, or adds code no step taught, fails that purpose.

## 1. Read the steps as a build order

- List each step's key API (the function, class or call its example is about). The capstone must use every one that can sit in one program; name any it leaves out and why in your head, not in the answer.
- Look for the dependency the steps already imply: what one step produces another consumes (a store feeds an agent, a tool feeds a loop, a config feeds a run). That chain is the application.

## 2. Choose the application

- Prefer a task a practitioner has: an assistant that remembers a user across sessions, a pipeline that reviews and retries, a service that streams results. Not "a demo of the features".
- If the steps do not form one chain, build the smallest program that exercises all of them in sequence and say so in `intro`; do not invent a story.
- Size: 15 to 120 non-blank lines in all, one to four parts. A reader should build it in one sitting.

## 3. Shape the parts

- Part 1 holds the imports and the setup. Each later part adds one capability and says which step it comes from.
- The last part is the entry point: how to run it, as code. Credentials come from environment variables, never literals.
- Keep the chapter's own names and arguments so the reader recognises the code. Add glue only: wiring, a loop, a print of the result.

## 4. Check, then answer

Call `check_code` with the whole application. Fix what it reports: a parse error, a size outside the limits, a step's API the code never uses. Answer when it says the code is valid.

## Boundary examples

- Four steps teach a store, a tool, a middleware and streaming: one assistant that remembers via the store, calls the tool, is wrapped in the middleware and prints a stream. Not four separate snippets.
- A step's example needs a service the reader may not have: keep it, read its address from the environment, and say so in that part's explanation.
