"""Redis key builders for the pipeline's run machinery (planner and synth); the strings are stored state, so they keep the stage name.

A thread's keys follow its stage, which its id says: `docs-distiller/{slug}/{uuid}` is a planner run, `docs-distiller/synth/{slug}/{uuid}` a synth chapter and
`docs-distiller/study/{slug}/{uuid}` a synth study."""
from __future__ import annotations


SYNTH_THREAD_PREFIXES = ("docs-distiller/synth/", "docs-distiller/study/")
_SNAPSHOT_SUFFIX = ":events:snapshot"


def stage_of(thread_id: str) -> str:
    """'synth' for a chapter or study thread, else 'planner'."""
    return "synth" if (thread_id or "").startswith(SYNTH_THREAD_PREFIXES) else "planner"


def _thread_state(thread_id: str) -> str:
    return f"coelhonexus:dd:{stage_of(thread_id)}:{thread_id}"


def cancel_key(thread_id: str) -> str:
    return f"{_thread_state(thread_id)}:cancel"


def event_channel(thread_id: str) -> str:
    return f"{_thread_state(thread_id)}:events"


def snapshot_key(thread_id: str) -> str:
    return f"{_thread_state(thread_id)}{_SNAPSHOT_SUFFIX}"


# ---- planner ------------------------------------------------------------------------------------------------------------------------------

def lock_key(slug: str) -> str:
    return f"coelhonexus:dd:planner:lock:{slug}"


def lock_prefix() -> str:
    """Scan-match prefix for `lock_key` — must stay an exact prefix of it, or active-run scans silently miss (no error, just empty)."""
    return "coelhonexus:dd:planner:lock:"


def active_run_key(slug: str) -> str:
    return f"coelhonexus:dd:planner:current:{slug}"


# ---- synth --------------------------------------------------------------------------------------------------------------------------------

def synth_snapshot_pattern(thread_prefix: str) -> str:
    """Scan-match for the snapshot keys of every synth thread under `thread_prefix` (e.g. `docs-distiller/synth/<slug>/`)."""
    return snapshot_key(f"{thread_prefix}*")


def thread_id_from_snapshot_key(key: str) -> str:
    """Inverse of `snapshot_key` for a synth thread."""
    head = "coelhonexus:dd:synth:"
    return key[len(head):-len(_SNAPSHOT_SUFFIX)]


def synth_thread_state_pattern(thread_prefix: str) -> str:
    """Scan-match for EVERY redis key (cancel flag, event state, snapshot) of the synth threads under `thread_prefix`."""
    return f"coelhonexus:dd:synth:{thread_prefix}*"


def synth_lock_key(slug: str) -> str:
    return f"coelhonexus:dd:synth:lock:{slug}"


def synth_lock_prefix() -> str:
    """Scan-match prefix for `synth_lock_key` — must stay an exact prefix of it."""
    return "coelhonexus:dd:synth:lock:"


def active_study_key(slug: str) -> str:
    return f"coelhonexus:dd:study:current:{slug}"


def active_chapters_key(study_thread_id: str) -> str:
    return f"coelhonexus:dd:study:{study_thread_id}:active_chapters"
