"""SAWC — pure helpers: identifier/code-alignment extraction, structural
validation + scoring, prompt builders, vault-hash dedup, JSON/Pydantic
parsing, and manifest hashing. No I/O, no LLM calls, no logging — see
service.py for the orchestration (LLM drafting, critic pick, pairwise
judge, MinIO persistence) that calls these."""
from __future__ import annotations
import domains
from . import params, schemas, versions

import ast
import builtins
import json
import math
import re
from collections import defaultdict
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError


IDENT_STOPWORDS = frozenset({
    "self", "cls", "str", "int", "bool", "list", "dict", "set", "tuple",
    "none", "true", "false", "return", "import", "from", "async", "def",
    "class", "yield", "raise", "with", "for", "while", "else", "elif",
    "try", "except", "finally", "not", "and", "the", "this", "that",
    "data", "key", "val", "value", "result", "item", "items", "args",
    "kwargs", "name", "type", "obj", "object", "func", "function",
    "arg", "params", "ctx", "context", "request", "response", "main",
})


def ast_identifiers(code: str) -> set[str]:
    """Best-effort identifier extraction. Python AST covers ~80%; regex fallback for shell/markdown. Stopwords dropped so scaffolding doesn't inflate alignment scores."""
    idents: set[str] = set()
    if not code or not code.strip():
        return idents
    # Python AST
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                idents.add(node.id)
            elif isinstance(node, ast.Attribute):
                idents.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                idents.add(node.name)
                # Decorator names too — they're the BIG semantic anchors.
                for d in node.decorator_list:
                    if isinstance(d, ast.Name):
                        idents.add(d.id)
                    elif isinstance(d, ast.Attribute):
                        idents.add(d.attr)
                    elif isinstance(d, ast.Call):
                        if isinstance(d.func, ast.Name):
                            idents.add(d.func.id)
                        elif isinstance(d.func, ast.Attribute):
                            idents.add(d.func.attr)
            elif isinstance(node, ast.ClassDef):
                idents.add(node.name)
            elif isinstance(node, ast.arg):
                idents.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                idents.add(node.arg)
            elif isinstance(node, ast.alias):
                if node.name:
                    idents.add(node.name.split(".")[-1])
                if node.asname:
                    idents.add(node.asname)
    except SyntaxError:
        # Not valid Python — that's fine, the regex fallback below picks up
        # anything that looks identifier-shaped.
        pass

    # Regex fallback covers PascalCase, snake_case, camelCase tokens that
    # AST may have missed (decorators-as-strings, log messages, etc).
    for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", code):
        idents.add(w)

    # Drop stopwords + ultra-short tokens.
    return {
        i for i in idents
        if len(i) >= 3 and i.lower() not in IDENT_STOPWORDS
    }


def prose_tokens(text: str) -> set[str]:
    """Pull identifier-like tokens from prose (inline `code` spans get
    PRIORITY; bare word tokens are the bulk)."""
    if not text:
        return set()
    out: set[str] = set()
    # Inline `code` spans — strip backticks; these are the strongest
    # signal that the LLM intentionally cited an identifier.
    for m in re.findall(r"`([^`]+)`", text):
        for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", m):
            out.add(w)
    # Bare alphanumeric tokens.
    for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", text):
        out.add(w)
    return {w for w in out if w.lower() not in IDENT_STOPWORDS and len(w) >= 3}


def first_lines_word_set(code: str, n_lines: int = 3) -> set[str]:
    """Lowercased word tokens from first N non-blank code lines — softer fallback for subheading alignment when heading tokens match first-line tokens after stopword removal."""
    if not code:
        return set()
    out: set[str] = set()
    n = 0
    for raw in code.splitlines():
        line = raw.strip()
        if not line:
            continue
        for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", line):
            wl = w.lower()
            if wl in IDENT_STOPWORDS:
                continue
            out.add(wl)
        n += 1
        if n >= n_lines:
            break
    return out


def identifier_overlap(prose: str, code: str) -> tuple[set[str], set[str]]:
    """Return (overlap_set, code_idents); case-sensitive (get_access_token ≠ Get_Access_Token)."""
    code_idents = ast_identifiers(code)
    if not code_idents:
        return set(), set()
    prose_set = prose_tokens(prose)
    if not prose_set:
        return set(), code_idents
    # Exact match first.
    overlap = prose_set & code_idents
    if overlap:
        return overlap, code_idents
    # Fallback: case-insensitive (catches "FastMCP" prose vs "FastMCP" code
    # already a hit; useful when LLM capitalizes differently like
    # `Decorator` vs `decorator`).
    code_lower = {i.lower(): i for i in code_idents}
    prose_lower = {p.lower() for p in prose_set}
    return (
        {code_lower[p] for p in (prose_lower & code_lower.keys())},
        code_idents,
    )


def extract_memory_entry(
    section: schemas.Section,
    section_contributions: list[dict],
    section_heading: str,
) -> schemas.MemoryEntry:
    """Build MemoryEntry deterministically (saves N LLM calls/chapter). SurveyGen-I §3.2.2 shape; mgsr_replan can upgrade to LLM-extract if needed."""
    parts: list[str] = []
    if section.intro:
        parts.append(section.intro.strip())
    if section.subtopics:
        parts.append(section.subtopics[0].explanation.strip())
    summary = " ".join(parts).strip()
    if len(summary) > params.MEMORY_SUMMARY_CHARS_MAX:
        summary = summary[: params.MEMORY_SUMMARY_CHARS_MAX - 1].rsplit(" ", 1)[0] + "…"
    if len(summary) < params.MEMORY_SUMMARY_CHARS_MIN:
        # Pad with the heading + a generic phrase so the Pydantic min
        # passes; mgsr_replan will flag thin sections via checklist_eval
        summary = (
            f"{section_heading}: {summary}"
            if summary
            else f"{section_heading}: (no content)"
        )
        if len(summary) < params.MEMORY_SUMMARY_CHARS_MIN:
            summary = summary + " — content pending refinement."

    candidates: list[str] = []
    for contrib in section_contributions or []:
        for fact in (contrib.get("key_facts") or []):
            # Pull `inline_code` spans
            for m in re.finditer(r"`([^`]+)`", fact):
                t = m.group(1).strip()
                if 2 <= len(t) <= params.MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)
            # Pull capitalized identifiers (PascalCase or camelCase)
            for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]{2,})\b", fact):
                t = m.group(1).strip()
                if 3 <= len(t) <= params.MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)

    # dedupe case-fold-aware
    seen: set[str] = set()
    terminology: list[str] = []
    for t in candidates:
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        terminology.append(t)
        if len(terminology) >= params.MEMORY_TERMS_MAX:
            break

    return schemas.MemoryEntry(
        section_id = section.section_id,
        heading = section_heading,
        summary = summary,
        key_terminology = terminology,
    )


SOFT_ISSUE_PREFIXES = (
    "subheading↔code mismatch",
    "explanation↔code mismatch",
    "subtopics has only ",
)


def section_relevance_query(outline_sec: dict, contributions: list[dict]) -> str:
    """Text describing what a section is about, for matching chapter code blocks to it: outline heading + description + the routed sources' summaries and key facts."""
    parts = [outline_sec.get("heading") or "", outline_sec.get("description") or ""]
    for c in contributions:
        parts.append(c.get("summary") or "")
        parts.extend(c.get("key_facts") or [])
    return " ".join(parts)[:params.RELEVANCE_QUERY_MAX_CHARS]


def _fence_body(entry) -> str:
    return (entry.get("fence_text") if isinstance(entry, dict) else getattr(entry, "fence_text", "")) or ""


def realign_api_subheadings(
    draft: schemas.LLMSectionDraft, vault_rich: dict | None, allowed_hashes: set[str],
) -> tuple[schemas.LLMSectionDraft, int]:
    """A subheading that puts an API in backticks ("Scheduling Coroutines with `asyncio.gather()`") over a block that
    does not contain it got the wrong hash (asyncio ch-01: the sequential `await say_after()` example sat under
    that heading and the prose described concurrency). Swap in the allowed block that does contain the API, when
    this draft does not use it yet; otherwise leave the subtopic alone. Returns (draft, n_swapped)."""
    if not vault_rich:
        return draft, 0
    used = {st.code_ref_hash for st in draft.subtopics if st.code_ref_hash}
    subs, n = list(draft.subtopics), 0
    for i, st in enumerate(subs):
        if not st.code_ref_hash or getattr(st, "code_source", "verbatim") == "derived":
            continue
        body = _fence_body(vault_rich.get(st.code_ref_hash))
        if not body:
            continue
        apis = [
            last for tk in re.findall(r"`([^`]+)`", st.subheading or "")
            for last in [(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", tk) or [""])[-1]]
            if len(last) >= 3 and last.lower() not in IDENT_STOPWORDS
        ]
        if not apis or all(re.search(rf"\b{re.escape(a)}\b", body) for a in apis):
            continue
        cands = [
            h for h in sorted(allowed_hashes)
            if h not in used and h in vault_rich
            and all(re.search(rf"\b{re.escape(a)}\b", _fence_body(vault_rich[h])) for a in apis)
        ]
        if cands:
            best = max(cands, key = lambda h: (sum(len(re.findall(rf"\b{re.escape(a)}\b", _fence_body(vault_rich[h]))) for a in apis), h))
            used.discard(st.code_ref_hash)
            used.add(best)
            subs[i] = st.model_copy(update = {"code_ref_hash": best})
            n += 1
    return (draft.model_copy(update = {"subtopics": subs}), n) if n else (draft, 0)


def realign_code_by_overlap(
    draft: schemas.LLMSectionDraft, vault_rich: dict | None, allowed_hashes: set[str],
) -> tuple[schemas.LLMSectionDraft, int]:
    """Swap the code block of a subtopic whose prose barely mentions it for the unused allowed block it describes
    (see params.REALIGN_*). Deterministic; leaves a subtopic alone when no block fits clearly better.
    Returns (draft, n_swapped)."""
    bank = [h for h in sorted(allowed_hashes) if vault_rich and h in vault_rich]
    if not vault_rich or len(bank) < params.REALIGN_MIN_BANK:
        return draft, 0
    ids = {h: ast_identifiers(_fence_body(vault_rich[h])) for h in bank}
    df: dict[str, int] = {}
    for s_ in ids.values():
        for w in s_:
            df[w] = df.get(w, 0) + 1
    generic = {w for w, c in df.items() if c >= params.REALIGN_GENERIC_DF * len(bank)}

    def specific(prose: str, h: str) -> int:
        return len(identifier_overlap(prose, _fence_body(vault_rich[h]))[0] - generic)

    used = {st.code_ref_hash for st in draft.subtopics if st.code_ref_hash}
    subs, n = list(draft.subtopics), 0
    for i, st in enumerate(subs):
        if not st.code_ref_hash or getattr(st, "code_source", "verbatim") == "derived" or st.code_ref_hash not in vault_rich:
            continue
        prose = f"{st.subheading} {st.explanation}"
        cur = specific(prose, st.code_ref_hash)
        if cur > params.REALIGN_MAX_CUR:
            continue
        best, best_n = None, 0
        for h in bank:
            if h in used:
                continue
            k = specific(prose, h)
            if k > best_n:
                best, best_n = h, k
        if best and best_n >= params.REALIGN_MIN_ALT and best_n >= cur + params.REALIGN_MIN_GAIN:
            used.discard(st.code_ref_hash)
            used.add(best)
            subs[i] = st.model_copy(update = {"code_ref_hash": best})
            n += 1
    return (draft.model_copy(update = {"subtopics": subs}), n) if n else (draft, 0)


def strip_invalid_derived(draft: schemas.LLMSectionDraft) -> tuple[schemas.LLMSectionDraft, int]:
    """The writer may fill `code_source='derived'` + `derived_code` itself when its bank has no block for a topic
    (asyncio ch-03, RETHINK iteration: five "derived" queue examples, every one collapsed onto a single line —
    SyntaxError, audit failed, five broken blocks rendered). Derived code that does not parse as Python is not
    shipped: the subtopic keeps its prose and loses the block. Returns (draft, n_stripped)."""
    ast_valid = domains.dd.synth.nodes.sawc_derive.domain.python_ast_valid
    bad = {
        i for i, st in enumerate(draft.subtopics)
        if getattr(st, "code_source", "verbatim") == "derived" and not ast_valid(st.derived_code or "")
    }
    if not bad:
        return draft, 0
    subs = [
        st.model_copy(update = {"code_ref_hash": "", "code_source": "verbatim", "derived_code": None}) if i in bad else st
        for i, st in enumerate(draft.subtopics)
    ]
    return draft.model_copy(update = {"subtopics": subs}), len(bad)


def prose_matches_code(prose: str, body: str) -> bool:
    """The render step's own misroute rule: a block with at least MISMATCH_MIN_CODE_IDENTS identifiers that shares
    none with the prose is not what the prose describes. (0 false positives over ~100 asyncio subtopics.)"""
    rd = domains.dd.synth.nodes.render
    ci = rd.domain._idents(body)
    if len(ci) < rd.params.MISMATCH_MIN_CODE_IDENTS:
        return True
    return bool(ci & rd.domain._idents(prose))


def strip_misaligned_code(
    draft: schemas.LLMSectionDraft, vault_rich: dict | None,
) -> tuple[schemas.LLMSectionDraft, int]:
    """Turn a subtopic whose code block shares no identifier with its own prose into a prose subtopic — the same call the
    render step makes later (it printed a visible 'omitted' note), made here so the checklist and the judge see the
    final state (asyncio ch-03: Barrier / Condition / Semaphore blocks under queue subtopics). Returns (draft, n)."""
    if not vault_rich:
        return draft, 0
    bad = set()
    for i, st in enumerate(draft.subtopics):
        if not st.code_ref_hash or getattr(st, "code_source", "verbatim") == "derived":
            continue
        body = _fence_body(vault_rich.get(st.code_ref_hash))
        if body and not prose_matches_code(f"{st.subheading} {st.explanation}", body):
            bad.add(i)
    if not bad:
        return draft, 0
    subs = [
        st.model_copy(update = {"code_ref_hash": "", "code_source": "verbatim", "derived_code": None}) if i in bad else st
        for i, st in enumerate(draft.subtopics)
    ]
    return draft.model_copy(update = {"subtopics": subs}), len(bad)


def cap_prose_subtopics(draft: schemas.LLMSectionDraft, n_facts: int) -> tuple[schemas.LLMSectionDraft, int]:
    """A section with no code at all keeps at most ceil(n_facts / PROSE_FACTS_PER_SUBTOPIC) subtopics (floor SUBTOPICS_MIN):
    the later ones are the padded restatements. Returns (draft, n_dropped)."""
    if any(st.code_ref_hash or getattr(st, "code_source", "verbatim") == "derived" for st in draft.subtopics):
        return draft, 0
    cap = max(params.SUBTOPICS_MIN, math.ceil(max(n_facts, 0) / params.PROSE_FACTS_PER_SUBTOPIC))
    if len(draft.subtopics) <= cap:
        return draft, 0
    return draft.model_copy(update = {"subtopics": list(draft.subtopics)[:cap]}), len(draft.subtopics) - cap


_BUILTIN_NAMES = frozenset(dir(builtins))


def strip_fence(text: str) -> str:
    """The code inside a ```lang fence (the text itself when it is not fenced)."""
    t = (text or "").strip()
    m = re.match(r"^```[^\n]*\n(.*?)\n?```\s*$", t, re.S)
    return m.group(1) if m else t


def code_dependencies(block: str) -> tuple[set[str], set[str]]:
    """(defs, free_calls) of one Python block: the functions/classes it defines at top level, and the plain names it calls
    that it neither defines, binds, imports nor takes from the builtins. A block that does not parse gives two empty sets."""
    try:
        tree = ast.parse(strip_fence(block))
    except (SyntaxError, ValueError):
        return set(), set()
    defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    bound, called = set(defs), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.alias):
            bound.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            called.add(n.func.id)
    return defs, called - bound - _BUILTIN_NAMES


def _verbatim_hash(st) -> str:
    return st.code_ref_hash if st.code_ref_hash and getattr(st, "code_source", "verbatim") != "derived" else ""


def dependency_blocks(
    sections: list[schemas.Section], unused: dict[str, list[str]], vault_rich: dict | None,
) -> dict[str, list[str]]:
    """section_id -> the unused routed blocks that define a helper some other block of the section calls, whether that block is already
    printed or is itself still unused (asyncio ch-04: the gather example calls run(), which the unused shell example defines; the
    top-up printed the gather block and left run() out). The coverage top-up must print them."""
    if not vault_rich:
        return {}
    out: dict[str, list[str]] = {}
    for sec in sections:
        un = unused.get(sec.section_id) or []
        callers = [_verbatim_hash(st) for st in sec.subtopics if _verbatim_hash(st)] + list(un)
        free: dict[str, set[str]] = {h: code_dependencies(_fence_body(vault_rich.get(h)))[1] for h in dict.fromkeys(callers)}
        need = [
            h for h in un
            if any(code_dependencies(_fence_body(vault_rich.get(h)))[0] & f for c, f in free.items() if c != h)
        ]
        if need:
            out[sec.section_id] = need
    return out


def order_by_code_dependencies(
    sections: list[schemas.Section], vault_rich: dict | None,
) -> tuple[list[schemas.Section], int]:
    """Define before use: inside a section, the subtopic whose block defines a function another block of the section calls comes
    first (asyncio ch-04: `asyncio.gather(run(...), run(...))` was printed before the shell example that defines run()). Otherwise
    the order is the writer's. Returns (sections, n_sections_reordered)."""
    if not vault_rich:
        return sections, 0
    deps: dict[str, tuple[set[str], set[str]]] = {}
    out, n = [], 0
    for sec in sections:
        subs = list(sec.subtopics)
        for st in subs:
            h = _verbatim_hash(st)
            if h and h not in deps:
                deps[h] = code_dependencies(_fence_body(vault_rich.get(h)))
        by_def: dict[str, int] = {}
        for i, st in enumerate(subs):
            for d in deps.get(_verbatim_hash(st), (set(), set()))[0]:
                by_def.setdefault(d, i)
        order: list[int] = []
        placed: set[int] = set()

        def place(i: int, stack: tuple[int, ...] = ()) -> None:
            if i in placed or i in stack:
                return
            for f in sorted(deps.get(_verbatim_hash(subs[i]), (set(), set()))[1]):
                j = by_def.get(f)
                if j is not None and j != i:
                    place(j, stack + (i,))
            placed.add(i)
            order.append(i)

        for i in range(len(subs)):
            place(i)
        if order != list(range(len(subs))):
            n += 1
            sec = sec.model_copy(update = {"subtopics": [subs[i] for i in order]})
        out.append(sec)
    return out, n


def unused_routed_hashes(
    per_section_index: dict[str, list[dict]], sections: list[schemas.Section],
) -> dict[str, list[str]]:
    """section_id -> the code blocks the digest routed to it that no section of the chapter prints (sorted, stable)."""
    used = {st.code_ref_hash for sec in sections for st in sec.subtopics if st.code_ref_hash}
    out: dict[str, list[str]] = {}
    for sec in sections:
        routed = sorted({h for c in per_section_index.get(sec.section_id) or [] for h in (c.get("code_refs") or [])})
        un = [h for h in routed if h not in used]
        if un:
            out[sec.section_id] = un
    return out


def build_topup_prompt(
    *, framework: str, chapter_title: str, section_heading: str, section_description: str,
    existing_subheadings: list[str], blocks: list[tuple[str, str]], facts: list[str],
    sources: list[tuple[str, str]], max_new: int, required: set[str] | None = None,
) -> str:
    """Prompt for the coverage top-up: extra subtopics for code blocks routed to a section but not printed yet. `required`
    marks the blocks that define a helper other printed examples call; each of them must get a subtopic."""
    required = required or set()
    shown = "\n".join(
        f"[{h}]{' (REQUIRED: the examples already in this section call a function it defines)' if h in required else ''}\n"
        f"{body[:params.TOPUP_MAX_BLOCK_CHARS]}" for h, body in blocks
    )
    covered = "\n".join(f"- {x}" for x in existing_subheadings) or "- (none)"
    fact_lines = "\n".join(f"- {f}" for f in facts[:params.TOPUP_MAX_FACTS]) or "- (none)"
    src_lines = "\n".join(f"- {k}: {sm[:160]}" for k, sm in sources)
    return (
        f"You are extending ONE section of a {framework} learning book (chapter '{chapter_title}', section "
        f"'{section_heading}'). {section_description}\n\n"
        f"== ALREADY COVERED (do not repeat these topics) ==\n{covered}\n\n"
        f"== CODE BLOCKS ROUTED TO THIS SECTION BUT NOT USED YET (verbatim from the docs) ==\n{shown}\n\n"
        f"== FACTS FROM THE DOCS ==\n{fact_lines}\n\n"
        f"== SOURCES YOU MAY CITE ==\n{src_lines}\n\n"
        f"For each block that teaches something the section does not cover yet, write ONE subtopic (at most {max_new}):\n"
        f"  - code_ref_hash: exactly one hash from the bracketed list above\n"
        f"  - subheading: 2-10 words naming what THAT block demonstrates\n"
        f"  - explanation: 8-80 words of prose that comes BEFORE the code; it MUST name at least one identifier "
        f"(function, class, method, parameter) that appears in the block and describe what the block shows\n"
        f"Skip a block that only repeats a covered topic, except a REQUIRED one: write a subtopic for it. Do not write code. Add citations (source_key from the list, "
        f"claim 6-400 chars) for the facts you use.\n\n"
        f'OUTPUT — STRICT JSON, no preamble: {{"subtopics": [{{"code_ref_hash": "...", "subheading": "...", '
        f'"explanation": "..."}}], "citations": [{{"source_key": "...", "claim": "..."}}]}}'
    )


def _reject(rejects: dict[str, int] | None, why: str) -> None:
    if rejects is not None:
        rejects[why] = rejects.get(why, 0) + 1


def parse_topup(
    parsed: dict | None, *, unused: set[str], vault_rich: dict | None, valid_source_keys: set[str],
    existing_subheads: set[str], max_new: int, rejects: dict[str, int] | None = None,
) -> tuple[list[schemas.Subtopic], list[schemas.Citation]]:
    """Validate a top-up answer: only unused routed hashes, no repeated hash or subheading, prose that matches its block. `rejects`
    (optional) collects why candidates were dropped, for the log."""
    if not parsed:
        _reject(rejects, "unparseable_json")
    parsed = normalize_typography(parsed)
    subs: list[schemas.Subtopic] = []
    seen_h: set[str] = set()
    seen_s = {x.casefold() for x in existing_subheads}
    for raw in (parsed or {}).get("subtopics") or []:
        if len(subs) >= max_new or not isinstance(raw, dict):
            break
        try:
            st = schemas.Subtopic(
                code_ref_hash = str(raw.get("code_ref_hash") or ""), subheading = str(raw.get("subheading") or ""),
                explanation = str(raw.get("explanation") or ""),
            )
        except Exception:
            _reject(rejects, "schema")
            continue
        h = st.code_ref_hash
        if not h or h not in unused:
            _reject(rejects, "hash_not_unused")
            continue
        if h in seen_h or st.subheading.casefold() in seen_s:
            _reject(rejects, "duplicate")
            continue
        body = _fence_body(vault_rich.get(h)) if vault_rich else ""
        if body and not prose_matches_code(f"{st.subheading} {st.explanation}", body):
            _reject(rejects, "prose_not_about_block")
            continue
        subs.append(st)
        seen_h.add(h)
        seen_s.add(st.subheading.casefold())
    cites: list[schemas.Citation] = []
    for raw in (parsed or {}).get("citations") or []:
        if not isinstance(raw, dict) or str(raw.get("source_key") or "") not in valid_source_keys:
            continue
        try:
            cites.append(schemas.Citation(source_key = str(raw["source_key"]), claim = str(raw.get("claim") or "")))
        except Exception:
            continue
    return subs, cites


_REL_RANK = {"primary": 0, "supporting": 1}
# A top-up subtopic that talks ABOUT the docs instead of stating their facts (asyncio ch-04: "The documentation provides examples of how to
# modify single-command scripts ..."): dropped.
META_FRAMING_RE = re.compile(
    r"^\s*(?:the|this)\s+(?:\w+\s+)?(?:documentation|docs|page|source|text|guide)\s+"
    r"(?:provides|shows|explains|describes|demonstrates|states|notes|mentions|covers|includes|contains|gives)\b",
    re.I,
)


def chapter_prose_tokens(sections: list[schemas.Section]) -> set[str]:
    """Words of everything the chapter says in prose (intros, subheadings, explanations; code excluded)."""
    return prose_tokens(" ".join(
        part for sec in sections for part in [sec.intro] + [f"{st.subheading} {st.explanation}" for st in sec.subtopics]
    ))


def uncovered_facts(facts: list[str], covered_tokens: set[str]) -> list[str]:
    """The key facts (long enough to judge) of which fewer than TOPUP_FACT_COVERED of the words occur in `covered_tokens`."""
    out = []
    for f in facts:
        ft = prose_tokens(f)
        if len(ft) >= params.TOPUP_FACT_MIN_TOKENS and len(ft & covered_tokens) / len(ft) < params.TOPUP_FACT_COVERED:
            out.append(f)
    return out


def source_gaps(
    per_section_index: dict[str, list[dict]], sections: list[schemas.Section], covered_tokens: set[str] | None = None,
) -> dict[str, list[tuple[str, str, list[str]]]]:
    """section_id -> [(source_key, summary, facts to teach)] for sources routed to the chapter that the prose leaves unsaid: a
    source no section cites (all its key facts), and, when `covered_tokens` is given, a cited source with at least
    TOPUP_FACT_MIN_GAP key facts the chapter prose does not state (those facts). Each source goes to the section where its
    contribution ranks best (primary, then supporting, then the earliest section)."""
    cited = {c.source_key for sec in sections for c in sec.citations}
    order = {sec.section_id: i for i, sec in enumerate(sections)}
    best: dict[str, tuple[tuple, str, str, list[str]]] = {}
    for sid, contribs in per_section_index.items():
        if sid not in order:
            continue
        for c in contribs:
            k = c.get("source_key")
            facts = [f for f in (c.get("key_facts") or []) if isinstance(f, str) and f.strip()]
            if not k or len(facts) < params.TOPUP_SRC_MIN_FACTS:
                continue
            if k in cited:
                if covered_tokens is None:
                    continue
                facts = uncovered_facts(facts, covered_tokens)
                if len(facts) < params.TOPUP_FACT_MIN_GAP:
                    continue
            rank = (_REL_RANK.get(c.get("relevance"), 2), order[sid])
            if k not in best or rank < best[k][0]:
                best[k] = (rank, sid, c.get("summary") or "", facts)
    out: dict[str, list[tuple[str, str, list[str]]]] = {}
    for k, (_, sid, summary, facts) in sorted(best.items(), key = lambda kv: kv[1][0]):
        out.setdefault(sid, []).append((k, summary, facts))
    return out


def uncited_routed_sources(
    per_section_index: dict[str, list[dict]], sections: list[schemas.Section],
) -> dict[str, list[tuple[str, str, list[str]]]]:
    """section_id -> [(source_key, summary, key_facts)] for sources routed to the chapter that no section cites."""
    return source_gaps(per_section_index, sections)


def build_source_topup_prompt(
    *, framework: str, chapter_title: str, section_heading: str, section_description: str,
    existing_subheadings: list[str], sources: list[tuple[str, str, list[str]]], max_new: int,
) -> str:
    """Prompt for the source top-up: prose subtopics for routed sources the section never covered."""
    covered = "\n".join(f"- {x}" for x in existing_subheadings) or "- (none)"
    blocks = "\n\n".join(
        f"[{k}]\n{sm[:300]}\nFacts:\n" + "\n".join(f"- {f}" for f in facts[:10]) for k, sm, facts in sources
    )
    return (
        f"You are extending ONE section of a {framework} learning book (chapter '{chapter_title}', section "
        f"'{section_heading}'). {section_description}\n\n"
        f"== ALREADY COVERED (do not repeat these topics) ==\n{covered}\n\n"
        f"== DOC FACTS THE SECTION DOES NOT STATE YET (no code is available for them) ==\n{blocks}\n\n"
        f"Write at most {max_new} prose subtopics that teach what these facts say. Give each page ONE subtopic holding all of "
        f"its facts listed here (never several short ones for the same page); one subtopic may combine pages that cover the same "
        f"API. State only what the covered text above does not already say, and state it directly: never restate a covered topic, "
        f"never open with 'the documentation/page/source ...'. For each:\n"
        f"  - subheading: 2-10 words naming the topic\n"
        f"  - explanation: {params.TOPUP_SRC_MIN_WORDS}-80 words of connected sentences stating the concrete facts (function "
        f"names, parameters, behaviour, version notes) taken ONLY from the facts above — no invented details, no code\n"
        f"  - sources: the page keys (exactly as in the brackets) you used\n"
        f"Then add citations (source_key from the brackets, claim 6-400 chars) for every page you used.\n\n"
        f'OUTPUT — STRICT JSON, no preamble: {{"subtopics": [{{"subheading": "...", "explanation": "...", "sources": ["..."]}}], '
        f'"citations": [{{"source_key": "...", "claim": "..."}}]}}'
    )


def parse_source_topup(
    parsed: dict | None, *, sources: list[tuple[str, str, list[str]]], existing_subheads: set[str], max_new: int,
    rejects: dict[str, int] | None = None,
) -> tuple[list[schemas.Subtopic], list[schemas.Citation]]:
    """Validate a source top-up: prose only, new heading, text that shares words with the facts it claims to cover; an
    offered source whose facts the prose clearly uses is cited even when the model forgot to."""
    if not parsed:
        _reject(rejects, "unparseable_json")
    parsed = normalize_typography(parsed)
    facts_by_src = {k: " ".join([sm] + facts) for k, sm, facts in sources}
    toks_by_src = {k: prose_tokens(t) for k, t in facts_by_src.items()}
    first_fact = {k: facts[0] for k, _, facts in sources}
    # (subtopic, sources it used, sources to cite); a page gets ONE subtopic — a second one led by the same page is folded into
    # it (asyncio ch-07: three 10-19 word fragments of one SubprocessTransport page, which the judge failed as "thin,
    # disconnected reference cards"), and a subtopic still under TOPUP_SRC_MIN_WORDS is dropped.
    entries: list[list] = []
    lead_idx: dict[str, int] = {}
    seen = {x.casefold() for x in existing_subheads}
    for raw in (parsed or {}).get("subtopics") or []:
        if not isinstance(raw, dict):
            continue
        try:
            st = schemas.Subtopic(
                code_ref_hash = "", subheading = str(raw.get("subheading") or ""), explanation = str(raw.get("explanation") or ""),
            )
        except Exception:
            _reject(rejects, "schema")
            continue
        if META_FRAMING_RE.search(st.explanation):
            _reject(rejects, "meta_framing")
            continue
        et = prose_tokens(st.explanation)
        used = [k for k in toks_by_src if len(et & toks_by_src[k]) >= params.TOPUP_SRC_GROUND_TOKENS]
        if not used:
            _reject(rejects, "not_grounded_in_facts")
            continue
        cite = [k for k in used if len(et & toks_by_src[k]) >= params.TOPUP_SRC_CITE_TOKENS or k in (raw.get("sources") or [])]
        lead = max(used, key = lambda k: len(et & toks_by_src[k]))
        if lead in lead_idx:
            ent = entries[lead_idx[lead]]
            try:
                ent[0] = schemas.Subtopic(code_ref_hash = "", subheading = ent[0].subheading, explanation = f"{ent[0].explanation} {st.explanation}")
            except Exception:
                continue
            ent[1] = list(dict.fromkeys(ent[1] + used))
            ent[2] = list(dict.fromkeys(ent[2] + cite))
            continue
        if st.subheading.casefold() in seen:
            _reject(rejects, "duplicate_heading")
            continue
        seen.add(st.subheading.casefold())
        lead_idx[lead] = len(entries)
        entries.append([st, used, cite])
    subs: list[schemas.Subtopic] = []
    cites: dict[tuple[str, str], schemas.Citation] = {}
    for st, _, cite in entries:
        if len(subs) >= max_new or len(st.explanation.split()) < params.TOPUP_SRC_MIN_WORDS:
            _reject(rejects, "too_short" if len(subs) < max_new else "over_max_new")
            continue
        subs.append(st)
        for k in cite:
            try:
                c = schemas.Citation(source_key = k, claim = first_fact[k][:400])
                cites[(c.source_key, c.claim)] = c
            except Exception:
                pass
    for raw in (parsed or {}).get("citations") or []:
        if isinstance(raw, dict) and raw.get("source_key") in facts_by_src and subs:
            try:
                c = schemas.Citation(source_key = str(raw["source_key"]), claim = str(raw.get("claim") or ""))
                cites.setdefault((c.source_key, c.claim), c)
            except Exception:
                continue
    return subs, list(cites.values())


def drop_unknown_code_refs(
    draft: schemas.LLMSectionDraft, allowed_hashes: set[str],
) -> tuple[schemas.LLMSectionDraft, int]:
    """Turn subtopics citing a code hash that is not in the allowed bank into prose subtopics (empty hash) instead of shipping the invented reference. The vault guarantees every rendered block is real source code; an unresolvable hash can only render as nothing and fails the chapter audit (asyncio ch-07: 2 of 3 refs valid, 1 invented → `needs_review` for an otherwise 93% chapter). Runs after the repair loops, as the last resort. Returns (draft, n_converted)."""
    bad = {
        i for i, st in enumerate(draft.subtopics)
        if st.code_ref_hash and st.code_ref_hash not in allowed_hashes
    }
    if not bad:
        return draft, 0
    subs = [
        st.model_copy(update = {
            "code_ref_hash": "", "code_source": "verbatim", "derived_code": None,
        }) if i in bad else st
        for i, st in enumerate(draft.subtopics)
    ]
    return draft.model_copy(update = {"subtopics": subs}), len(bad)


def hard_issues(issues: list[str]) -> list[str]:
    """Filter to issues that trigger writer repair. Soft issues still ship in .issues for visibility but skip repair — writer can't reliably close them."""
    return [
        i for i in issues
        if not any(i.startswith(p) for p in SOFT_ISSUE_PREFIXES)
    ]


def code_density_floor(n_allowed: int) -> int:
    """Fewest subtopics a code-first section must have for a bank of `n_allowed` blocks (the validator's CODE-FIRST rule)."""
    if n_allowed >= 20:
        return 6
    if n_allowed >= 10:
        return 4
    if n_allowed >= 6:
        return 3
    if n_allowed >= 3:
        return max(params.SUBTOPICS_MIN, 3)
    return params.SUBTOPICS_MIN


def writer_subtopic_range(n_allowed: int) -> tuple[str, str, str]:
    """(pick range, repeat range, aim range) the writer prompt names for a bank of `n_allowed` blocks. A bank of 10+ blocks asks for at least the
    validator's floor (fastmcp ch-01: the prompt said "4-6" while the validator wanted 6+ for banks of 20+, so most drafts were sent to a repair
    call for the very thing the prompt told them to do)."""
    if n_allowed < 10:
        return "3-8", "4-6", "4-6"
    lo = max(4, code_density_floor(n_allowed))
    hi = min(params.SUBTOPICS_MAX, lo + 3)
    return f"{lo}-{hi}", f"{lo}-{hi}", f"{lo}-{hi}"


def validate_section_against_inputs(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    vault_rich: dict | None = None,
) -> list[str]:
    """Cross-reference rules beyond Pydantic: heading drift, hallucinated hashes/source_keys, subheading↔code mismatch, explanation↔code mismatch. vault_rich=None skips code-body checks gracefully."""
    issues: list[str] = []

    if draft.heading.strip().casefold() != expected_heading.strip().casefold():
        issues.append(
            f"heading {draft.heading!r} doesn't match the outline heading "
            f"{expected_heading!r}. Echo the outline heading verbatim."
        )

    # v2 cookbook schema: validate subtopics' code_ref_hash field.
    # Empty hashes are PROSE subtopics (no-code section) — exempt.
    bad_hashes = [
        s.code_ref_hash for s in draft.subtopics
        if s.code_ref_hash and s.code_ref_hash not in allowed_hashes
    ]
    if bad_hashes:
        issues.append(
            f"subtopics use code_ref_hash not in allowed_hashes: {bad_hashes}. "
            f"Pick ONLY from the allowed_hashes list shown in the prompt."
        )

    # Code-density floor scaled to bank size. Each subtopic = 1 code block,
    # so the floor IS the subtopic count.
    n_allowed = len(allowed_hashes)
    n_used = len(draft.subtopics)
    floor = code_density_floor(n_allowed)
    if n_used < floor:
        sorted_bank = sorted(allowed_hashes)[:30]
        bank_listing = ", ".join(sorted_bank)
        if len(allowed_hashes) > 30:
            bank_listing += f", ... ({len(allowed_hashes) - 30} more)"
        issues.append(
            f"subtopics has only {n_used} entries but the section's code "
            f"bank offers {n_allowed} hashes — that's a CODE-FIRST violation. "
            f"Emit at least {floor} subtopics, each with a distinct hash "
            f"from the bank. Available hashes you can cite: [{bank_listing}]. "
            f"Each Subtopic needs subheading (2-10 words) + explanation "
            f"(8-80 words, the prose BEFORE the code) + code_ref_hash."
        )

    bad_sources = [
        c.source_key for c in draft.citations
        if c.source_key not in valid_source_keys
    ]
    if bad_sources:
        issues.append(
            f"citations use source_keys not in the digest: {bad_sources}. "
            f"Pick ONLY from the source_keys listed in the prompt."
        )

    # Skipped when vault_rich is unavailable (back-compat with older callers).
    if vault_rich:
        # Derived subtopics have their own validation path (AST gate in
        # render_audit_write); skip the verbatim-anchor check for them.
        misaligned_sub: list[str] = []
        misaligned_expl: list[str] = []
        for s in draft.subtopics:
            if getattr(s, "code_source", "verbatim") == "derived":
                continue
            entry = vault_rich.get(s.code_ref_hash) if vault_rich else None
            if entry is None:
                continue
            # entry can be a dict-shaped VaultEntry or the model itself.
            body = (
                entry.get("fence_text") if isinstance(entry, dict)
                else getattr(entry, "fence_text", "")
            ) or ""
            if not body.strip():
                continue
            code_idents = ast_identifiers(body)
            if not code_idents:
                # No identifiers extractable (e.g., directory tree or
                # plain markdown) — skip both alignment checks; let the
                # writer's heuristics handle it.
                continue

            # subheading↔code. Strict-AST overlap first; if zero,
            # fall back to first-3-lines word overlap (catches less
            # tightly-named patterns like 'Minimal Tool Definition').
            # A subheading that puts an API in backticks ("... with `asyncio.gather()`") must find it in the block: asyncio ch-01
            # printed the sequential `await say_after()` example under that heading (the shared `asyncio` / `say_after`
            # words passed the overlap test below).
            absent_api = [
                last for tk in re.findall(r"`([^`]+)`", s.subheading or "")
                for last in [(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", tk) or [""])[-1]]
                if len(last) >= 3 and last.lower() not in IDENT_STOPWORDS and not re.search(rf"\b{re.escape(last)}\b", body)
            ]
            if absent_api:
                misaligned_sub.append(s.subheading)
                continue
            sub_overlap = prose_tokens(s.subheading) & code_idents
            if not sub_overlap:
                head_words = first_lines_word_set(body, n_lines = 3)
                head_overlap = {
                    w.lower() for w in prose_tokens(s.subheading)
                } & head_words
                if not head_overlap:
                    misaligned_sub.append(s.subheading)

            # explanation↔code: inline `code` span = high-precision; bare words = low-precision. Floor: any backtick match OR ≥2 distinct bare overlaps (1 bare is too easy to game).
            inline_prose = set()
            for tk in re.findall(r"`([^`]+)`", s.explanation):
                for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", tk):
                    if w.lower() not in IDENT_STOPWORDS and len(w) >= 3:
                        inline_prose.add(w)
            inline_match = inline_prose & code_idents
            if inline_match:
                pass  # high-precision signal — accept
            else:
                bare_overlap, _ = identifier_overlap(s.explanation, body)
                if len(bare_overlap) < 2:
                    misaligned_expl.append(s.subheading)

        if misaligned_sub:
            sample = misaligned_sub[:3]
            issues.append(
                f"subheading↔code mismatch on subtopic(s) {sample!r}: the "
                f"subheading names a topic that has no overlap with the "
                f"chosen code_ref_hash body's identifiers. PICK THE HASH "
                f"FIRST, then name what the code actually demonstrates "
                f"(decorator, function, type, parameter visible in the "
                f"block). If no allowed hash matches the topic you want "
                f"to cover, drop that subtopic and pick a different hash."
            )
        if misaligned_expl:
            sample = misaligned_expl[:3]
            issues.append(
                f"explanation↔code mismatch on subtopic(s) {sample!r}: the "
                f"explanation references zero identifiers from the chosen "
                f"code block. Rewrite the explanation to name ≥1 specific "
                f"identifier (decorator like `@mcp.tool`, function name, "
                f"type, kwarg) that appears in the picked code body. "
                f"Generic prose that describes a broader topic without "
                f"grounding to the visible code is rejected."
            )

    return issues


def score_draft_structural(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> float:
    """Structural quality score (Self-Certainty proxy, arXiv 2502.18581) for when critic LLM fails. Penalizes vault/citation violations and heading mismatch; rewards subtopic count + citation density + explanation length."""
    # v2 cookbook scoring: subtopic count + explanation density + heading
    # match + citation count drive the structural score.
    n_vault_violations = sum(
        1 for s in draft.subtopics
        if s.code_ref_hash and s.code_ref_hash not in allowed_hashes
    )
    n_citation_violations = sum(
        1 for c in draft.citations if c.source_key not in valid_source_keys
    )
    heading_mismatch = (
        draft.heading.strip().casefold() != expected_heading.strip().casefold()
    )

    n_subtopics = len(draft.subtopics)
    n_citations = len(draft.citations)
    total_expl_chars = sum(len(s.explanation) for s in draft.subtopics)
    intro_chars = len(draft.intro or "")

    score = 5.0
    score -= 10.0 * n_vault_violations
    score -= 10.0 * n_citation_violations
    score -= 5.0 if heading_mismatch else 0.0
    # Reward 4-6 subtopics; penalize <3 (impossible — Pydantic blocks) or >10
    score += 5.0 * min(n_subtopics / 5.0, 1.0)
    score -= 1.0 * max(0, n_subtopics - 10)
    # Reward citation density
    if n_primary_contribs > 0:
        score += 4.0 * min(n_citations / n_primary_contribs, 1.0)
    # Reward intro + explanations in sweet spot
    if intro_chars >= 60:
        score += 1.0
    avg_expl = total_expl_chars / max(1, n_subtopics)
    if 60 <= avg_expl <= 400:
        score += 2.0
    elif avg_expl > 800:
        score -= 1.0
    return round(score, 3)


def compute_sawc_stats(
    sections: list[schemas.Section],
    n_stages: int,
    n_total_drafts_fired: int,
    n_critic_picks: int,
    n_picker_fallbacks: int,
) -> schemas.SAWCStats:
    n_sections = len(sections)
    # `n_sections_completed` = content-bearing, NOT "zero issues" — soft
    # warnings still ship the section; gate fires only on actual absence.
    def _is_present(s) -> bool:
        if "placeholder" in (s.issues or []):
            return False
        if not (s.heading or "").strip():
            return False
        if not (s.intro or "").strip():
            return False
        if not s.subtopics:
            return False
        if not s.citations:
            return False
        return True

    n_sections_completed = sum(1 for s in sections if _is_present(s))
    n_sections_fallback = sum(1 for s in sections if "placeholder" in s.issues)
    n_sections_citation_fallback = sum(
        1 for s in sections if "citation_fallback" in s.issues
    )
    n_repairs = sum(s.n_repairs for s in sections)
    total_subtopics = sum(len(s.subtopics) for s in sections)
    total_citations = sum(len(s.citations) for s in sections)
    total_expl_words = sum(
        len((st.explanation or "").split())
        for s in sections for st in s.subtopics
    )
    return schemas.SAWCStats(
        n_sections = n_sections,
        n_sections_completed = n_sections_completed,
        n_sections_fallback = n_sections_fallback,
        n_sections_citation_fallback = n_sections_citation_fallback,
        n_stages = n_stages,
        n_total_drafts_fired = n_total_drafts_fired,
        n_critic_picks = n_critic_picks,
        n_picker_fallbacks = n_picker_fallbacks,
        n_repairs = n_repairs,
        total_subtopics = total_subtopics,
        total_citations = total_citations,
        avg_subtopics_per_section = (
            total_subtopics / n_sections if n_sections else 0.0
        ),
        avg_explanation_words = (
            total_expl_words / total_subtopics if total_subtopics else 0.0
        ),
    )


def format_contributions_block(contributions: list[dict]) -> str:
    """Pretty-format the digest's per_section[section_id] contributions for
    the writer prompt."""
    if not contributions:
        return "(no contributions assigned to this section — write a thin "\
               "orientation paragraph only; checklist_eval will flag this)"
    lines: list[str] = []
    for i, c in enumerate(contributions):
        src = c.get("source_key") or "?"
        # Source key can be long — show last component
        src_short = src.rsplit("/", 1)[-1]
        relevance = c.get("relevance", "?")
        summary = c.get("summary", "")
        facts = c.get("key_facts") or []
        refs = c.get("code_refs") or []
        lines.append(
            f"  [{i + 1}] {src_short} ({relevance}) — {summary}\n"
            f"      key_facts:"
        )
        for f in facts[:5]:
            lines.append(f"        • {f}")
        if refs:
            lines.append(f"      code_refs: {', '.join(refs)}")
    return "\n".join(lines)


def format_memory_block(memory: list[dict]) -> str:
    """Pretty-format the memory ledger for the writer prompt; accepts dicts (model_dump() or raw) to avoid callers coercing to MemoryEntry."""
    if not memory:
        return "  (this is the first stage — no prior sections yet)"
    lines: list[str] = []
    for e in memory:
        sid = e.get("section_id", "?")
        head = e.get("heading", "?")
        summ = e.get("summary", "")
        terms = e.get("key_terminology") or []
        lines.append(f"  [{sid}] {head}")
        lines.append(f"      summary:     {summ}")
        if terms:
            lines.append(
                f"      terminology: {', '.join(terms)}"
            )
    return "\n".join(lines)


MAX_VAULT_CHARS_PER_ENTRY = 6_000


MAX_VAULT_CHARS_TOTAL     = 60_000


_CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom as outline_sdp/digest_construct's
    classifiers. The Rotator is a universal gateway with no context-length
    -aware arm filtering."""
    msg = str(e).lower()
    return any(marker in msg for marker in _CONTEXT_OVERFLOW_MARKERS)


def build_writer_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
    prose_mode: bool = False,
    mixed_mode: bool = False,
    already_shown_hashes: set[str] | None = None,
    vault_char_budget: int | None = None,
    prior_feedback: list[str] | None = None,
) -> str:
    """Build the per-section writer prompt. mixed_mode=True when the code bank holds only 1-2 blocks (fewer than SUBTOPICS_MIN): the writer anchors one subtopic to each block and fills the rest with prose, instead of the section going fully prose and dropping the only examples there are. Original notes follow. vault_rich enables Visible Vault (LLM sees code bodies; hash-only listing otherwise). prose_mode=True when bank is empty (prose subtopics instead of placeholder). already_shown_hashes suppresses cross-section hash recycling. vault_char_budget overrides MAX_VAULT_CHARS_TOTAL — used to retry at a smaller budget after a context-overflow failure. prior_feedback: checklist's failed-criteria feedback strings from the PREVIOUS RETHINK iteration — closes the self-refine loop (arXiv 2303.17651 requires critique to inform regeneration; before this, a RETHINK iteration reran blind with zero signal about what was actually wrong, which is why some iterations regressed instead of improving)."""
    prior_feedback_block = ""
    if prior_feedback:
        feedback_lines = "\n".join(f"  - {fb}" for fb in prior_feedback[:8])
        prior_feedback_block = (
            f"== PRIOR ATTEMPT FEEDBACK — FIX THESE ==\n"
            f"The last draft of this chapter failed review for reasons "
            f"below. This is a REWRITE, not a first draft — address these "
            f"specifically, don't just repeat the same approach:\n"
            f"{feedback_lines}\n\n"
        )
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites
        else "(none — this is a stage-0 section)"
    )
    # A section with an empty code bank is a conceptual/prose topic — write
    # prose subtopics rather than failing to an empty placeholder.
    mixed = bool(mixed_mode) and bool(allowed_hashes) and not prose_mode
    prose = (prose_mode or not allowed_hashes) and not mixed

    rng_pick, rng_repeat, rng_aim = writer_subtopic_range(len(allowed_hashes))
    already_shown_hashes = already_shown_hashes or set()
    shown_here = sorted(h for h in (already_shown_hashes or set()) if h)
    already_shown_block = ""
    if shown_here and not prose:
        listing = ", ".join(shown_here[:40])
        if len(shown_here) > 40:
            listing += f", … ({len(shown_here) - 40} more)"
        already_shown_block = (
            f"== ALREADY SHOWN EARLIER IN THIS CHAPTER (do NOT re-pick) ==\n"
            f"These hashes were already rendered as subtopics in earlier "
            f"sections. Re-picking one makes this section a hollow 'see above' "
            f"cross-reference (a render-time pass strips the duplicate). PREFER "
            f"hashes NOT in this list; only re-pick if it is genuinely central "
            f"to THIS section's distinct angle:\n  {listing}\n\n"
        )

    # Visible vault — LLM sees code bodies (budget-capped: the Rotator is
    # a universal gateway with no context-length-aware arm filtering, so
    # an uncapped bank can exceed a small-context arm from a heterogeneous
    # pool — see format_entries_for_prompt). Render still substitutes via
    # hash so final output is byte-perfect regardless of what got
    # truncated here.
    if allowed_hashes and vault_rich:
        coerced_vault: dict[str, domains.dd.synth.nodes.vault.schemas.VaultEntry] = {}
        for h in allowed_hashes:
            entry = vault_rich.get(h)
            if entry is None:
                continue
            if isinstance(entry, dict):
                try:
                    entry = domains.dd.synth.nodes.vault.schemas.VaultEntry(**entry)
                except Exception:
                    entry = domains.dd.synth.nodes.vault.schemas.VaultEntry(
                        hash = h,
                        fence_text = entry.get("fence_text") or "",
                        info_string = entry.get("info_string") or "",
                        lang = entry.get("lang") or "text",
                        line_count = int(entry.get("line_count") or 0),
                        char_count = int(entry.get("char_count") or 0),
                        sentinel_kind = entry.get(
                            "sentinel_kind", "fence_backtick",
                        ),
                    )
            coerced_vault[h] = entry
        hash_list = domains.dd.synth.nodes.vault.domain.format_entries_for_prompt(
            coerced_vault, hashes = allowed_hashes,
            max_chars_per_entry = MAX_VAULT_CHARS_PER_ENTRY,
            max_total_chars = vault_char_budget or MAX_VAULT_CHARS_TOTAL,
        )
    else:
        hash_list = (
            "\n".join(f"  - {h}" for h in allowed_hashes)
            if allowed_hashes
            else "  (none — prose-only section, leave code_refs empty)"
        )

    source_list = (
        "\n".join(f"  - {k}" for k in valid_source_keys)
        if valid_source_keys
        else "  (no sources — citations may be empty)"
    )

    # Prose vs code-first: build the bank section + a top-of-prompt directive.
    if mixed:
        n_bank = len(allowed_hashes)
        prose_note = (
            f"🟨 MIXED MODE — this section's code bank has only {n_bank} "
            f"block(s), too few for a code-first section. Anchor ONE subtopic "
            f"to EACH bank block (code_ref_hash = that block's hash). Every "
            f"OTHER subtopic is PROSE (code_ref_hash \"\"), so the section "
            f"still has ≥3 DISTINCT subtopics. The CODE-FIRST rules below "
            f"(unique hash, identifier grounding, no embellishment) apply ONLY "
            f"to the subtopics you anchor to a bank block; the 'at least 3 "
            f"subtopics' floor is met by the prose ones.\n\n"
        )
        bank_section = (
            f"== CODE BANK — {n_bank} block(s); use EACH in exactly one "
            f"subtopic ==\n"
            f"{hash_list}\n\n"
            f"== PROSE SUBTOPICS — everything beyond the bank ==\n"
            f"Emit 3-6 subtopics in total. Anchored subtopic: code_ref_hash = "
            f"the block's hash, a subheading naming what THAT block shows, and "
            f"an 8-80 word explanation citing ≥1 identifier visible in it — "
            f"describe ONLY what the block shows. Every other subtopic: "
            f"code_ref_hash \"\" (EMPTY) and a substantial 40-80 word "
            f"explanation grounded in the contributions + citations (no "
            f"invented specifics).\n"
        )
    elif prose:
        prose_note = (
            "🟦 PROSE MODE — this section's sources have NO code; it is a "
            "CONCEPTUAL topic. The CODE-FIRST rules below (pick a hash first, "
            "code-density, identifier grounding, no-recycle) are SUSPENDED. "
            "Set EVERY subtopic's code_ref_hash to \"\" (empty) and write "
            "substantial, source-grounded conceptual prose. Still emit ≥3 "
            "DISTINCT subtopics and ground each to the contributions / "
            "citations below — state only what the sources say.\n\n"
        )
        bank_section = (
            "== PROSE SECTION — NO CODE BANK ==\n"
            "Teach this concept as prose. Emit 3-6 subtopics, each:\n"
            "  - code_ref_hash: \"\"   (EMPTY — no code to anchor)\n"
            "  - subheading: 2-10 words naming the concept / step / policy\n"
            "  - explanation: a substantial 40-80 word paragraph that "
            "actually TEACHES it, grounded in the contributions + citations "
            "(no invented specifics — no numbers, flags, or APIs the sources "
            "don't state).\n"
            "  - GROUND IT CONCRETELY: name at least one real file path, "
            "directory name, config key, command, or setting the sources "
            "actually mention. 'This helps you understand the layout' is "
            "filler — 'settings.json lives under ~/.claude/' is not. If the "
            "sources genuinely give you nothing concrete for a subtopic, "
            "that's a signal to cut it and cover fewer, better-grounded "
            "subtopics instead.\n"
            "  - VARY THE OPENING of each explanation — reusing the same "
            "lead-in phrase across subtopics (e.g. every paragraph starting "
            "'Exploring the directory...') reads as templated filler, not "
            "distinct teaching points.\n"
        )
    else:
        prose_note = ""
        bank_section = (
            f"== ALLOWED CODE BANK ({len(allowed_hashes)} entries) — these "
            f"are the actual code blocks available for THIS section. "
            f"Each `<code id = ...>` envelope shows the FULL code body. PICK "
            f"{rng_pick} BEST ONES — each becomes one subtopic. Reason about each "
            f"block fully; the explanation must reference specific lines / "
            f"decorators / arguments. ==\n"
            f"{hash_list}"
        )
    return (
        f"You are the Section Writer — step 6 of the Docs Distiller "
        f"synth pipeline. Write ONE section of one chapter as a "
        f"COOKBOOK — a sequence of (subheading, explanation, code block) "
        f"triples. This is one of N = 3 best-of-N drafts; a critic LLM "
        f"will pick the best afterwards (MAMM-Refine arXiv 2503.15272).\n\n"

        f"⚡ CRITICAL PURPOSE — this is a CODE-FIRST learning resource. "
        f"The reader is here to learn {framework} FAST by reading "
        f"production-quality code with focused explanations. Structure "
        f"is: TOPIC (H2) → SUBTOPIC (H3) → 1-2 sentence explanation → "
        f"code block. Repeat the subtopic pattern {rng_repeat} times per "
        f"section. Each code block teaches ONE pedagogically valuable "
        f"thing.\n\n"

        f"{prior_feedback_block}"
        f"{prose_note}"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION (H2): {section_id} — {section_heading}\n"
        f"SECTION GOAL: {section_description}\n"
        f"PREREQUISITES (already covered): {prereqs_str}\n\n"

        f"== GROUNDED CONTRIBUTIONS (your subtopics MUST cover these) ==\n"
        f"{format_contributions_block(contributions)}\n\n"

        f"{already_shown_block}"

        f"{bank_section}\n\n"

        f"== VALID CITATION SOURCE_KEYS ({len(valid_source_keys)}) — "
        f"these are the source docs that the digest routed TO THIS "
        f"SECTION specifically (NOT chapter-wide). "
        f"citations.source_key MUST be one of these — citing a source "
        f"that wasn't routed here means the section is straying from "
        f"its assigned scope. ==\n"
        f"{source_list}\n\n"

        f"== MEMORY (compressed prior-stage sections — already covered, "
        f"don't re-introduce) ==\n"
        f"{format_memory_block(memory)}\n\n"

        f"== OUTPUT — strict JSON (cookbook v2 schema, code-first order) ==\n"
        f"{{\n"
        f'  "heading":  "{section_heading}",  /* ECHO verbatim, no "# " */\n'
        f'  "intro":    "1-2 sentences (20-400 chars) of substance: name the '
        f"section's core APIs and the problem they solve, opening with the subject itself "
        f"(e.g. 'asyncio.Lock serializes access to shared state...'). NEVER open with "
        f"'This section covers/explores/explains/shows' or 'Learn how to'. NO code fences.\",\n"
        f'  "subtopics": [\n'
        f'    {{\n'
        f'      "code_ref_hash": "16-hex hash — PICK THIS FIRST from the '
        f'code bank above; the next two fields describe THIS chosen block",\n'
        f'      "subheading":    "2-10 word phrase NAMING what the chosen '
        f'code block demonstrates (derive from its identifiers/decorators '
        f'/function names — NOT the broader topic you might want to '
        f'cover).",\n'
        f'      "explanation":   "8-80 words describing the chosen block. '
        f'MUST mention ≥1 specific identifier (decorator, function name, '
        f'type, parameter) that is visible in the code body. NO code '
        f'fences inside."\n'
        f'    }},\n'
        f'    ... 3-12 subtopics, aim for {rng_aim} ...\n'
        f'  ],\n'
        f'  "citations": [\n'
        f'    {{"source_key": "ingestion/.../0024-foo.md", '
        f'"claim": "restate the specific fact this source backs"}},\n'
        f'    ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. `heading` MUST be EXACTLY {section_heading!r} (case-sensitive "
        f"   echo). No leading '#' chars.\n"
        f"2. **Per subtopic: PICK code_ref_hash FIRST, then write the "
        f"   subheading + explanation that ground to THAT block's actual "
        f"   identifiers**. Do NOT pick a topic-sounding subheading and "
        f"   then grab a random hash — that produces prose that doesn't "
        f"   describe the code below it (hard fail in the validator).\n"
        f"3. Each subtopic MUST have a UNIQUE code_ref_hash from the bank "
        f"   above. Inventing or paraphrasing a hash is a hard violation.\n"
        f"4. **CODE DENSITY: at least 3 subtopics per section. Aim for "
        f"   4-6** when the bank has ≥6 entries; up to 8 when bank ≥20. "
        f"   The whole point is code-rich learning material.\n"
        f"5. **EXPLANATION GROUNDING (Ship B, validator-enforced)**: the "
        f"   explanation MUST reference ≥1 identifier visible in the chosen "
        f"   code body — a decorator name, function name, type, kwarg, or "
        f"   imported symbol. Generic topic prose with zero code-anchored "
        f"   terms is rejected.\n"
        f"6. **NO EMBELLISHMENT (U3, 2026-05-27)**: describe ONLY what the "
        f"   chosen code body SHOWS or what the section's source digest "
        f"   explicitly says. Do NOT invent: parameter names not in the "
        f"   code, default values not stated, return types not annotated, "
        f"   error classes not raised in the snippet, related APIs not "
        f"   imported, command-line flags not appearing in the block, "
        f"   pricing/quota/SLA facts the source doesn't state. Anti-"
        f"   examples (DO NOT WRITE these unless the code/source contains "
        f"   them verbatim):\n"
        f"     ✗ 'The Browser class provides methods for creating new "
        f"       pages, retrieving all pages, and closing the session' — "
        f"       UNLESS the code shows these three methods.\n"
        f"     ✗ 'The @sandbox decorator accepts a max_steps parameter to "
        f"       limit agent loop iterations' — UNLESS `max_steps = ` "
        f"       appears in the code block.\n"
        f"     ✗ 'Browser Use's pricing is $X per Y' — UNLESS the digest "
        f"       contains pricing facts.\n"
        f"   The atomic-claim grounding judge AND the CoCoA alignment "
        f"   judge BOTH flag embellishment; chapters fail when these run "
        f"   above threshold. Stay strictly inside what's visible. If you "
        f"   want to teach something the code doesn't show, PICK A "
        f"   DIFFERENT HASH that does show it.\n"
        f"7. **SUBHEADING GROUNDING (Ship E, validator-enforced)**: the "
        f"   subheading MUST share ≥1 token with the code body's identifiers "
        f"   OR with words in its first 3 non-blank lines. 'Token Caching "
        f"   to Reduce Verification Overhead' is REJECTED when the code "
        f"   shows `@mcp.tool def write_summary(...)` — those mention "
        f"   nothing about token caching. Pick the hash first, name what "
        f"   it actually demonstrates.\n"
        f"8. EXPLANATIONS ARE TIGHT: 8-80 words. Reference specific lines/"
        f"   decorators/types from the chosen code. NO multi-paragraph "
        f"   summaries.\n"
        f"9. DISTINCT subheadings within the section — no two subtopics "
        f"   can share a subheading or share a code_ref_hash.\n"
        f"9b. **NO BOILERPLATE RECYCLING (DD-SYNTH-SECTION-RECYCLING-"
        f"    2026-05-29)**: do NOT center a subtopic on a generic canonical "
        f"    artifact — a full class/dataclass definition, a complete "
        f"    config-file dump, or import boilerplate — when a more "
        f"    SECTION-SPECIFIC hash is available. Those same blocks get "
        f"    picked by sibling sections and make the chapter repetitive. A "
        f"    render-time pass REMOVES any code block whose body duplicates "
        f"    one already shown earlier in the chapter (replacing it with a "
        f"    cross-reference), so a recycled pick wastes the subtopic. "
        f"    Choose hashes that demonstrate THIS section's distinct angle.\n"
        f"10. Every `citations[*].source_key` MUST be one of the valid "
        f"    source_keys above. Aim for {n_primary_contribs}+ citations.\n"
        f"11. NO inline `<code-ref hash = \"...\"/>` tags anywhere. NO "
        f"    ```code fences``` in `intro` or `explanation`. The renderer "
        f"    materializes code per-subtopic from `code_ref_hash`.\n"
        f"12. NO `# docs:` / `# src:` source-id leaks in prose. Use the "
        f"    typed `citations` field.\n"
        f"13. Don't re-introduce terminology already in `memory[*]"
        f".key_terminology` above — assume the reader saw it.\n"
        f"14. PEDAGOGICAL ORDER: subtopics ordered easiest → most "
        f"    advanced. First subtopic = canonical/minimal example. "
        f"    Subsequent subtopics = primitives / recipes / edge cases.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation."
    )


def summarize_candidate(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> dict:
    """Compact candidate summary for critic picker (~250 tokens). Biases toward structure, not content, per outline_sdp's USC pattern."""
    issues = validate_section_against_inputs(
        draft,
        expected_heading = expected_heading,
        allowed_hashes = allowed_hashes,
        valid_source_keys = valid_source_keys,
        vault_rich = vault_rich,
    )
    n_subtopics = len(draft.subtopics)
    total_expl_words = sum(
        len((s.explanation or "").split()) for s in draft.subtopics
    )
    avg_expl_words = (total_expl_words / n_subtopics) if n_subtopics else 0.0
    intro_chars = len(draft.intro or "")
    structural_score = score_draft_structural(
        draft,
        expected_heading = expected_heading,
        allowed_hashes = allowed_hashes,
        valid_source_keys = valid_source_keys,
        n_primary_contribs = n_primary_contribs,
        vault_rich = vault_rich,
    )
    return {
        "n_subtopics":      n_subtopics,
        "intro_chars":      intro_chars,
        "avg_expl_words":   avg_expl_words,
        "n_citations":      len(draft.citations),
        "heading_match":    (
            draft.heading.strip().casefold()
            == expected_heading.strip().casefold()
        ),
        "structural_score": structural_score,
        "violations":       issues,
    }


def drop_repeated_code_blocks(
    sections: list[schemas.Section], vault_rich: dict | None = None,
) -> tuple[list[schemas.Section], int]:
    """Chapter-level last guard: a verbatim code block is printed ONCE. Among the subtopics that picked the same
    block the one whose prose shares the most identifiers with it keeps it (ties: the earliest); the others lose
    their subtopic while the section keeps SUBTOPICS_MIN of them, else just the block (a prose subtopic). asyncio
    ch-01: gather + to_thread snippet in s3 and s5; ch-03: the queue section printed the Event and Barrier examples
    of the previous section under "Bounded Queues" and "Graceful Shutdown" (overlap 2 vs 10 and 7), so the copies
    that DESCRIBE the block stay. A losing copy whose prose repeats the winner's (token Jaccard >= ECHO_JACCARD) is dropped even
    below the floor, never kept as a prose echo (asyncio ch-04: "Shell subprocess with communicate" restated "Shell Command with
    PIPE Streams" word for word, 0.96); the top-ups refill the section from facts nobody states yet. Returns (sections, n_changed)."""
    occ: dict[str, list[tuple[int, int, int]]] = {}
    for si, sec in enumerate(sections):
        for ti, st in enumerate(sec.subtopics):
            h = st.code_ref_hash
            if not h or getattr(st, "code_source", "verbatim") == "derived":
                continue
            body = _fence_body(vault_rich.get(h)) if vault_rich else ""
            score = len(identifier_overlap(f"{st.subheading} {st.explanation}", body)[0]) if body else 0
            occ.setdefault(h, []).append((si, ti, score))
    lose: dict[int, set[int]] = {}
    echo: dict[int, set[int]] = {}
    for h, os_ in occ.items():
        if len(os_) < 2:
            continue
        keep = max(os_, key = lambda o: (o[2], -o[0], -o[1]))
        kept_prose = sections[keep[0]].subtopics[keep[1]].explanation
        for o in os_:
            if o != keep:
                lose.setdefault(o[0], set()).add(o[1])
                if prose_similarity(sections[o[0]].subtopics[o[1]].explanation, kept_prose) >= params.ECHO_JACCARD:
                    echo.setdefault(o[0], set()).add(o[1])
    out: list[schemas.Section] = []
    n = 0
    for si, sec in enumerate(sections):
        bad = lose.get(si)
        if not bad:
            out.append(sec)
            continue
        budget = len(sec.subtopics) - params.SUBTOPICS_MIN
        echoes = sorted(echo.get(si, ()))[:max(len(sec.subtopics) - 1, 0)]      # a section never loses its last subtopic
        subs = []
        for ti, st in enumerate(sec.subtopics):
            if ti in bad:
                n += 1
                if ti in echoes:
                    continue
                if budget > 0:
                    budget -= 1
                    continue
                st = st.model_copy(update = {"code_ref_hash": "", "code_source": "verbatim", "derived_code": None})
            subs.append(st)
        out.append(sec.model_copy(update = {"subtopics": subs}))
    return out, n


def prose_similarity(a: str, b: str) -> float:
    """Jaccard overlap of the identifier-like word sets of two texts (0 when both are empty)."""
    ta, tb = prose_tokens(a), prose_tokens(b)
    return len(ta & tb) / (len(ta | tb) or 1)


def drop_prose_echoes(sections: list[schemas.Section]) -> tuple[list[schemas.Section], int]:
    """A prose subtopic (no code block) whose explanation repeats another subtopic of the chapter (token Jaccard >=
    PROSE_ECHO_JACCARD, near-verbatim) is dropped; the earlier one, or the one with code, stays. Returns (sections, n_dropped)."""
    seq = [(si, ti, st) for si, sec in enumerate(sections) for ti, st in enumerate(sec.subtopics)]
    toks = [prose_tokens(st.explanation) for _, _, st in seq]
    drop: set[tuple[int, int]] = set()
    left = {si: len(sec.subtopics) for si, sec in enumerate(sections)}
    for i, (si, ti, st) in enumerate(seq):
        if st.code_ref_hash or getattr(st, "code_source", "verbatim") == "derived" or left[si] <= 1:
            continue
        for j, (sj, tj, other) in enumerate(seq):
            if j == i or (sj, tj) in drop:
                continue
            if j > i and not (other.code_ref_hash or getattr(other, "code_source", "verbatim") == "derived"):
                continue
            if len(toks[i] & toks[j]) / (len(toks[i] | toks[j]) or 1) >= params.PROSE_ECHO_JACCARD:
                drop.add((si, ti))
                left[si] -= 1
                break
    if not drop:
        return sections, 0
    out = [
        sec.model_copy(update = {"subtopics": [st for ti, st in enumerate(sec.subtopics) if (si, ti) not in drop]})
        for si, sec in enumerate(sections)
    ]
    return out, len(drop)


def hashes_claimed_elsewhere(per_section_index: dict[str, list[dict]], section_id: str) -> set[str]:
    """Vault hashes routed (after dedupe_vault_hashes_across_sections) to any section other than `section_id`."""
    return {
        h
        for sid, contribs in per_section_index.items() if sid != section_id
        for c in contribs
        for h in (c.get("code_refs") or [])
    }


def dedupe_vault_hashes_across_sections(
    per_section_index: dict[str, list[dict]],
) -> tuple[int, int]:
    """Modify per_section_index in-place so each vault hash appears in at most one section. Winner = strongest relevance, then smallest pool, then sorted section_id. Returns (n_hashes_deduped, n_refs_removed)."""
    # Pass 1: for each (hash, section), find the BEST relevance any
    # contribution in that section asserts for the hash.
    hash_section_best_rel: dict[tuple[str, str], str] = {}
    for sid, contribs in per_section_index.items():
        for c in contribs:
            rel = c.get("relevance") or "tangential"
            for h in (c.get("code_refs") or []):
                key = (h, sid)
                cur = hash_section_best_rel.get(key)
                if cur is None or _RELEVANCE_RANK.get(rel, 9) < _RELEVANCE_RANK.get(cur, 9):
                    hash_section_best_rel[key] = rel

    # Pass 2: group by hash; only hashes claimed by ≥2 distinct sections
    # need deduplication.
    hash_section_options: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (h, sid), rel in hash_section_best_rel.items():
        hash_section_options[h].append((sid, rel))

    # Snapshot pool sizes for tie-breaking (use original sizes; don't
    # behavior non-deterministic across iterations).
    section_pool_sizes: dict[str, int] = {
        sid: sum(len(c.get("code_refs") or []) for c in contribs)
        for sid, contribs in per_section_index.items()
    }

    n_hashes_deduped = 0
    n_refs_removed = 0
    for h, options in hash_section_options.items():
        if len(options) <= 1:
            continue
        n_hashes_deduped += 1
        # Pick: strongest relevance, then smallest pool, then sorted sid
        # (final tiebreak deterministic).
        best_sid = min(options, key=lambda x: (
            _RELEVANCE_RANK.get(x[1], 9),
            section_pool_sizes.get(x[0], 0),
            x[0],
        ))[0]
        for sid, _rel in options:
            if sid == best_sid:
                continue
            for c in per_section_index[sid]:
                refs = c.get("code_refs") or []
                if h in refs:
                    c["code_refs"] = [r for r in refs if r != h]
                    n_refs_removed += 1
    return n_hashes_deduped, n_refs_removed


def placeholder_section(
    *,
    section_id: str,
    heading: str,
    n_repairs: int,
    deployment_writer: Optional[str],
    error_tags: Optional[list[str]] = None,
) -> schemas.Section:
    """Fallback when all writer drafts and repairs fail. Keeps chapter assemblable; empty subtopics triggers checklist density gate → mgsr_replan retargets or merges this section."""
    issues = ["placeholder"]
    if error_tags:
        # "draft_fail:<tag>" per failed attempt — lets sawc_write_run
        # aggregate an error_breakdown the same way digest_construct
        # already does, instead of a total failure being undiagnosable.
        issues.extend(f"draft_fail:{tag}" for tag in error_tags)
    return schemas.Section(
        section_id=section_id,
        heading=heading,
        intro=(
            f"This section ({heading}) is awaiting content. The synth "
            f"writer was unable to produce a valid draft on its initial "
            f"pass; mgsr_replan should retarget this section or merge "
            f"it into an adjacent section in the next iteration."
        ),
        subtopics=[],
        citations=[],
        n_drafts_tried=params.N_DRAFTS,
        n_repairs=n_repairs,
        deployment_writer=deployment_writer,
        issues=issues,
    )


def compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    digest_manifest_hash: str,
    refine_iter: int = 0,
) -> str:
    """Content-addressed cache key for SAWC. refine_iter included so each mgsr→sawc loop iteration gets fresh drafts (without it, cache short-circuits with stale results)."""
    payload = (
        f"outline={outline_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={versions.SAWC_PROMPT_VERSION}|"
        f"schema={versions.SAWC_SCHEMA_VERSION}|"
        f"iter={refine_iter}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


_RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


def parse_json_response(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


_TYPOGRAPHY = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " "})


def normalize_typography(obj):
    """Writer output with the non-breaking hyphen (U+2011) and no-break spaces the model likes ("low‑level", "top‑level") swapped for the
    plain characters: they break heading anchors, text search and identifier matching in the book. Strings in nested dicts / lists only."""
    if isinstance(obj, str):
        return obj.translate(_TYPOGRAPHY)
    if isinstance(obj, dict):
        return {k: normalize_typography(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_typography(v) for v in obj]
    return obj


def trim_intro(intro: str) -> str:
    """An intro over INTRO_CHARS_MAX is cut at its last sentence end that fits (asyncio ch-05: 401 chars cost a writer draft two
    repair round-trips and the draft); text without a usable sentence end is cut at a word."""
    s = " ".join(intro.split())
    if len(s) <= params.INTRO_CHARS_MAX:
        return s
    cut = s[:params.INTRO_CHARS_MAX]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".") if cut.endswith(".") else -1)
    if end + 1 >= params.INTRO_CHARS_MIN:
        return cut[:end + 1]
    cut = cut[:cut.rfind(" ")].rstrip(" ,;:-") if " " in cut else cut
    return cut.rstrip(".") + "."


def trim_lists(raw: dict) -> dict:
    """A draft whose lists run over their caps is trimmed, not rejected (fastmcp ch-01, 133 routed blocks: drafts with 16 citations
    failed the 12-citation validator, the repair round-trips timed out and the section shipped as a placeholder). Citations keep
    their first mention per source; sources the cut drops are re-cited by the coverage / source top-ups that follow. Subtopics keep
    the writer's order."""
    out = dict(raw)
    cits = out.get("citations")
    if isinstance(cits, list) and len(cits) > params.CITATIONS_MAX:
        seen, uniq = set(), []
        for c in cits:
            key = c.get("source_key") if isinstance(c, dict) else None
            if key is not None and key in seen:
                continue
            seen.add(key)
            uniq.append(c)
        out["citations"] = uniq[:params.CITATIONS_MAX]
    subs = out.get("subtopics")
    if isinstance(subs, list) and len(subs) > params.SUBTOPICS_MAX:
        out["subtopics"] = subs[:params.SUBTOPICS_MAX]
    return out


def try_parse_draft(
    raw: dict,
    *,
    expected_heading: Optional[str] = None,
) -> tuple[Optional[schemas.LLMSectionDraft], Optional[str]]:
    """Pydantic-validate a writer draft. `heading` is only an echo of the
    outline's H2, so when `expected_heading` is given the authoritative copy
    replaces whatever the LLM emitted — a writer that trims or rewrites a
    heading (e.g. drops permalink debris, leaving a 1-word name) must not
    sink an otherwise good draft or burn repair round-trips on it."""
    if expected_heading and isinstance(raw, dict):
        raw = {**raw, "heading": expected_heading}
    if isinstance(raw, dict):
        raw = normalize_typography(raw)
    if isinstance(raw, dict) and isinstance(raw.get("intro"), str):
        raw = {**raw, "intro": trim_intro(raw["intro"])}
    if isinstance(raw, dict):
        raw = trim_lists(raw)
    try:
        return schemas.LLMSectionDraft.model_validate(raw), None
    except ValidationError as e:
        return None, shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix

