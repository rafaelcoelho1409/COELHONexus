"""PhaseEnforcerMiddleware — injects before_model SystemMessages to keep the orchestrator
running until every phase has its fs artifact on disk.

Uses before_model (not after_model) so the injected message reaches the LLM's
decision context before its next call — after_model's returned HumanMessage was
silently ignored when the AIMessage was terminal (no tool_calls).
"""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
except ImportError:                                                       # pragma: no cover
    from langchain.agents.middleware.types import AgentMiddleware         # type: ignore

from langchain_core.messages import SystemMessage

from ..keys import (
    FS_DISCOVERY_KEY_ARXIV,
    FS_DISCOVERY_KEY_HF,
    FS_DISCOVERY_KEY_HN,
    FS_DISCOVERY_KEY_S2,
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
)
from ..tools.state import fs_list, fs_read


# All 4 discovery files must be present before discovery is considered complete.
# An empty-list stash is the correct empty-result signal; absence means the subagent didn't run.
_REQUIRED_DISCOVERY_KEYS: tuple[str, ...] = (
    FS_DISCOVERY_KEY_ARXIV,
    FS_DISCOVERY_KEY_S2,
    FS_DISCOVERY_KEY_HF,
    FS_DISCOVERY_KEY_HN,
)


logger = logging.getLogger(__name__)

# 20 nudges: enough headroom for every phase to recover without being a runaway.
# The safety net is _build_digest_from_fs + task.py's inline backfill for any
# remaining missing extractions after the agent exits.
MAX_CORRECTIONS = 20

_SCAN_ID_RE = re.compile(r"scan_id=([0-9a-fA-F-]{32,})")


class PhaseEnforcerMiddleware(AgentMiddleware):
    """Force the orchestrator to complete each phase before terminating."""

    name: str = "rr_phase_enforcer"

    def __init__(self) -> None:
        super().__init__()
        self._corrections_per_thread: dict[str, int] = {}

    @staticmethod
    def _scan_id_from_state(state: dict[str, Any]) -> str | None:
        messages = state.get("messages", []) or []
        for m in messages:
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                continue
            m2 = _SCAN_ID_RE.search(content)
            if m2:
                return m2.group(1)
        return None

    @staticmethod
    def _missing_discovery_sources(scan_id: str) -> list[str]:
        """Return source names whose discovery file is still absent. Empty ⇒ all 4 subagents stashed."""
        missing: list[str] = []
        for key in _REQUIRED_DISCOVERY_KEYS:
            if fs_read(scan_id, key) is None:
                source = key.split("/", 1)[1].rsplit(".", 1)[0]
                missing.append(source)
        return missing

    @staticmethod
    def _missing_deep_read_arxiv_ids(scan_id: str) -> list[str]:
        """Return arxiv_ids from triage's top_n that still lack an extraction file."""
        top_n = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
        if not isinstance(top_n, list) or not top_n:
            return []
        expected_ids = [
            str(p.get("arxiv_id") or "") for p in top_n
            if isinstance(p, dict) and p.get("arxiv_id")
        ]
        if not expected_ids:
            return []
        existing = fs_list(scan_id, prefix="extractions/")
        present_ids = set()
        for path in existing:
            if path.startswith("extractions/") and path.endswith(".json"):
                aid = path[len("extractions/"):-len(".json")]
                if aid:
                    present_ids.add(aid)
        return [aid for aid in expected_ids if aid not in present_ids]

    @classmethod
    def _next_missing_phase(cls, scan_id: str) -> str | None:
        """Return the name of the first incomplete phase, or None when all artifacts exist."""
        # Discovery: ALL 4 source files required (count=0 is OK; absence is not).
        if cls._missing_discovery_sources(scan_id):
            return "discovery"
        if fs_read(scan_id, FS_FILE_TRIAGE_TOPN) is None:
            return "triage"
        # All top_n papers must have extraction files, not just ≥1.
        if cls._missing_deep_read_arxiv_ids(scan_id):
            return "deep_read"
        if fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis"
        return None

    @staticmethod
    def _last_was_terminal(messages: list[Any]) -> bool:
        if not messages:
            return False
        last = messages[-1]
        if type(last).__name__ != "AIMessage":
            return False
        return not getattr(last, "tool_calls", None)

    def before_model(self, state: dict[str, Any], runtime: Any = None) -> dict[str, Any] | None:
        """Inject a high-priority SystemMessage when a phase is incomplete."""
        messages = state.get("messages", []) or []
        if not messages:
            return None

        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None

        n = self._corrections_per_thread.get(scan_id, 0)
        if n >= MAX_CORRECTIONS:
            return None

        missing = self._next_missing_phase(scan_id)
        if missing is None:
            return None

        last = messages[-1]
        last_content = getattr(last, "content", "") or ""
        if (
            type(last).__name__ == "SystemMessage"
            and isinstance(last_content, str)
            and last_content.startswith("[phase-enforcer]")
        ):
            return None

        if missing == "discovery":
            missing_sources = self._missing_discovery_sources(scan_id)
            subagent_map = {
                "arxiv":                     "discovery_arxiv",
                "semantic_scholar":          "discovery_semantic_scholar",
                "huggingface_daily_papers":  "discovery_huggingface_daily_papers",
                "hn":                        "discovery_hn",
            }
            calls = [
                f"task(subagent_type='{subagent_map[s]}', "
                f"description=\"scan_id={scan_id} topic='<topic>' verticals=<list>\")"
                for s in missing_sources if s in subagent_map
            ]
            nudge_body = (
                f"Discovery is INCOMPLETE — the following source files are still "
                f"missing from fs: {missing_sources!r}. The orchestrator MUST "
                f"dispatch all 4 discovery subagents (or all 4 discover_* tools "
                f"in tools mode), not just one. Each subagent's stash_discovery_result "
                f"creates discovery/<source>.json — an empty list is the correct "
                f"empty-result signal but the file MUST exist. Dispatch the missing "
                f"subagents now, IN ONE MESSAGE for parallel execution:\n"
                + "\n".join(calls)
            )
        elif missing == "deep_read":
            missing_ids = self._missing_deep_read_arxiv_ids(scan_id)
            calls = [
                f"task(subagent_type='deep_read', "
                f"description=\"scan_id={scan_id} arxiv_id='{aid}'\")"
                for aid in missing_ids
            ]
            nudge_body = (
                f"Deep_read is INCOMPLETE — the following arxiv_ids from "
                f"fs/triage/top_n.json still lack an extraction file: "
                f"{missing_ids!r}. Dispatch one deep_read task PER missing "
                f"arxiv_id, ALL IN ONE MESSAGE for parallel execution. "
                f"DO NOT skip to synthesis until every top_n paper has an "
                f"extraction on disk (the ScanComplete validator will "
                f"reject a terminal output with missing extractions).\n"
                + "\n".join(calls)
            )
        else:
            nudge_map = {
                "triage":     f"Discovery is done but you have not called triage_candidates(scan_id='{scan_id}', topic='<topic>', profile_verticals=[...], top_n=N). Call it NOW — it is unconditional even if some discoveries returned 0.",
                "synthesis":  f"Deep_read is done. Dispatch task(subagent_type='synthesis', description='scan_id={scan_id}') NOW. After synthesis writes fs/synthesis/report.json you MUST immediately emit respond_in_format with a valid ScanComplete — there is NO report subagent. The digest is assembled in Python after your ScanComplete response.",
            }
            nudge_body = nudge_map[missing]
        nudge = SystemMessage(
            content=f"[phase-enforcer] next_missing={missing}: {nudge_body}"
        )

        self._corrections_per_thread[scan_id] = n + 1
        logger.info(
            f"[phase-enforcer] scan_id={scan_id} next_missing={missing!r} "
            f"injecting BEFORE-model nudge (#{n + 1}/{MAX_CORRECTIONS})"
        )
        return {"messages": [nudge]}
