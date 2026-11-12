from pathlib import Path


# What the architect reads to decide when to hand work over; the critic itself sees SYSTEM_PROMPT.
DESCRIPTION = (
    "Reviews a draft chapter outline in a fresh context through ONE lens and replies with ACCEPT or REVISE and at most 12 lines "
    "of confirmed defects. Send TWO calls in one message for the same outline, one with the lens 'coverage' and one with the "
    "lens 'structure'. Include the whole outline (every chapter's title, description and key concepts) in the text: it sees only "
    "that text and the files under /work/ and /corpus/. It changes nothing."
)
SYSTEM_PROMPT = (Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
