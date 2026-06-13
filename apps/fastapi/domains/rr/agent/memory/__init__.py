"""Reusable Markdown memory bundles for the RR orchestrator (DeepAgents §9.3).

Memory files persist ACROSS scans (vs the per-scan virtual filesystem in
tools/state.py). The orchestrator reads them at scan start so it can:

- Tilt ranking by `operator_profile.md`'s verticals + weight overrides
- Flag genuinely NEW themes via `themes_seen.md` instead of re-discovering
  them every scan

These complement `create_deep_agent(memory=[...])` once that API stabilizes.
Today the orchestrator's prompt template substitutes the .md content
inline at agent-build time.
"""
from pathlib import Path


_MEMORY_DIR = Path(__file__).parent


def load_memory(name: str) -> str:
    """Return content of `<name>.md` from this directory. Returns empty
    string if missing (memory files are append-only over time; a missing
    file just means 'no history yet')."""
    path = _MEMORY_DIR / f"{name}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


# Module-level loads. Operator profile is REQUIRED (user-edited);
# themes_seen is OPTIONAL (synthesis appends to it).
MEMORY_OPERATOR_PROFILE = load_memory("operator_profile")
MEMORY_THEMES_SEEN      = load_memory("themes_seen")


__all__ = [
    "MEMORY_OPERATOR_PROFILE",
    "MEMORY_THEMES_SEEN",
    "load_memory",
]
