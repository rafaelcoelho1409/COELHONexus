# FastHTML refactor — remaining steps (2026-06-05)

**Status:** 22 extractions shipped this session; codebase is past the "comfortable for next edit" threshold (every monolith < 500 LOC). 5 small follow-ups remain. **Hard commitment: no more refactor work after these 5 without a feature/bug forcing function.**

---

## State at end of session (2026-06-05)

```
js/dd/
├── planner/   canvas.js  drawer.js  graph.js  lifecycle.js  planner.js
│              polling.js  polling_deps.js  renderers.js  shared.js
├── synth/     canvas.js  chstrip.js  chstrip_deps.js  graph.js
│              lifecycle.js  polling.js  polling_deps.js  shared.js  synth.js
├── study/     chapters.js  flashcards.js  flashcards_deps.js  readme.js
│              shared.js  sidebar.js  study_deps.js  study.js
└── shared/
    ├── library/    recovery.js  sidebar.js  (+ library.js orchestrator)
    └── renderers/  code_copy.js  init.js  lazy_observer.js  mermaid.js  terminal.js
```

| Monolith | Pre-reorg | End-of-session | Δ |
|---|---|---|---|
| `planner/planner.js` | 2376 | 433 | −82% |
| `synth/synth.js` | 2230 | 310 | −86% |
| `study/study.js` | 998 | 476 | −52% |

All `node --check` clean (63 JS files). All CSS brace-balanced (27 files). All Python AST-clean (59 files).

---

## The 5 remaining steps (ordered)

### 1. Extract `_loadStudyChallenges` → `study/challenges.js`
- **Where:** `study/study.js` inline at ~line 243.
- **Pattern:** same shape as `study/readme.js` (sibling, imports `_loadStudyArtifact` from `study/shared.js`).
- **Effort:** 30 min.
- **Cross-refs:** none beyond `_loadStudyArtifact` (in shared.js).

### 2. Extract search subsystem → `study/search.js`
- **Where:** `study/study.js`, the bottom block (`_buildSearchIndex`, `_runSearch`, `_ensureSearchOverlay`, `openSearch`, `closeSearch`).
- **Effort:** 45 min.
- **Cross-refs:** `loadStudyChapters` — already in `study_deps.js` via the existing DI registration. Wire `_buildSearchIndex` through the same registry.

### 3. Audit `shared/ui.js` (339 LOC)
- **Possible natural splits:** toast/notice helpers, drawer wiring (`openDrawer`), cross-stage blocker logic (`refreshCrossStageBlocker`, `crossStageBlockerFor`).
- **Effort:** 30 min audit + 0–2 hr extraction if warranted.
- **Likely outcome:** 1–3 file split OR keep as-is (borderline size).

### 4. Audit `shared/stagegraph.js` (301 LOC)
- **Possible natural splits:** Cytoscape stylesheet declarations vs interaction wiring vs node-status logic.
- **Effort:** 30 min audit + 0–1 hr.
- **Likely outcome:** stays as one file (Cytoscape wrappers tend to be cohesive).

### 5. Audit `shared/srs.js` (216 LOC)
- **Effort:** 15 min audit + 0 hr expected.
- **Likely outcome:** stays as one file (FSRS algorithm is a single responsibility).

### Realistic shippable count: **2–4 items** (#1, #2 definite; #3–#5 may produce 0–2 extractions each).

---

## Patterns proven this session

### A. Per-function grep + brace-counting extraction (SAFE)
```python
def extract_function(lines, name):
    pat = re.compile(rf"^(export\s+)?(async\s+)?function\s+{re.escape(name)}\s*\(")
    start = next((i for i, ln in enumerate(lines) if pat.match(ln)), None)
    if start is None: return None
    depth, in_func = 0, False
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{": depth += 1; in_func = True
            elif ch == "}":
                depth -= 1
                if in_func and depth == 0: return (start, i + 1)
```
Always prefer this over line-range extraction. Line ranges cut functions mid-body — proven bug in this session.

### B. DI registration pattern (for cross-refs that would cycle)
Files: `{stage}_deps.js` with mutable `deps` object + `registerXyzDeps(obj)` function:
```js
// foo_deps.js
export const deps = { pollFoo: null, refreshFooStartState: null };
export function registerFooDeps(obj) { Object.assign(deps, obj); }

// foo.js (the extracted module)
import { deps } from './foo_deps.js';
deps.pollFoo?.(tid);  // optional chaining for safety

// parent.js (registers at module init, after the functions are defined)
import { registerFooDeps } from './foo_deps.js';
registerFooDeps({ pollFoo, refreshFooStartState });
```
Works because module evaluation completes before any user event fires. Safe by JS spec.

### C. Getter pattern (for shared mutable module state)
Used in `renderers/init.js` for `_mermaid` / `_renderMathInElement`:
```js
// init.js
let _mermaid = null;
export function initContentRenderers() { /* mutates _mermaid */ }
export function getMermaid() { return _mermaid; }

// lazy_observer.js
import { getMermaid } from './init.js';
if (getMermaid()) { try { getMermaid().run({ nodes: [el] }); } catch (_) {} }
```
Use when the state is set lazily and consumers in sibling files need deferred access.

### D. Re-export shim (for backward compat)
After extracting `f`, `g`, `h` from `parent.js` to `child.js`:
```js
// parent.js
export { f, g, h } from './child.js';
import { f, g } from './child.js';   // also import what parent.js still needs internally
```
Lets `main.js` keep using `import { f } from './parent.js'` unchanged.

---

## Anti-patterns (proven failures this session)

### ❌ Line-range extraction (`lines[164:364]`)
Step 1 of the second pass cut `_renderPlannerGraph` mid-body. node --check passed but runtime broke (duplicate exports + unclosed function). **Always use per-function brace counting.**

### ❌ Phase H regex rewrite over comments
The `\bS\.(\w+)` regex used to rewrite state references also matched inside `//` comments. Resulted in comments like `// chapter Si.sidebar` that look wrong. Acceptable cost, but worth flagging — comment-only artifacts surfaced 6× in this session and were fixed manually.

### ❌ Extracting blocks with module-private state references mid-body
The orphan `let _synthRunStartMs = 0;` in `synth/graph.js` (left over from earlier extraction) referenced from `synth.js` would have failed at runtime. Always check: does the extracted block reference any `let`/`const`/`var` at module scope that wasn't extracted with it?

---

## Hard commitment after the 5 ship

**Do not propose more refactor work without a forcing function.** The codebase has reached a comfortable size threshold:
- All monoliths < 500 LOC
- Every stage folder follows a consistent template (entry + drawer + canvas + polling + lifecycle + shared + deps where needed)
- DI pattern documented; future cross-cycle extractions reuse the same shape

If after these 5 a future session finds more "refactor opportunities" without a tied feature/bug, push back: that's over-engineering, not real incompleteness.

---

## Cross-file dependency map (current state)

```
main.js
 ├── stage/<x>.js  (entry / orchestrator + re-exports)
 │    ├── stage/canvas.js
 │    ├── stage/drawer.js          (planner only — NodeDrawer IIFE)
 │    ├── stage/graph.js
 │    ├── stage/polling.js  ←──┐
 │    ├── stage/polling_deps.js  │  DI registry (mutated by orchestrator)
 │    ├── stage/lifecycle.js  ←──┤
 │    ├── stage/renderers.js     (planner only — SUBSTEP_RENDERERS)
 │    ├── stage/chstrip.js       (synth only — DI'd)
 │    ├── stage/chstrip_deps.js  (synth only)
 │    ├── stage/sidebar.js       (study only — DI'd)
 │    ├── stage/chapters.js      (study only — DI'd)
 │    ├── stage/flashcards.js    (study only — DI'd)
 │    ├── stage/flashcards_deps.js
 │    ├── stage/readme.js        (study only)
 │    └── stage/shared.js        (pure helpers: _fieldPresent, storage keys, etc.)
 │
 └── shared/
      ├── library.js  (entry + re-exports)
      │    ├── library/recovery.js
      │    └── library/sidebar.js
      ├── content_renderer.js  (entry + re-exports)
      │    └── renderers/{code_copy,init,lazy_observer,mermaid,terminal}.js
      ├── ingestion.js  (re-export shim only — 22 LOC)
      │    ├── ingestion/manifest.js
      │    └── ingestion/polling.js
      ├── state/{api,catalog,ingestion,overlays,planner,synth,study}.js
      ├── stores/{pipeline,study,sse,index}.js
      └── {ui,nav,utils,timing,srs,stagegraph,framework_picker,content_renderer}.js
```

---

## Verification commands (paste-ready)

```bash
# JS syntax check across the tree
find /home/rafaelcoelho/Workbench/COELHONexus/apps/fasthtml/static/js -name "*.js" \
  -exec node --check {} \; 2>&1 | grep -v "^$"

# Brace balance audit (catches mid-body cuts that node --check misses)
for f in $(find /home/rafaelcoelho/Workbench/COELHONexus/apps/fasthtml/static/js -name "*.js"); do
  o=$(tr -cd '{' < "$f" | wc -c)
  c=$(tr -cd '}' < "$f" | wc -c)
  [ "$o" != "$c" ] && echo "BROKEN: $f ($o open, $c close)"
done

# Audit for Phase H comment artifacts (Sa/Sc/Si/So/Sp/Sy/Ss inside //)
python3 -c "
import re
from pathlib import Path
ROOT = Path('/home/rafaelcoelho/Workbench/COELHONexus/apps/fasthtml/static/js')
for f in ROOT.rglob('*.js'):
    for i, ln in enumerate(f.read_text().splitlines(), 1):
        if re.search(r'//.*?\bS[acispyo]\.(\w+)', ln):
            print(f'{f}:{i}: {ln.strip()[:100]}')
"
```
