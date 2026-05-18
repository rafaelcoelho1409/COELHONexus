"""Docs Distiller feature — 3-step wizard.

  Step 1  Catalog    — framework picker (search + chips + tile grid)
  Step 2  Ingestion  — live progress + cancel button + downloaded files
  Step 3  Study      — page grid backed by the persistent MinIO manifest

A library sidebar (Steps 2 + 3) lists every framework already finalized
in MinIO; each row has refresh + delete buttons.

Behavior contracts with the backend (forwarded via the FastHTML proxy):
  POST /runs                      → {status: cached|queued|locked, run_id?, manifest?}
  POST /runs/{id}/cancel          → cooperative cancel
  GET  /runs/{id}                 → live progress + manifest snapshot (Redis)
  GET  /ingestion                 → sidebar data source (every finalized framework)
  GET  /ingestion/{slug}/manifest → canonical manifest from MinIO
  GET  /ingestion/{slug}/pages/{i}→ page body from MinIO

All HTML scaffolding lives here; CSS is in /static/css/app.css and the
client-side wizard logic is in /static/js/docs_distiller.js.
"""
import httpx
from fasthtml.common import (
    Button, Div, Img, Input, Option, P, Script, Select, Span,
)

from proxy import FASTAPI_URL
from shell import _Shell


def _fetch_catalog() -> list[dict]:
    try:
        r = httpx.get(f"{FASTAPI_URL}/api/v1/docs-distiller/resolver", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def _Step(n: int, label: str, active: bool = False):
    cls = "fw-step active" if active else "fw-step"
    return Div(
        Span(str(n), cls="fw-step-circle"),
        Span(label, cls="fw-step-label"),
        cls=cls,
        id=f"fw-step-{n}",
        data_step=str(n),
    )


def _tile(f: dict):
    children = []
    # Multi-logo stack entries (e.g. LangChain - LangGraph - DeepAgents)
    # render every component logo in a horizontal strip. Single-logo
    # entries fall back to the legacy single-image render.
    logos = f.get("logos") or []
    if logos:
        children.append(Div(
            *[Img(src=u, alt="", cls="fw-tile-logo-multi") for u in logos],
            cls="fw-tile-logos",
        ))
    elif f.get("logo"):
        children.append(Img(src=f["logo"], alt="", cls="fw-tile-logo"))
    children.append(Div(f["name"], cls="fw-tile-name"))
    children.append(Div(f.get("category") or "—", cls="fw-tile-cat"))
    return Div(
        *children,
        cls="fw-tile",
        data_name=f["name"],
        data_slug=f["slug"],
        data_category=(f.get("category") or "Other"),
    )


def _Picker():
    catalog = _fetch_catalog()
    if not catalog:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/resolver.",
                cls="fw-empty",
            ),
            cls="fw-picker",
        )

    cats = sorted({(f.get("category") or "Other") for f in catalog})
    chips = [Span("All", cls="fw-chip active", data_chip="All")] + [
        Span(c, cls="fw-chip", data_chip=c) for c in cats
    ]
    tiles = [_tile(f) for f in catalog]

    # Step 1 — catalog picker (always visible, never locked)
    step1_edit = Div(
        Div(
            Input(
                type="search", id="fw-search",
                placeholder=f"Search {len(catalog)} frameworks…",
                autocomplete="off", autofocus=True,
                cls="fw-search",
            ),
            Span("", id="fw-count", cls="fw-count"),
            cls="fw-search-row",
        ),
        Div(*chips, cls="fw-chips"),
        Div(*tiles, cls="fw-grid", id="fw-grid"),
        id="fw-step-1-edit",
    )

    # Step 2 — live progress (visible only during a run) + cached file list
    step2_body = Div(
        Div(
            Span("", id="fw-cache-notice-text", cls="fw-notice-text"),
            id="fw-cache-notice", cls="fw-notice", style="display:none;",
        ),
        Div(
            Span("", id="fw-denied-toast-text", cls="fw-toast-text"),
            Button("✕", id="fw-denied-toast-close", cls="fw-toast-close"),
            id="fw-denied-toast", cls="fw-toast", style="display:none;",
        ),
        # Live progress display — JS hides it when activeRunId is null
        Div(
            Div(
                Span("—", id="fw-progress-tier", cls="fw-progress-tier"),
                Span("idle", id="fw-progress-status", cls="fw-progress-status"),
                cls="fw-progress-head",
            ),
            Div(
                Div(cls="fw-progress-fill", id="fw-progress-fill"),
                cls="fw-progress-bar indeterminate", id="fw-progress-bar",
            ),
            Div(
                Span("", id="fw-progress-counter"),
                Span(""),
                cls="fw-progress-meta",
            ),
            Div("", id="fw-progress-url", cls="fw-progress-url"),
            Div(
                Div(
                    # Logo strip — JS populates with one or more <img>
                    # elements. Supports the unified stack tiles which
                    # carry a `logos: [...]` array (LangChain stack,
                    # Grafana stack) as well as the single-logo case.
                    Div(id="fw-progress-logos", cls="fw-progress-logos"),
                    Span("", id="fw-progress-framework",
                         cls="fw-progress-framework"),
                    cls="fw-progress-framework-box",
                ),
                Button("Cancel ingestion", id="fw-cancel", cls="btn-outline"),
                cls="fw-progress-actions",
            ),
            id="fw-progress-box", cls="fw-progress",
        ),
        # File list — populated from the canonical MinIO manifest whenever
        # the user navigates to Step 2 with an active framework selection.
        Div("", id="fw-step2-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick a framework in the catalog or the sidebar to see "
                "its downloaded files.",
                cls="fw-empty",
            ),
            id="fw-step2-grid", cls="fw-page-grid",
        ),
    )

    # Step 3 — Planner (9 substep cards, populated by JS polling /debug/graph)
    # Each card mirrors one LangGraph node; status fills in as the run advances.
    planner_substeps = [
        ("corpus_load",  "Corpus load",      "Read ingestion's canonical manifest from MinIO."),
        ("embed_corpus", "Embed corpus",     "NIM 8B pass (chunk+mean-pool, L2-norm); vectors cached in MinIO."),
        ("off_topic",    "Off-topic filter", "Pure LLM-as-Judge per page, routed by ParetoBandit (dd-grader cells)."),
        ("cluster",      "Cluster",          "UMAP (10-D) + HDBSCAN (eom) on stored vectors; soft-membership matrix powers LITA refine."),
        ("refine",       "Refine (LITA)",    "Bandit LLM reassigns boundary docs to best-fit cluster via top-5 candidate prompt with c-TF-IDF context."),
        ("label",        "Label",            "KeyLLM-style 2-4 word topic per cluster. Bandit LLM + 3 samples + Universal Self-Consistency vote; round 2 re-labels split-vote clusters with sibling context."),
        ("reduce",       "Reduce (outline)", "Single bandit-LLM merge of labeled clusters → 4-12 chapter outline. N=3 samples + USC vote + self-refine + coverage repair (TnT-LLM pattern)."),
        ("plan_write",   "Plan write",       "Hydrate sources from refine assignments + light sanitization (smart title-case, drop empty chapters) + inline provenance refs. Persists hash-keyed blob + mutable `plan-latest.json` pointer (SLSA/Atlas idiom)."),
    ]
    substep_cards = [
        Div(
            Div(
                Span("○", cls="fw-planner-card-icon", data_status="pending"),
                Div(
                    Div(label, cls="fw-planner-card-title"),
                    Div(desc, cls="fw-planner-card-desc"),
                    cls="fw-planner-card-text",
                ),
                Span("", cls="fw-planner-card-latency"),
                Span("▾", cls="fw-planner-card-chevron"),
                cls="fw-planner-card-head",
            ),
            Div(
                Div(
                    "Output will appear here once the substep runs.",
                    cls="fw-empty",
                ),
                cls="fw-planner-card-body",
            ),
            cls="fw-planner-card",
            data_substep=key,
            data_idx=str(i),
        )
        for i, (key, label, desc) in enumerate(planner_substeps)
    ]
    step3_body = Div(
        # Header w/ Start button + progress meta
        Div(
            Div(
                Div("Planner", cls="fw-planner-title"),
                Div(
                    "Pick a framework, then start to generate the chapter plan.",
                    cls="fw-planner-subtitle", id="fw-planner-subtitle",
                ),
                cls="fw-planner-head-text",
            ),
            Div(
                # Mode dropdown — populated from /planner/info on load.
                # Server-rendered fallback options so the UI is usable even
                # before that fetch completes; JS replaces them with the
                # canonical list (which may add modes in the future).
                Div(
                    Span("Mode", cls="fw-planner-mode-label"),
                    Select(
                        Option("LLM-only", value="llm", selected=True),
                        Option("Classical + LLM (soon)",
                               value="classical", disabled=True),
                        id="fw-planner-mode", cls="fw-planner-mode-select",
                    ),
                    cls="fw-planner-mode-box",
                ),
                Span("", id="fw-planner-progress-label",
                     cls="fw-planner-progress-label"),
                Button("Wipe planner", id="fw-planner-wipe",
                       cls="btn-outline", disabled=True,
                       title=("Delete this framework's planner cache "
                              "(MinIO embeddings + Postgres checkpoints "
                              "+ browser state)")),
                Button("Start Planner", id="fw-planner-start",
                       cls="btn-primary", disabled=True),
                cls="fw-planner-head-actions",
            ),
            cls="fw-planner-head",
        ),
        # Substep timeline
        Div(*substep_cards, id="fw-planner-cards", cls="fw-planner-cards"),
    )

    # Step 4 — Synth (9 substep cards, populated by JS via SSE).
    # Architecture per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`.
    # All cards start in `future` state (⏳); the IMPLEMENTED set comes
    # from GET /synth/info — nodes light up as they ship, mirroring the
    # planner's incremental-rollout pattern. NO node code exists yet;
    # this is UI scaffolding only.
    synth_substeps = [
        ("cache_lookup",      "Cache lookup",
         "Redis 30d keyed by (plan_hash, tone_hash, chapter_id); partial-cache 7d for cascade-timeout resume."),
        ("corpus_normalize",  "Corpus normalize",
         "Strip Mintlify fence-meta + raw-corpus boundaries + orphan tags at INGESTION (replaces deprecated scrubber passes 0-2)."),
        ("outline_sdp",       "Outline (SDP DAG)",
         "Structure-Driven Planner — outline = list of sections w/ dependency DAG; topological stage indexing enables stage-parallel writing (SurveyGen-I 2508.14317)."),
        ("digest_construct",  "Digest construct",
         "Per-source LLM digest → aggregate-merge-consolidate; LLM assigns content to sections w/ reasoning (replaces blind embedding cosine; LLMxMapReduce-V3 2510.10890)."),
        ("vault_sentinelize", "Vault sentinelize",
         "Code blocks → <code-ref hash=...>; LLM never sees/emits code content. VeriCite-style audit (missing/invented/duplicated/orphaned) → bandit signal."),
        ("sawc_write",        "SAWC write",
         "Stage-parallel best-of-N drafts (N=3) via Instructor + Pydantic schema; writer ≠ critic rotator picks for MAMM diversity (2503.15272)."),
        ("checklist_eval",    "Checklist eval",
         "~10 binary criteria (Prometheus-2-style rubric on free-tier model); pass-rate = guided-refinement feedback (RefineBench 2511.22173)."),
        ("mgsr_replan",       "MGSR replan",
         "Memory-Guided Structure Replanner — typed actions {merge|delete|rename|reorder|add} on outline DAG + CoRefine confidence-halting (2602.08948). Loops back to SAWC until ≥80% criteria pass OR plateau OR budget exhausted."),
        ("render_audit_write","Render + audit",
         "Jinja render → round-trip code audit → 3 MinIO artifacts (README.md + challenges.md + flashcards.json) + Langfuse OTel span close."),
    ]
    synth_cards = [
        Div(
            Div(
                # Hourglass icon for `future` state; JS swaps to ○/◐/●/✕
                # as the substep's implementation lands + executes.
                Span("⏳", cls="fw-planner-card-icon", data_status="future"),
                Div(
                    Div(label, cls="fw-planner-card-title"),
                    Div(desc, cls="fw-planner-card-desc"),
                    cls="fw-planner-card-text",
                ),
                Span("", cls="fw-planner-card-latency"),
                Span("▾", cls="fw-planner-card-chevron"),
                cls="fw-planner-card-head",
            ),
            Div(
                Div(
                    "Substep not yet implemented — will be wired into the "
                    "graph as its real logic lands.",
                    cls="fw-empty",
                ),
                cls="fw-planner-card-body",
            ),
            # Reuse planner-card CSS classes for visual parity; add `future`
            # so the styles dim the card until the IMPLEMENTED set lights it.
            cls="fw-planner-card future",
            data_substep=key,
            data_idx=str(i),
        )
        for i, (key, label, desc) in enumerate(synth_substeps)
    ]
    step4_body = Div(
        # Header w/ Start Synth button + progress meta — mirrors Planner head.
        Div(
            Div(
                Div("Synth", cls="fw-planner-title"),
                Div("", cls="fw-planner-subtitle", id="fw-synth-subtitle"),
                cls="fw-planner-head-text",
            ),
            Div(
                # Budget knob — per the SOTA doc step 8 (CoRefine halting):
                # max replan iterations per chapter before forcing best-seen
                # commit. Server-rendered defaults; JS may extend later.
                Div(
                    Span("Budget", cls="fw-planner-mode-label"),
                    Select(
                        Option("3 iters (fast)",   value="3"),
                        Option("5 iters (default)", value="5", selected=True),
                        Option("8 iters (quality)", value="8"),
                        id="fw-synth-budget", cls="fw-planner-mode-select",
                    ),
                    cls="fw-planner-mode-box",
                ),
                Span("", id="fw-synth-progress-label",
                     cls="fw-planner-progress-label"),
                Button("Wipe synth", id="fw-synth-wipe",
                       cls="btn-outline", disabled=True,
                       title=("Delete this framework's synth cache "
                              "(MinIO chapter artifacts + Postgres "
                              "checkpoints + browser state)")),
                Button("Start Synth", id="fw-synth-start",
                       cls="btn-primary", disabled=True),
                cls="fw-planner-head-actions",
            ),
            cls="fw-planner-head",
        ),
        # Substep timeline (9 cards, all `future` until nodes ship).
        Div(*synth_cards, id="fw-synth-cards", cls="fw-planner-cards"),
    )

    # Step 5 — page grid (rendered by JS from /ingestion/{slug}/manifest)
    step5_body = Div(
        Div(id="fw-pages-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick an item from the sidebar or generate a new study.",
                cls="fw-empty",
            ),
            id="fw-page-grid", cls="fw-page-grid",
        ),
    )

    return Div(
        # Stepper row — Catalog → Ingestion → Planner → Synth → Study
        Div(
            Div(
                _Step(1, "Catalog", active=True),
                Span(cls="fw-step-connector"),
                _Step(2, "Ingestion"),
                Span(cls="fw-step-connector"),
                _Step(3, "Planner"),
                Span(cls="fw-step-connector"),
                _Step(4, "Synth"),
                Span(cls="fw-step-connector"),
                _Step(5, "Study"),
                cls="fw-stepper",
            ),
            cls="fw-stepper-row",
        ),

        # Layout: sidebar + main step content
        Div(
            # Sidebar (library) — always rendered; JS toggles visual state.
            Div(
                P("Library", cls="fw-sidebar-title"),
                Div(
                    Div("Loading…", cls="fw-sidebar-empty"),
                    id="fw-sidebar-list",
                ),
                id="fw-sidebar", cls="fw-sidebar",
            ),
            # Main panel — holds the 5 step panels
            Div(
                Div(
                    step1_edit,
                    id="fw-step-1-panel", cls="fw-step-panel active",
                ),
                Div(step2_body, id="fw-step-2-panel", cls="fw-step-panel"),
                Div(step3_body, id="fw-step-3-panel", cls="fw-step-panel"),
                Div(step4_body, id="fw-step-4-panel", cls="fw-step-panel"),
                Div(step5_body, id="fw-step-5-panel", cls="fw-step-panel"),
                cls="fw-main",
            ),
            cls="fw-layout",
        ),

        # Sticky bar (Step 1 → Generate)
        Div(
            Span(
                "Selected: ",
                Span("", id="fw-selected-name", cls="fw-selected-name"),
                id="fw-selected-label", cls="fw-selected-label",
            ),
            Button("Start Ingestion", id="fw-generate", cls="btn-primary"),
            id="fw-sticky-bar", cls="fw-sticky-bar",
        ),
        # Generic confirm modal (reused by delete + future destructive actions)
        Div(
            Div(
                Div("", id="fw-modal-title", cls="fw-modal-title"),
                P("", id="fw-modal-message", cls="fw-modal-message"),
                Div(
                    Button("Cancel", id="fw-modal-cancel", cls="btn-outline"),
                    Button("Confirm", id="fw-modal-confirm", cls="btn-primary"),
                    cls="fw-modal-actions",
                ),
                cls="fw-modal",
            ),
            id="fw-modal", cls="fw-modal-backdrop",
        ),
        # File-content drawer (right-anchored slide-out). One instance; the
        # JS pages it through the current manifest's entries via prev/next.
        Div(
            Div(
                Div(
                    Div("", id="fw-drawer-name", cls="fw-drawer-name"),
                    Div("", id="fw-drawer-meta", cls="fw-drawer-meta"),
                    cls="fw-drawer-title",
                ),
                Div(
                    Button("◀", id="fw-drawer-prev",
                           cls="fw-drawer-btn", title="Previous (←)"),
                    Button("▶", id="fw-drawer-next",
                           cls="fw-drawer-btn", title="Next (→)"),
                    Button("✕", id="fw-drawer-close",
                           cls="fw-drawer-btn", title="Close (Esc)"),
                    cls="fw-drawer-controls",
                ),
                cls="fw-drawer-header",
            ),
            Div("", id="fw-drawer-body", cls="fw-drawer-body"),
            id="fw-drawer", cls="fw-drawer",
        ),
        Script(src="/static/js/docs_distiller.js"),
        cls="fw-picker",
    )


def register(rt) -> None:
    """Attach /docs-distiller to `rt`. The / route lives in features/home.py."""
    @rt("/docs-distiller")
    def docs_distiller():
        return _Shell("docs-distiller", "Docs Distiller", body=_Picker())
