"""What the model reads of an outline's score."""
from __future__ import annotations
from . import params

import collections.abc


def report(scored: dict, chapters: collections.abc.Sequence[dict], labels: collections.abc.Sequence[str], target: int) -> str:
    """The score of one outline as the text the model reads: the three numbers, then the pages that match no chapter and the chapter sizes."""
    lines = [
        f"score {scored['score']} | {scored['n']} chapters (target {target}) | largest chapter "
        f"{scored['largest_share_pct']}% of the pages | {len(scored['unmatched'])} pages match no chapter ({scored['unmatched_pct']}%)",
    ]
    if scored["unmatched"]:
        shown = [labels[i] for i in scored["unmatched"][:params.UNMATCHED_LISTED]]
        more = len(scored["unmatched"]) - len(shown)
        lines.append("pages matching no chapter: " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else ""))
    sizes = ", ".join(f"{i + 1}. {chapter['title']}: {n}" for i, (chapter, n) in enumerate(zip(chapters, scored["chapter_sizes"])))
    lines.append("pages matched per chapter: " + sizes)
    return "\n".join(lines)
