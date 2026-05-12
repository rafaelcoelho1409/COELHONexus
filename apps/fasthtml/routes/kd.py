"""
Knowledge Distiller routes — markdown inspector + MAP A/B compare + studies.

Route map:
  GET  /kd/inspect             → KDInspectPage() shell (HTMX-driven 3-pane)
  *    /api/kd/inspect/<rest>  → reverse-proxy → FastAPI /api/v1/knowledge/inspect/<rest>
  GET  /kd/map-compare         → MapComparePage() shell (form + result swap)
  POST /kd/map-compare/run     → calls FastAPI /api/v1/knowledge/debug/map_compare
  GET  /kd/studies             → KDStudiesListPage() — table of studies (10s refresh)
  GET  /kd/studies/{id}        → KDStudyDetailPage(id) — chapters viewer
  GET  /api/kd/studies/list_fragment              → HTMX fragment for studies table
  GET  /api/kd/studies/{id}/header                → HTMX fragment for study header
  GET  /api/kd/studies/{id}/chapters_list         → HTMX fragment for chapter cards
  GET  /api/kd/studies/{id}/chapters/{n}/render   → HTMX fragment for one chapter's content
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
