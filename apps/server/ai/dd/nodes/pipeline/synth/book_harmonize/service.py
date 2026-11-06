"""book_harmonize steps — the nodes of its subgraph: prepare, extract, canonicalize, detect, plan_patches, patch, finalize.

After a study's chapters are rendered, one pass makes them agree with each other. A claim_extractor reads each chapter for its claims and terms; a
term_canonicalizer settles the terms chapters define differently; a drift_detector per chapter audits it against the canonical terms and its siblings' claims,
verifying a conflict in the siblings' text (`/chapters/`) before reporting it; a violation is kept only when it quotes the chapter verbatim and a real sibling
claim or canonical term backs it; each kept one is fixed by a paragraph_patcher editing only the paragraph that holds the quote, and an edit is accepted only when
the chapter keeps its length and every code block byte for byte."""
from __future__ import annotations
import domains
from . import domain, params
from .. import render
from ..... import agents, backends, graphs

import json
import logging
import re
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.context.SynthContext
STEP = "book_harmonize"


def summary(started: float, n_chapters: int, skipped: str | None, **extra) -> dict:
    return {
        "n_chapters": n_chapters, "n_atomic_claims": 0, "n_canonical_terms": 0, "n_chapters_with_issues": 0, "n_chapters_patched": 0,
        "patches": [], "elapsed_ms": int((time.monotonic() - started) * 1000), "skipped": skipped, **extra,
    }


async def prepare(state: graphs.pipeline.synth.book_harmonize.state.HarmonizeState, runtime: Runtime[Context]) -> dict:
    started = time.monotonic()
    chapters = runtime.context.scratch[STEP]["chapters"]
    if len(chapters) < params.MIN_CHAPTERS:
        return {"finished": True, "started": started, "result": summary(started, len(chapters), "less_than_2_chapters")}
    runtime.context.scratch[STEP]["claims"] = {}
    return {"finished": False, "started": started, "chapter_ids": list(chapters)}


async def extract(state: dict, runtime: Runtime[Context]) -> dict:
    """The atomic claims and key terms of one chapter. A chapter whose extraction fails contributes none (it keeps its prose)."""
    context = runtime.context
    chapter_id = state["chapter_id"]
    prose = context.scratch[STEP]["chapters"][chapter_id]["prose"]
    try:
        answer = await agents.service.ask(
            context.agents.claim_extractor(), agents.pipeline.synth.schemas.ClaimsAnswer,
            json.dumps({"max_claims": params.MAX_CLAIMS_PER_CHAPTER, "prose": prose[:params.PROSE_CHARS_FOR_CLAIMS]}, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
    except Exception as error:
        logger.warning(f"[book_harmonize] claim/term extract failed for {chapter_id}: {type(error).__name__}: {error}")
        return {"extracted": [{"chapter_id": chapter_id, "claims": [], "terms": []}]}
    return {"extracted": [{
        "chapter_id": chapter_id,
        "claims": [c for c in answer.claims if isinstance(c, str) and c.strip()][:params.MAX_CLAIMS_PER_CHAPTER],
        "terms": [t.model_dump() for t in answer.terms],
    }]}


async def canonicalize(state: graphs.pipeline.synth.book_harmonize.state.HarmonizeState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[STEP]
    claims_by_id = {e["chapter_id"]: e["claims"] for e in state.get("extracted") or []}
    terms_by_id = {e["chapter_id"]: e["terms"] for e in state.get("extracted") or []}
    data["claims"] = claims_by_id
    total_claims = sum(len(c) for c in claims_by_id.values())
    if total_claims == 0:
        return {"finished": True, "result": summary(state["started"], len(data["chapters"]), "no_claims_extracted")}
    data["n_claims"] = total_claims
    data["canonical_terms"] = []
    if not any(terms_by_id.values()):
        return {"canonical_terms": []}
    lines: list[str] = []
    for chapter_id, terms in terms_by_id.items():
        if not terms:
            continue
        lines.append(f"## Chapter {chapter_id}")
        for term in terms[:params.TERMS_PER_CHAPTER]:
            name = (term.get("name") or "").strip()
            if name:
                lines.append(f"  - {name}: {(term.get('definition') or '').strip()[:200]}")
    try:
        answer = await agents.service.ask(
            context.agents.term_canonicalizer(), agents.pipeline.synth.schemas.CanonicalAnswer,
            json.dumps({"framework": state["framework_name"], "terms": "\n".join(lines)[:params.TERMS_BLOCK_CHARS]}, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
        data["canonical_terms"] = [t.model_dump() for t in answer.canonical_terms]
    except Exception as error:
        logger.warning(f"[book_harmonize] canonicalize failed: {type(error).__name__}: {error}")
    return {"canonical_terms": data["canonical_terms"]}


async def detect(state: dict, runtime: Runtime[Context]) -> dict:
    """Cross-chapter problems of one chapter, as the drift_detector reported them (validated later). A failure assumes none."""
    context = runtime.context
    data = context.scratch[STEP]
    chapter_id = state["chapter_id"]
    files = {f"/{cid}.md": chapter["prose"] for cid, chapter in data["chapters"].items()}
    role = context.agents.drift_detector(backend = backends.files.service.mounted(files, agents.pipeline.synth.params.CHAPTERS_ROUTE))
    request = {
        "framework": data["framework_name"],
        "chapter_id": chapter_id,
        "prose": data["chapters"][chapter_id]["prose"][:params.PROSE_CHARS_FOR_DETECT],
        "canonical_terms": domain.format_canonical_terms(data["canonical_terms"]),
        "sibling_claims": domain.pick_sibling_claims(chapter_id, data["claims"]) or "(no sibling claims available)",
    }
    try:
        answer = await agents.service.ask(
            role, agents.pipeline.synth.schemas.ViolationsAnswer, json.dumps(request, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
    except Exception as error:
        logger.warning(f"[book_harmonize] detect failed for {chapter_id}: {type(error).__name__}: {error}")
        return {"detections": [{"chapter_id": chapter_id, "violations": [], "summary": ""}]}
    return {"detections": [{"chapter_id": chapter_id, "violations": [v.model_dump() for v in answer.violations], "summary": answer.summary}]}


async def plan_patches(state: graphs.pipeline.synth.book_harmonize.state.HarmonizeState, runtime: Runtime[Context]) -> dict:
    """Keep the grounded violations; group them by the paragraph that holds their quote: one edit per paragraph."""
    data = runtime.context.scratch[STEP]
    canonical = state.get("canonical_terms") or []
    data["canonical_terms"] = canonical
    detections = {d["chapter_id"]: d for d in state.get("detections") or []}
    plans: dict[str, dict] = {}
    todo: list[dict] = []
    for chapter_id, chapter in data["chapters"].items():
        detection = detections.get(chapter_id) or {"violations": [], "summary": ""}
        original = render.domain.strip_outer_wrapper_fences(chapter["prose"])
        raw = detection["violations"]
        kept, dropped = domain.validate_violations(
            raw, this_id = chapter_id, this_prose = chapter["prose"], claims_by_id = data["claims"], canonical_terms = canonical,
        )
        if dropped:
            logger.info(f"[book_harmonize] {chapter_id}: dropped {len(dropped)} ungrounded violation(s): {dropped}")
        parts = re.split(r"(\n[ \t]*\n)", original)         # paragraphs at even indexes, separators at odd ones
        paragraphs = parts[0::2]
        by_paragraph: dict[int, list[dict]] = {}
        for violation in kept[:params.MAX_VIOLATIONS_PER_CHAPTER]:
            index = domain.paragraph_for_quote(paragraphs, violation.get("this_chapter_says") or "")
            if index is not None:
                by_paragraph.setdefault(index, []).append(violation)
        plans[chapter_id] = {
            "original": original, "parts": parts, "paragraphs": paragraphs, "violations": kept, "n_raw": len(raw), "dropped": dropped,
            "summary": detection["summary"], "by_paragraph": by_paragraph,
        }
        for index, violations in by_paragraph.items():
            todo.append({"chapter_id": chapter_id, "paragraph": index, "violations": violations})
    data["plans"] = plans
    return {"patch_todo": todo}


async def patch(state: dict, runtime: Runtime[Context]) -> dict:
    """One paragraph, minimally edited; accepted only when its length stays close and it carries no fence."""
    context = runtime.context
    data = context.scratch[STEP]
    task = state["task"]
    chapter_id, index = task["chapter_id"], task["paragraph"]
    paragraph = data["plans"][chapter_id]["paragraphs"][index]
    request = {
        "framework": data["framework_name"],
        "chapter_id": chapter_id,
        "violations": [
            {"kind": v.get("kind", "issue"), "this_paragraph": str(v.get("this_chapter_says") or "")[:200], "should": str(v.get("should_say") or "")[:200]}
            for v in task["violations"]
        ],
        "canonical_terms": domain.format_canonical_terms(data["canonical_terms"]),
        "paragraph": paragraph,
    }
    try:
        answer = await agents.service.ask(
            context.agents.paragraph_patcher(), agents.pipeline.synth.schemas.ParagraphAnswer, json.dumps(request, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
    except Exception as error:
        logger.warning(f"[book_harmonize] patch failed for {chapter_id} paragraph {index}: {type(error).__name__}: {error}")
        return {"edits": [{"chapter_id": chapter_id, "paragraph": index, "text": None}]}
    edited = (answer.paragraph or "").strip()
    return {"edits": [{"chapter_id": chapter_id, "paragraph": index, "text": edited if domain.paragraph_patch_ok(paragraph, edited) else None}]}


async def finalize(state: graphs.pipeline.synth.book_harmonize.state.HarmonizeState, runtime: Runtime[Context]) -> dict:
    data = runtime.context.scratch[STEP]
    slug = state["framework_slug"]
    edits_by_chapter: dict[str, dict[int, str]] = {}
    for edit in state.get("edits") or []:
        if edit["text"]:
            edits_by_chapter.setdefault(edit["chapter_id"], {})[edit["paragraph"]] = edit["text"]
    patches: list[dict] = []
    n_with_issues = 0
    n_patched = 0
    for chapter_id, plan in data["plans"].items():
        if not plan["violations"]:
            patches.append({
                "chapter_id": chapter_id, "n_violations": 0, "n_violations_raw": plan["n_raw"], "dropped": plan["dropped"],
                "patched": False, "new_prose": None, "summary": plan["summary"],
            })
            continue
        n_with_issues += 1
        parts, paragraphs = list(plan["parts"]), plan["paragraphs"]
        changed = {i: text for i, text in edits_by_chapter.get(chapter_id, {}).items() if text != paragraphs[i].strip()}
        patched_prose = None
        if changed:
            for i, text in changed.items():
                parts[2 * i] = text
            patched_prose = "".join(parts)
        ok = bool(patched_prose and len(patched_prose) > params.PATCH_MIN_KEPT_SHARE * len(plan["original"]))
        if ok and not domain.code_blocks_preserved(plan["original"], patched_prose):
            logger.info(f"[book_harmonize] {chapter_id}: patch rejected — it altered or dropped fenced code blocks (vault byte-exactness)")
            ok = False
        patches.append({
            "chapter_id": chapter_id, "n_violations": len(plan["violations"]), "patched": ok, "new_prose": patched_prose if ok else None,
            "summary": plan["summary"], "violations": plan["violations"], "n_violations_raw": plan["n_raw"],
        })
        if ok:
            n_patched += 1
            domains.dd.pipeline.observability.service.record_classical_patch(dim = "cross_chapter_coherence", framework = slug)
    return {"result": {
        "n_chapters": len(data["chapters"]),
        "n_atomic_claims": data.get("n_claims", 0),
        "n_canonical_terms": len(data["canonical_terms"]),
        "n_chapters_with_issues": n_with_issues,
        "n_chapters_patched": n_patched,
        "patches": patches,
        "elapsed_ms": int((time.monotonic() - state["started"]) * 1000),
        "skipped": None,
    }}


# ---- the study's pass around the subgraph: what it reads from and writes to storage -------------------------------------------------------------

async def load_chapters(blobs, slug: str, chapter_ids: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """The study's rendered chapters as `{chapter_id, title, prose}`, plus the ids left out: no README yet, or a render whose audit failed (its prose can be a
    literal unfilled placeholder, and the pass would treat it as an equally trustworthy sibling when extracting claims and patching the others)."""
    storage_keys = domains.dd.pipeline.storage.keys
    chapters: list[dict] = []
    missing: list[str] = []
    audit_failed: list[str] = []
    for chapter_id in chapter_ids:
        try:
            render_blob = json.loads(await blobs.read_text(storage_keys.chapter_render_latest_key(slug, chapter_id)))
            if not (render_blob.get("audit") or {}).get("audit_passed", True):
                audit_failed.append(chapter_id)
                continue
        except Exception:
            pass   # no render stats yet: fall through to the README check
        try:
            readme = await blobs.read_bytes(storage_keys.chapter_readme_key(slug, chapter_id))
        except Exception:
            missing.append(chapter_id)
            continue
        chapters.append({"chapter_id": chapter_id, "title": chapter_id, "prose": readme.decode("utf-8", errors = "replace")})
    return chapters, missing, audit_failed


async def read_cached(blobs, slug: str, manifest_hash: str) -> dict | None:
    """The stored result of a pass over these exact chapters (content-addressed), or None."""
    cache_key = domains.dd.pipeline.storage.keys.book_harmonize_versioned_key(slug, manifest_hash)
    if not await blobs.exists(cache_key):
        return None
    try:
        cached = json.loads((await blobs.read_bytes(cache_key)).decode("utf-8"))
    except Exception as error:
        logger.warning(f"[book_harmonize] cache read failed at {cache_key!r}: {type(error).__name__}: {error} — recomputing")
        return None
    cached["cache_hit"] = True
    cached["manifest_hash"] = manifest_hash
    return cached


async def overwrite_chapters(blobs, slug: str, result: dict) -> int:
    """Write each validated patch over its chapter's README; returns how many were written."""
    n_overwritten = 0
    for patched in result.get("patches") or []:
        if not patched.get("patched"):
            continue
        chapter_id, new_prose = patched.get("chapter_id"), patched.get("new_prose")
        if not chapter_id or not new_prose:
            continue
        try:
            await blobs.write(
                domains.dd.pipeline.storage.keys.chapter_readme_key(slug, chapter_id),
                render.domain.strip_outer_wrapper_fences(new_prose).rstrip() + "\n",
                content_type = "text/markdown",
            )
            n_overwritten += 1
        except Exception as error:
            logger.warning(f"[book_harmonize] {slug}/{chapter_id}: overwrite failed ({type(error).__name__}: {error})")
    return n_overwritten


async def store_result(blobs, slug: str, manifest_hash: str, payload: dict) -> None:
    """Keep the pass's result under its content hash and as the slug's latest (best-effort: logged, never raised)."""
    storage_keys = domains.dd.pipeline.storage.keys
    try:
        body = json.dumps(payload, ensure_ascii = False).encode("utf-8")
        await blobs.write(storage_keys.book_harmonize_versioned_key(slug, manifest_hash), body, content_type = "application/json")
        await blobs.write(storage_keys.book_harmonize_latest_key(slug), body, content_type = "application/json")
    except Exception as error:
        logger.warning(f"[book_harmonize] {slug}: cache write failed ({type(error).__name__}: {error})")
