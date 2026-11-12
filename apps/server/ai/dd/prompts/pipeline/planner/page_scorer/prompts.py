from pathlib import Path


_TEMPLATE = (Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()


def system_prompt(proposals: list[dict]) -> str:
    """The scorer's system prompt for one run: the rubric, then this run's chapters. It is the same for every page of the run,
    so providers that cache a prompt prefix reuse it across the hundreds of page calls."""
    chapters = "\n".join(
        f"[{index}] {proposal.get('title')!r}\n"
        f"    description: {proposal.get('description', '')}\n"
        f"    key_concepts: {', '.join((proposal.get('key_concepts') or [])[:10])}"
        for index, proposal in enumerate(proposals)
    )
    return _TEMPLATE.replace("{chapters}", chapters)
