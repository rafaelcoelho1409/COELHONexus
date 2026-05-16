"""
Knowledge Distiller routes — markdown inspector + MAP A/B compare + studies.

Route map:
  GET  /kd/inspect             → KDInspectPage() shell (HTMX-driven 3-pane)
  *    /api/kd/inspect/<rest>  → reverse-proxy → FastAPI /api/v1/knowledge/inspect/<rest>
  GET  /kd/map-compare         → MapComparePage() shell (form + result swap)
  POST /kd/map-compare/run     → calls FastAPI /api/v1/knowledge/debug/map_compare
  GET  /kd/studies             → KDStudiesListPage() — table of studies (10s refresh)
  GET  /kd/studies/{id}        → KDStudyDetailPage(id) — chapters viewer
  GET  /kd/studies/{id}/observability/ingestion → IngestionObservabilityPage
  GET  /api/kd/studies/list_fragment              → HTMX fragment for studies table
  GET  /api/kd/studies/{id}/header                → HTMX fragment for study header
  GET  /api/kd/studies/{id}/chapters_list         → HTMX fragment for chapter cards
  GET  /api/kd/studies/{id}/chapters/{n}/render   → HTMX fragment for one chapter's content
  GET  /api/kd/studies/{id}/observability/ingestion/fragment → polled every 2s
"""
import httpx
from fasthtml.common import APIRouter, Div, I, Span
from starlette.requests import Request

from components.kd_inspect import KDInspectPage
from components.map_compare import MapComparePage, MapCompareResult
from components.kd_studies import (
    KDStudiesListPage,
    KDStudiesTableFragment,
    KDStudyDetailPage,
    StudyHeaderFragment,
    ChaptersListFragment,
    ChapterContentFragment,
    ChapterErrorFragment,
)
from components.kd_observability import (
    IngestionObservabilityPage,
    IngestionObservabilityFragment,
    PlannerObservabilityPage,
    PlannerObservabilityFragment,
)
from services.fastapi_client import _get_client, reverse_proxy


ar = APIRouter()


# -----------------------------------------------------------------------------
# /kd/inspect — markdown inspector
# -----------------------------------------------------------------------------
@ar("/kd/inspect")
async def kd_inspect_page():
    """Render the 3-pane inspector shell. HTMX hydrates content from FastAPI."""
    return KDInspectPage()


# Reverse-proxy /api/kd/inspect/<rest> → /api/v1/knowledge/inspect/<rest>.
@ar("/api/kd/inspect/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def kd_inspect_proxy(request: Request, rest: str):
    """Forward every method/body/query-string to FastAPI's inspect router."""
    upstream = f"/api/v1/knowledge/inspect/{rest}"
    return await reverse_proxy(request, upstream)


# -----------------------------------------------------------------------------
# /kd/map-compare — MAP A/B comparison UI
# -----------------------------------------------------------------------------
@ar("/kd/map-compare")
async def kd_map_compare_page():
    """Render the form + empty result area; HTMX populates results on submit."""
    return MapComparePage()


@ar("/kd/map-compare/run", methods=["POST"])
async def kd_map_compare_run(request: Request):
    """
    HTMX form-submit target. Reads form fields, calls FastAPI's JSON
    /api/v1/knowledge/debug/map_compare, renders the result via
    MapCompareResult component. The classical_only checkbox keeps wall
    time under ~30s by skipping the LLM-rotator path.

    Returns a Div HTML fragment that swaps into #map-compare-result.
    """
    form = await request.form()

    # Build query params for the upstream FastAPI debug endpoint.
    # Form checkboxes only POST when checked → presence-as-truth.
    params: dict[str, str] = {
        "study_root": form.get("study_root", "").strip(),
        "framework":  form.get("framework", "").strip(),
        "shard_size": form.get("shard_size", "40"),
    }
    max_shards = (form.get("max_shards") or "").strip()
    if max_shards:
        params["max_shards"] = max_shards
    if form.get("skip_off_topic_filter"):
        params["skip_off_topic_filter"] = "true"
    if form.get("classical_only"):
        params["classical_only"] = "true"

    # The FastAPI debug endpoint can take 30s-5min depending on flags;
    # reuse the shared httpx client but override the timeout for this call.
    client = _get_client()
    try:
        r = await client.get(
            "/api/v1/knowledge/debug/map_compare",
            params=params,
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
        )
        if r.status_code != 200:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span(f"FastAPI returned HTTP {r.status_code}: "
                     f"{r.text[:400]}",
                     cls="text-xs font-mono break-all"),
                role="alert",
                cls="alert alert-error text-xs p-3 gap-2",
            )
        return MapCompareResult(r.json())
    except Exception as e:
        return Div(
            I(data_lucide="alert-triangle", cls="w-4 h-4"),
            Span(f"Request failed: {type(e).__name__}: {str(e)[:400]}",
                 cls="text-xs font-mono break-all"),
            role="alert",
            cls="alert alert-error text-xs p-3 gap-2",
        )


# -----------------------------------------------------------------------------
# /kd/studies — live registry viewer + per-study chapter detail
# -----------------------------------------------------------------------------
@ar("/kd/studies")
async def kd_studies_list_page():
    """List page shell — HTMX hydrates #kd-studies-table every 10s."""
    return KDStudiesListPage()


@ar("/kd/studies/{study_id}")
async def kd_study_detail_page(study_id: str):
    """Detail page shell — header + chapter cards lazy-load via HTMX."""
    return KDStudyDetailPage(study_id)


@ar("/api/kd/studies/list_fragment")
async def kd_studies_list_fragment():
    """HTMX fragment: studies table body, refreshed every 10s."""
    client = _get_client()
    try:
        r = await client.get("/api/v1/knowledge/studies", params={"limit": "50"})
        if r.status_code != 200:
            return Div(
                f"FastAPI HTTP {r.status_code}: {r.text[:200]}",
                cls="text-sm text-error p-4",
            )
        data = r.json()
        return KDStudiesTableFragment(
            studies=data.get("studies", []),
            total=data.get("total", 0),
        )
    except Exception as e:
        return Div(
            f"Fetch failed: {type(e).__name__}: {str(e)[:200]}",
            cls="text-sm text-error p-4",
        )


@ar("/api/kd/studies/{study_id}/header")
async def kd_study_header_fragment(study_id: str):
    """HTMX fragment: study header card."""
    client = _get_client()
    try:
        r = await client.get(f"/api/v1/knowledge/studies/{study_id}")
        if r.status_code != 200:
            return Div(
                f"Study not found or HTTP {r.status_code}",
                cls="text-sm text-error p-4 bg-base-100 border border-base-300 rounded-lg",
            )
        return StudyHeaderFragment(r.json())
    except Exception as e:
        return Div(
            f"Fetch failed: {type(e).__name__}: {str(e)[:200]}",
            cls="text-sm text-error p-4",
        )


@ar("/api/kd/studies/{study_id}/chapters_list")
async def kd_study_chapters_list_fragment(study_id: str):
    """HTMX fragment: chapter cards. Combines study record + MinIO tree."""
    client = _get_client()
    try:
        study_resp = await client.get(f"/api/v1/knowledge/studies/{study_id}")
        if study_resp.status_code != 200:
            return Div(
                f"Study unavailable: HTTP {study_resp.status_code}",
                cls="text-sm text-error p-4",
            )
        study_data = study_resp.json()

        tree_resp = await client.get(f"/api/v1/knowledge/studies/{study_id}/tree")
        tree = tree_resp.json() if tree_resp.status_code == 200 else {}

        return ChaptersListFragment(study_id, study_data, tree)
    except Exception as e:
        return Div(
            f"Fetch failed: {type(e).__name__}: {str(e)[:200]}",
            cls="text-sm text-error p-4",
        )


@ar("/api/kd/studies/{study_id}/chapters/{n:int}/render")
async def kd_study_chapter_render_fragment(study_id: str, n: int):
    """HTMX fragment: one chapter's rendered content (README + challenges + flashcards)."""
    client = _get_client()
    try:
        r = await client.get(
            f"/api/v1/knowledge/studies/{study_id}/chapters/{n}",
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        )
        if r.status_code == 404:
            return ChapterErrorFragment("Chapter not ready yet (synth in progress).")
        if r.status_code != 200:
            return ChapterErrorFragment(f"FastAPI HTTP {r.status_code}")
        return ChapterContentFragment(r.json())
    except Exception as e:
        return ChapterErrorFragment(f"{type(e).__name__}: {str(e)[:200]}")


# -----------------------------------------------------------------------------
# /kd/studies/{id}/observability/ingestion — Stage 1 of the KD observability
# stack (companion: docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md). Real-time
# per-URL view of the resolver + ingestion sub-steps so we stop debugging
# 2h Celery studies as black boxes.
# -----------------------------------------------------------------------------
@ar("/kd/studies/{study_id}/observability/ingestion")
async def kd_study_observability_ingestion_page(study_id: str):
    """Shell page — HTMX fragment polls every 2s for fresh data."""
    return IngestionObservabilityPage(study_id)


@ar("/kd/studies/{study_id}/observability/planner")
async def kd_study_observability_planner_page(study_id: str):
    """Stage 2 shell — Planner observability. HTMX fragment polls every 2s."""
    return PlannerObservabilityPage(study_id)


@ar("/api/kd/studies/{study_id}/observability/planner/replay/{shard_idx:int}", methods=["POST"])
async def kd_study_observability_planner_replay(study_id: str, shard_idx: int):
    """
    Pass-through to FastAPI's replay endpoint. Returns a small HTML fragment
    showing the new ShardLabels for an inline diff against the original.
    """
    from fasthtml.common import Code, Pre, P, H4
    client = _get_client()
    try:
        r = await client.post(
            f"/api/v1/knowledge/studies/{study_id}/observability/planner/replay/{shard_idx}",
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0),
        )
        if r.status_code != 200:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span(f"Replay failed: HTTP {r.status_code} — {r.text[:300]}",
                     cls="text-xs font-mono break-all"),
                cls="alert alert-error text-xs p-3 gap-2",
            )
        data = r.json()
        orig = data.get("original", {}) or {}
        rep = data.get("replay", {}) or {}
        orig_clusters = orig.get("clusters") or []
        rep_clusters = rep.get("clusters") or []
        return Div(
            H4(f"Shard #{shard_idx} replay",
               cls="text-sm font-semibold mb-2"),
            Div(
                Div(
                    Span("Original",
                         cls="text-xs font-semibold opacity-70 block mb-1"),
                    Span(f"{len(orig_clusters)} clusters · path={orig.get('path')}",
                         cls="text-xs block mb-1"),
                    *[
                        Div(
                            Span(c.get("name", ""), cls="text-xs font-mono font-semibold"),
                            Span(f" — {c.get('n_files', 0)} files",
                                 cls="text-xs opacity-60"),
                            cls="mb-1",
                        )
                        for c in orig_clusters
                    ],
                    cls="flex-1 p-3 bg-base-200 rounded",
                ),
                Div(
                    Span("Replay (now)",
                         cls="text-xs font-semibold opacity-70 block mb-1"),
                    Span(f"{len(rep_clusters)} clusters",
                         cls="text-xs block mb-1"),
                    *[
                        Div(
                            Span(c.get("name", ""), cls="text-xs font-mono font-semibold"),
                            Span(f" — {c.get('n_files', 0)} files",
                                 cls="text-xs opacity-60"),
                            cls="mb-1",
                        )
                        for c in rep_clusters
                    ],
                    cls="flex-1 p-3 bg-info/10 rounded",
                ),
                cls="grid grid-cols-1 md:grid-cols-2 gap-3",
            ),
            cls="mt-2",
        )
    except Exception as e:
        return Div(
            I(data_lucide="alert-triangle", cls="w-4 h-4"),
            Span(f"{type(e).__name__}: {str(e)[:300]}",
                 cls="text-xs font-mono break-all"),
            cls="alert alert-error text-xs p-3 gap-2",
        )


@ar("/api/kd/studies/{study_id}/observability/planner/fragment")
async def kd_study_observability_planner_fragment(study_id: str):
    """
    HTMX fragment for Planner page. Reads FastAPI's
    GET /studies/{id}/observability/planner snapshot, renders one card
    per sub-step (corpus_load, off_topic, dedup, cache, shards+MAP,
    REDUCE, chapter_coherence, validation+coverage).
    """
    client = _get_client()
    try:
        r = await client.get(
            f"/api/v1/knowledge/studies/{study_id}/observability/planner",
        )
        if r.status_code == 404:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span("Study not found", cls="text-xs"),
                cls="alert alert-warning text-xs p-3 gap-2",
            )
        if r.status_code != 200:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span(f"FastAPI HTTP {r.status_code}: {r.text[:300]}",
                     cls="text-xs font-mono break-all"),
                cls="alert alert-error text-xs p-3 gap-2",
            )
        return PlannerObservabilityFragment(r.json())
    except Exception as e:
        return Div(
            I(data_lucide="alert-triangle", cls="w-4 h-4"),
            Span(f"{type(e).__name__}: {str(e)[:300]}",
                 cls="text-xs font-mono break-all"),
            cls="alert alert-error text-xs p-3 gap-2",
        )


@ar("/api/kd/studies/{study_id}/observability/ingestion/fragment")
async def kd_study_observability_ingestion_fragment(study_id: str):
    """
    HTMX fragment, polled every 2s. Re-renders header + per-URL table.
    Data source: FastAPI's
    `GET /api/v1/knowledge/studies/{id}/observability/ingestion`, which
    snapshots the throttled per-tier counter (Redis key
    `coelhonexus:knowledge:ingest_progress:{id}`) plus the per-URL list
    (Redis key `coelhonexus:knowledge:ingest_urls:{id}`).
    """
    client = _get_client()
    try:
        r = await client.get(
            f"/api/v1/knowledge/studies/{study_id}/observability/ingestion",
        )
        if r.status_code == 404:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span("Study not found", cls="text-xs"),
                cls="alert alert-warning text-xs p-3 gap-2",
            )
        if r.status_code != 200:
            return Div(
                I(data_lucide="alert-triangle", cls="w-4 h-4"),
                Span(f"FastAPI HTTP {r.status_code}: {r.text[:300]}",
                     cls="text-xs font-mono break-all"),
                cls="alert alert-error text-xs p-3 gap-2",
            )
        return IngestionObservabilityFragment(r.json())
    except Exception as e:
        return Div(
            I(data_lucide="alert-triangle", cls="w-4 h-4"),
            Span(f"{type(e).__name__}: {str(e)[:300]}",
                 cls="text-xs font-mono break-all"),
            cls="alert alert-error text-xs p-3 gap-2",
        )
