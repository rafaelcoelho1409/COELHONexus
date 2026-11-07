"""What the model reads of a draft's check."""
from __future__ import annotations
from . import params


def report(issues: list[str]) -> str:
    """The text the model reads: a single line when the draft is valid, else every issue (the first ones, if there are many)."""
    if not issues:
        return "OK: no issues. The draft is valid; answer now."
    shown = issues[:params.ISSUES_SHOWN]
    more = len(issues) - len(shown)
    lines = [f"{len(issues)} issue(s) to fix:"] + [f"- {issue[:params.ISSUE_CHARS]}" for issue in shown]
    if more > 0:
        lines.append(f"- ... and {more} more")
    return "\n".join(lines)
